import os
import re
from typing import List, Optional

from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from constructs import Construct

from cdk_constructs.database import ProvisionedDatabase

# Matches the source CloudFormation template's WSO2InstanceSecurityGroup
# ingress rules (22, 8140, 9763, 9443, 8086, 10173), plus 8243/8280 (the
# APIM gateway HTTPS/HTTP transport ports, relevant since Product defaults
# to wso2am) added on top -- a strict superset of the original's exposure.
WSO2_APIM_PORTS = (8140, 9443, 8243, 8280, 9763, 8086, 10173)

# Mirrors the source CloudFormation template's `OperatingSystemAMI` mapping.
# "Windows" is intentionally omitted -- it was never a valid entry in the
# source mapping either, so the Windows branch of that template could never
# actually launch. Like the source mapping, these AMI IDs are single-region
# (us-east-1) -- there was never a region dimension to port from.
_AMI_REGION = "us-east-1"
OPERATING_SYSTEM_AMI = {
    "Ubuntu": "ami-091138d0f0d41ff90",
    "CentOS": "ami-0199fa59b56678149",
    "RHEL8": "ami-077a8c9eeb949d9d6",
    "RHEL9": "ami-07aa0998b75b559e0",
    "RHEL10": "ami-03828cfec46dbf930",
    "SUSE": "ami-009fab158dcff70e0",
    "Rocky": "ami-00f8640e522fb2841",
}

_USER_DATA_DIR = os.path.join(os.path.dirname(__file__), "user_data")
_TOKEN_PATTERN = re.compile(
    "|".join(re.escape(t) for t in (
        "__OPERATING_SYSTEM__", "__JDK__", "__MAVEN_VERSION__", "__CUSTOM_USER_DATA__",
        "__PRODUCT__", "__PRODUCT_VERSION__", "__DB_TYPE__", "__DB_VERSION__", "__DB_NAME__",
        "__DB_HOST__", "__DB_PORT__", "__DB_SECRET_ARN__", "__AWS_REGION__",
    ))
)


def render_unix_user_data(
    *,
    operating_system: str,
    jdk: str,
    maven_version: str,
    custom_user_data: str,
    product: str,
    product_version: str,
    db: ProvisionedDatabase,
    aws_region: str,
) -> str:
    with open(os.path.join(_USER_DATA_DIR, "unix_user_data.sh")) as f:
        template = f.read()

    # DB_TYPE reported to the provisioning script collapses the CDB engine
    # variant back to its base family, matching the source template.
    db_type = "oracle-se2" if db.engine_key == "oracle-se2-cdb" else db.engine_key

    replacements = {
        "__OPERATING_SYSTEM__": operating_system,
        "__JDK__": jdk,
        "__MAVEN_VERSION__": maven_version,
        "__CUSTOM_USER_DATA__": custom_user_data,
        "__PRODUCT__": product,
        "__PRODUCT_VERSION__": product_version,
        "__DB_TYPE__": db_type,
        "__DB_VERSION__": db.engine_version,
        "__DB_NAME__": db.db_name,
        "__DB_HOST__": db.instance.db_instance_endpoint_address,
        "__DB_PORT__": db.instance.db_instance_endpoint_port,
        "__DB_SECRET_ARN__": db.instance.secret.secret_full_arn or db.instance.secret.secret_arn,
        "__AWS_REGION__": aws_region,
    }
    # Single-pass substitution: a sequential replace()-per-token loop would
    # let an earlier substitution's value (e.g. custom_user_data) that
    # happens to contain a later token's literal text get replaced again.
    return _TOKEN_PATTERN.sub(lambda m: replacements[m.group(0)], template)


class ComputeConstruct(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        role: iam.IRole,
        key_pair_name: Optional[str] = None,
    ) -> None:
        super().__init__(scope, construct_id)

        self.vpc = vpc
        self.role = role
        self.key_pair = (
            ec2.KeyPair.from_key_pair_name(self, "KeyPair", key_pair_name)
            if key_pair_name
            else None
        )

        self.security_group = ec2.SecurityGroup(
            self,
            "SecurityGroup",
            vpc=vpc,
            description="Security group for WSO2 APIM EC2 instances",
            allow_all_outbound=True,
        )
        self.security_group.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "SSH")
        for port in WSO2_APIM_PORTS:
            self.security_group.add_ingress_rule(
                ec2.Peer.any_ipv4(), ec2.Port.tcp(port), f"WSO2 APIM port {port}"
            )

        self.instances: List[ec2.Instance] = []

    def add_instances(
        self,
        *,
        instance_count: int,
        instance_type: str,
        operating_system: str,
        db: ProvisionedDatabase,
        jdk: str = "ADOPT_OPEN_JDK8",
        maven_version: str = "3.3.9",
        custom_user_data: str = "echo",
        product: str = "wso2am",
        product_version: str = "3.2.0",
    ) -> List[ec2.Instance]:
        region = Stack.of(self).region
        if region != _AMI_REGION:
            raise ValueError(
                f"OPERATING_SYSTEM_AMI only has AMI IDs for {_AMI_REGION!r} "
                f"(deploying to {region!r}); add a region-specific AMI ID "
                f"for {operating_system!r} before deploying elsewhere."
            )
        image = ec2.MachineImage.generic_linux({region: OPERATING_SYSTEM_AMI[operating_system]})
        user_data = ec2.UserData.custom(
            render_unix_user_data(
                operating_system=operating_system,
                jdk=jdk,
                maven_version=maven_version,
                custom_user_data=custom_user_data,
                product=product,
                product_version=product_version,
                db=db,
                aws_region=region,
            )
        )

        instances = [
            ec2.Instance(
                self,
                f"Instance{i}",
                vpc=self.vpc,
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
                instance_type=ec2.InstanceType(instance_type),
                machine_image=image,
                role=self.role,
                security_group=self.security_group,
                key_pair=self.key_pair,
                user_data=user_data,
                block_devices=[
                    ec2.BlockDevice(
                        device_name="/dev/sda1",
                        volume=ec2.BlockDeviceVolume.ebs(50, volume_type=ec2.EbsDeviceVolumeType.GP2),
                    ),
                ],
            )
            for i in range(instance_count)
        ]
        self.instances.extend(instances)
        return instances
