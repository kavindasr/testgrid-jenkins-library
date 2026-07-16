import re
from dataclasses import dataclass
from typing import List

from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

DB_PORTS = (1433, 1521, 3306, 5432, 50000)

# Mirrors the source CloudFormation template's `DBEngineMap`: friendly choice
# -> (engine key, engine version). "Db2-11.5" is intentionally omitted --
# aws-cdk-lib's RDS L2 engine catalog has no Db2 support.
DB_ENGINE_MAP = {
    "MySQL-5.7": ("mysql", "5.7"),
    "MySQL-8.0": ("mysql", "8.0"),
    "MySQL-8.4": ("mysql", "8.4.8"),
    "Postgres-12.22": ("postgres", "12.22"),
    "Postgres-15.14": ("postgres", "15.14"),
    "Postgres-16.14": ("postgres", "16.14"),
    "Postgres-17.4": ("postgres", "17.4"),
    "Postgres-17.6": ("postgres", "17.6"),
    "Postgres-18.0": ("postgres", "18.3"),
    "SQLServer-SE-13.00": ("sqlserver-se", "13.00"),
    "SQLServer-SE-14.00": ("sqlserver-se", "14.00"),
    "SQLServer-SE-15.00": ("sqlserver-se", "15.00"),
    "SQLServer-SE-16.00": ("sqlserver-se", "16.00"),
    "Oracle-SE2-19.0": ("oracle-se2", "19.0.0.0.ru-2025-04.rur-2025-04.r1"),
    "Oracle-SE2-21.0": ("oracle-se2-cdb", "21.0.0.0.ru-2024-01.rur-2024-01.r1"),
}

# rds's per-engine EngineVersion.of(full_version, major_version) helpers expect
# a "major" prefix whose number of dot-segments differs by engine family
# (MySQL/MariaDB "8.0" vs Postgres/Oracle "15"/"19" vs SQL Server "15.00").
_ENGINE_FACTORIES = {
    "mysql": (rds.DatabaseInstanceEngine.mysql, rds.MysqlEngineVersion, 2),
    "postgres": (rds.DatabaseInstanceEngine.postgres, rds.PostgresEngineVersion, 1),
    "mariadb": (rds.DatabaseInstanceEngine.maria_db, rds.MariaDbEngineVersion, 2),
    "oracle-se2": (rds.DatabaseInstanceEngine.oracle_se2, rds.OracleEngineVersion, 1),
    "oracle-se2-cdb": (rds.DatabaseInstanceEngine.oracle_se2_cdb, rds.OracleEngineVersion, 1),
    "sqlserver-se": (rds.DatabaseInstanceEngine.sql_server_se, rds.SqlServerEngineVersion, 2),
}


def _build_engine(engine: str, version: str) -> rds.IInstanceEngine:
    factory, version_cls, major_segments = _ENGINE_FACTORIES[engine]
    major = ".".join(version.split(".")[:major_segments])
    return factory(version=version_cls.of(version, major))


def _construct_id(db_choice: str) -> str:
    return "Instance" + re.sub(r"[^A-Za-z0-9]", "", db_choice)


@dataclass
class ProvisionedDatabase:
    db_choice: str
    engine_key: str
    engine_version: str
    db_name: str
    instance: rds.DatabaseInstance


class DatabaseConstruct(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        source_security_group: ec2.ISecurityGroup,
        db_choices: List[str],
        instance_type: str = "t3.medium",
        db_name: str = "WSO2AMDB",
        username: str = "wso2carbon",
        allocated_storage: int = 20,
    ) -> None:
        super().__init__(scope, construct_id)

        if not db_choices:
            raise ValueError("db_choices must contain at least one entry.")

        duplicates = {c for c in db_choices if db_choices.count(c) > 1}
        if duplicates:
            raise ValueError(
                f"db_choices has duplicate entries {sorted(duplicates)}; each "
                "entry becomes its own RDS instance with a name derived from "
                "the choice string, so duplicates would collide on construct ID."
            )

        self.security_group = ec2.SecurityGroup(
            self,
            "SecurityGroup",
            vpc=vpc,
            description="Security group for WSO2 APIM RDS instances",
            allow_all_outbound=False,
        )
        for port in DB_PORTS:
            self.security_group.add_ingress_rule(
                source_security_group, ec2.Port.tcp(port), f"DB port {port} from EC2 instances"
            )

        self.databases: List[ProvisionedDatabase] = []
        for db_choice in db_choices:
            engine_key, engine_version = DB_ENGINE_MAP[db_choice]
            db_engine = _build_engine(engine_key, engine_version)
            # SQL Server's DatabaseInstance doesn't accept an initial DB name.
            supports_initial_db_name = not engine_key.startswith("sqlserver")

            instance = rds.DatabaseInstance(
                self,
                _construct_id(db_choice),
                vpc=vpc,
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
                engine=db_engine,
                instance_type=ec2.InstanceType(instance_type),
                credentials=rds.Credentials.from_generated_secret(username),
                database_name=db_name if supports_initial_db_name else None,
                allocated_storage=allocated_storage,
                security_groups=[self.security_group],
                multi_az=False,
                backup_retention=Duration.days(0),
                delete_automated_backups=True,
                removal_policy=RemovalPolicy.DESTROY,
            )
            self.databases.append(
                ProvisionedDatabase(
                    db_choice=db_choice,
                    engine_key=engine_key,
                    engine_version=engine_version,
                    db_name=db_name if supports_initial_db_name else "",
                    instance=instance,
                )
            )

        self.instances: List[rds.DatabaseInstance] = [db.instance for db in self.databases]
        self.secrets: List[secretsmanager.ISecret] = [
            db.instance.secret for db in self.databases
        ]
