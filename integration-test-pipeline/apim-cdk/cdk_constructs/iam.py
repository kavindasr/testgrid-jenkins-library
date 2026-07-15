from aws_cdk import aws_iam as iam
from constructs import Construct


class IamConstruct(Construct):
    def __init__(self, scope: Construct, construct_id: str) -> None:
        super().__init__(scope, construct_id)

        self.ec2_role = iam.Role(
            self,
            "Ec2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "CloudWatchAgentServerPolicy"
                ),
            ],
        )

        # The source template's user-data fetched these via a hardcoded
        # AWSAccessKeyId/AWSAccessKeySecret parameter pair; the instance role
        # replaces that with scoped read access to the same buckets.
        self.ec2_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[
                    "arn:aws:s3:::wum-for-testgrid/*",
                    "arn:aws:s3:::integration-testgrid-resources/*",
                ],
            )
        )
