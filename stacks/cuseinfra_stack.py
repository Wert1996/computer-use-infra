import aws_cdk as cdk
from constructs import Construct
from infra.networking import Networking
from infra.job_executor import JobExecutor
from infra.job_ingestion import JobIngestion
from infra.observability import Observability
from infra.reaper import Reaper
from infra.authentication import Authentication


class CuseinfraStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        networking = Networking(self, "Networking")

        observability = Observability(self, "Observability")

        authentication = Authentication(self, "Authentication")

        executor = JobExecutor(
            self, "JobExecutor",
            vpc=networking.vpc,
            agent_subnets=networking.agent_subnets,
            agent_security_group=networking.agent_security_group,
            output_bucket=observability.output_bucket,
            log_group=observability.log_group,
        )

        ingestion = JobIngestion(
            self, "JobIngestion",
            jobs_table=executor.jobs_table,
            cluster=executor.cluster,
            task_definition=executor.task_definition,
            container_name=executor.container_name,
            agent_subnets=networking.agent_subnets,
            agent_security_group=networking.agent_security_group,
            output_bucket=observability.output_bucket,
            secrets_prefix=executor.secrets_prefix,
            # Authentication parameters
            jwt_authorizer=authentication.jwt_authorizer,
            user_pool_id=authentication.user_pool_id,
            permissions_table=authentication.permissions_table,
        )

        Reaper(
            self, "Reaper",
            jobs_table=executor.jobs_table,
            cluster=executor.cluster,
        )

        cdk.CfnOutput(self, "ApiUrl", value=ingestion.api_url)
        cdk.CfnOutput(self, "OutputBucket", value=observability.output_bucket.bucket_name)
