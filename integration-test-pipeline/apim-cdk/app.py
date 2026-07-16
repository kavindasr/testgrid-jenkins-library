#!/usr/bin/env python3
import os
import re

import aws_cdk as cdk

from stacks.wso2_stack import Wso2Stack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)


def _ctx(key, default):
    value = app.node.try_get_context(key)
    return value if value is not None else default


def _stack_name(raw: str) -> str:
    # CloudFormation stack names must match ^[A-Za-z][A-Za-z0-9-]*$ -- Jenkins
    # job names/build metadata (e.g. "wso2am-mysql-8.0-build42") commonly
    # contain dots/underscores, so sanitize rather than push this rule onto
    # every caller.
    name = re.sub(r"[^A-Za-z0-9-]", "-", raw)
    return name if re.match(r"^[A-Za-z]", name) else f"s-{name}"


Wso2Stack(
    app,
    _stack_name(_ctx("stackName", "Wso2ApimStack")),
    env=env,
    ec2_instance_count=int(_ctx("ec2InstanceCount", 2)),
    db_choices=_ctx("dbChoices", ["MySQL-5.7"]),
    db_name=_ctx("dbName", "WSO2AMDB"),
    db_username=_ctx("dbUsername", "wso2carbon"),
    instance_type=_ctx("instanceType", "t2.large"),
    db_instance_type=_ctx("dbInstanceType", "m5.xlarge"),
    operating_system=_ctx("operatingSystem", "Ubuntu"),
    jdk=_ctx("jdk", "ADOPT_OPEN_JDK8"),
    maven_version=_ctx("mavenVersion", "3.3.9"),
    custom_user_data=_ctx("customUserData", "echo"),
    product=_ctx("product", "wso2am"),
    product_version=_ctx("productVersion", "3.2.0"),
    key_pair_name=app.node.try_get_context("keyPairName"),
)

app.synth()
