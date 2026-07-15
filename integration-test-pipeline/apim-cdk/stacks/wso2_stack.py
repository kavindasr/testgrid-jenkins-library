from typing import List, Optional

from aws_cdk import CfnOutput, Stack
from constructs import Construct

from cdk_constructs.compute import ComputeConstruct
from cdk_constructs.database import DatabaseConstruct
from cdk_constructs.iam import IamConstruct
from cdk_constructs.network import NetworkConstruct


class Wso2Stack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        ec2_instance_count: int = 2,
        db_choices: Optional[List[str]] = None,
        db_name: str = "WSO2AMDB",
        db_username: str = "wso2carbon",
        instance_type: str = "t2.large",
        db_instance_type: str = "m5.xlarge",
        operating_system: str = "Ubuntu",
        jdk: str = "ADOPT_OPEN_JDK8",
        maven_version: str = "3.3.9",
        custom_user_data: str = "echo",
        product: str = "wso2am",
        product_version: str = "3.2.0",
        key_pair_name: Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        network = NetworkConstruct(self, "Network")
        iam = IamConstruct(self, "Iam")

        # Compute's security group has to exist before Database (whose SG
        # ingress rules reference it), but Compute's instances need the
        # database's endpoint/secret in their user-data -- so instance
        # creation is deferred to add_instances() after Database exists.
        compute = ComputeConstruct(
            self,
            "Compute",
            vpc=network.vpc,
            role=iam.ec2_role,
            key_pair_name=key_pair_name,
        )

        database = DatabaseConstruct(
            self,
            "Database",
            vpc=network.vpc,
            source_security_group=compute.security_group,
            db_choices=db_choices or ["MySQL-5.7"],
            instance_type=db_instance_type,
            db_name=db_name,
            username=db_username,
        )
        for secret in database.secrets:
            secret.grant_read(iam.ec2_role)

        # EC2-to-DB pairing (which instance's user-data targets which
        # database) isn't decided yet -- wired to the first provisioned
        # database as a placeholder so the stack stays deployable.
        primary_db = database.databases[0]
        instances = compute.add_instances(
            instance_count=ec2_instance_count,
            instance_type=instance_type,
            operating_system=operating_system,
            db_instance=primary_db.instance,
            db_engine_key=primary_db.engine_key,
            db_engine_version=primary_db.engine_version,
            db_name=primary_db.db_name,
            jdk=jdk,
            maven_version=maven_version,
            custom_user_data=custom_user_data,
            product=product,
            product_version=product_version,
        )

        # No load balancer, matching the source template -- each instance is
        # reached directly on its own public DNS name, same as the original
        # template's WSO2MgtConsoleURL/WSO2PublicIP outputs.
        for i, instance in enumerate(instances):
            CfnOutput(
                self,
                f"WSO2MgtConsoleURL{i}",
                value=f"https://{instance.instance_public_dns_name}:9443/carbon",
            )
            CfnOutput(self, f"WSO2PublicIP{i}", value=instance.instance_public_ip)

        self.vpc = network.vpc
        self.instances = instances
        self.databases = database.instances
