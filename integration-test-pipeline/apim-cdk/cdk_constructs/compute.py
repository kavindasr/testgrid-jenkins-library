import os
from typing import List, Optional

from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_rds as rds
from constructs import Construct

WSO2_APIM_PORTS = (9443, 8243, 8280, 9763, 8086, 10173)

# Mirrors the source CloudFormation template's `OperatingSystemAMI` mapping.
# "Windows" is intentionally omitted -- it was never a valid entry in the
# source mapping either, so the Windows branch of that template could never
# actually launch.
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


def render_unix_user_data(
    *,
    operating_system: str,
    jdk: str,
    maven_version: str,
    custom_user_data: str,
    product: str,
    product_version: str,
    db_instance: rds.DatabaseInstance,
    db_engine_key: str,
    db_engine_version: str,
    db_name: str,
    aws_region: str,
) -> str:
    with open(os.path.join(_USER_DATA_DIR, "unix_user_data.sh")) as f:
        template = f.read()

    # DB_TYPE reported to the provisioning script collapses the CDB engine
    # variant back to its base family, matching the source template.
    db_type = "oracle-se2" if db_engine_key == "oracle-se2-cdb" else db_engine_key

    replacements = {
        "__OPERATING_SYSTEM__": operating_system,
        "__JDK__": jdk,
        "__MAVEN_VERSION__": maven_version,
        "__CUSTOM_USER_DATA__": custom_user_data,
        "__PRODUCT__": product,
        "__PRODUCT_VERSION__": product_version,
        "__DB_TYPE__": db_type,
        "__DB_VERSION__": db_engine_version,
        "__DB_NAME__": db_name,
        "__DB_HOST__": db_instance.db_instance_endpoint_address,
        "__DB_PORT__": db_instance.db_instance_endpoint_port,
        "__DB_SECRET_ARN__": db_instance.secret.secret_full_arn or db_instance.secret.secret_arn,
        "__AWS_REGION__": aws_region,
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    return template


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
        db_instance: rds.DatabaseInstance,
        db_engine_key: str,
        db_engine_version: str,
        db_name: str,
        jdk: str = "ADOPT_OPEN_JDK8",
        maven_version: str = "3.3.9",
        custom_user_data: str = "echo",
        product: str = "wso2am",
        product_version: str = "3.2.0",
    ) -> List[ec2.Instance]:
        image = ec2.MachineImage.generic_linux({Stack.of(self).region: OPERATING_SYSTEM_AMI[operating_system]})
        user_data = ec2.UserData.custom(
            render_unix_user_data(
                operating_system=operating_system,
                jdk=jdk,
                maven_version=maven_version,
                custom_user_data=custom_user_data,
                product=product,
                product_version=product_version,
                db_instance=db_instance,
                db_engine_key=db_engine_key,
                db_engine_version=db_engine_version,
                db_name=db_name,
                aws_region=Stack.of(self).region,
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
