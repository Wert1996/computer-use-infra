import aws_cdk as cdk
import aws_cdk.aws_dynamodb as dynamodb
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_iam as iam
import aws_cdk.aws_lambda as _lambda
import aws_cdk.aws_lambda_event_sources as event_sources
import aws_cdk.aws_s3 as s3
import aws_cdk.aws_sqs as sqs
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_integrations
from constructs import Construct

LAMBDA_DIR = "lambda"
MAX_TASK_DURATION = cdk.Duration.minutes(15)


class JobIngestion(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        jobs_table: dynamodb.ITable,
        cluster: ecs.ICluster,
        task_definition: ecs.FargateTaskDefinition,
        container_name: str,
        agent_subnets: ec2.SelectedSubnets,
        agent_security_group: ec2.ISecurityGroup,
        output_bucket: s3.IBucket,
        secrets_prefix: str,
    ) -> None:
        super().__init__(scope, construct_id)

        queues = {}
        for priority in ["high", "medium", "low"]:
            queue = sqs.Queue(
                self, f"Queue-{priority}",
                queue_name=f"jobs-{priority}.fifo",
                fifo=True,
                content_based_deduplication=True,
                visibility_timeout=MAX_TASK_DURATION,
            )
            queues[priority] = queue

        ingest_fn = _lambda.Function(
            self, "IngestFn",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset(f"{LAMBDA_DIR}/ingest"),
            timeout=cdk.Duration.seconds(10),
            environment={
                "JOBS_TABLE": jobs_table.table_name,
                "QUEUE_HIGH_URL": queues["high"].queue_url,
                "QUEUE_MEDIUM_URL": queues["medium"].queue_url,
                "QUEUE_LOW_URL": queues["low"].queue_url,
                "MAX_TASK_DURATION_SECONDS": str(int(MAX_TASK_DURATION.to_seconds())),
            },
        )
        jobs_table.grant_write_data(ingest_fn)
        for q in queues.values():
            q.grant_send_messages(ingest_fn)

        get_job_fn = _lambda.Function(
            self, "GetJobFn",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset(f"{LAMBDA_DIR}/get_job"),
            timeout=cdk.Duration.seconds(10),
            environment={
                "JOBS_TABLE": jobs_table.table_name,
                "OUTPUT_BUCKET": output_bucket.bucket_name,
            },
        )
        jobs_table.grant_read_data(get_job_fn)
        output_bucket.grant_read(get_job_fn, "jobs/*")

        api = apigwv2.HttpApi(
            self, "Api",
            api_name="cuseinfra-api",
        )

        api.add_routes(
            path="/jobs",
            methods=[apigwv2.HttpMethod.POST],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "IngestIntegration", ingest_fn
            ),
        )
        api.add_routes(
            path="/jobs/{id}",
            methods=[apigwv2.HttpMethod.GET],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "GetJobIntegration", get_job_fn
            ),
        )

        self.api_url = api.url or ""

        subnet_ids = [s.subnet_id for s in agent_subnets.subnets]

        worker_fn = _lambda.Function(
            self, "WorkerFn",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset(f"{LAMBDA_DIR}/worker"),
            timeout=MAX_TASK_DURATION,
            environment={
                "JOBS_TABLE": jobs_table.table_name,
                "CLUSTER_ARN": cluster.cluster_arn,
                "TASK_DEFINITION_ARN": task_definition.task_definition_arn,
                "CONTAINER_NAME": container_name,
                "SUBNET_IDS": ",".join(subnet_ids),
                "SECURITY_GROUP_ID": agent_security_group.security_group_id,
                "OUTPUT_BUCKET": output_bucket.bucket_name,
                "SECRETS_PREFIX": secrets_prefix,
                "MAX_CONCURRENT_PER_TENANT": "5",
            },
        )
        jobs_table.grant_read_write_data(worker_fn)
        output_bucket.grant_put(worker_fn, "jobs/*")

        worker_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[task_definition.task_definition_arn],
            )
        )
        worker_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[
                    task_definition.task_role.role_arn,
                    task_definition.execution_role.role_arn,
                ],
            )
        )

        concurrency_map = {"high": 30, "medium": 15, "low": 5}
        for priority, queue in queues.items():
            worker_fn.add_event_source(
                event_sources.SqsEventSource(
                    queue,
                    batch_size=1,
                    max_concurrency=concurrency_map[priority],
                )
            )

        completion_fn = _lambda.Function(
            self, "CompletionFn",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset(f"{LAMBDA_DIR}/completion"),
            timeout=cdk.Duration.seconds(30),
            environment={
                "JOBS_TABLE": jobs_table.table_name,
                "OUTPUT_BUCKET": output_bucket.bucket_name,
            },
        )
        jobs_table.grant_read_write_data(completion_fn)
        output_bucket.grant_read(completion_fn, "jobs/*")

        import aws_cdk.aws_events as events
        import aws_cdk.aws_events_targets as targets

        ecs_rule = events.Rule(
            self, "EcsTaskStoppedRule",
            event_pattern=events.EventPattern(
                source=["aws.ecs"],
                detail_type=["ECS Task State Change"],
                detail={
                    "lastStatus": ["STOPPED"],
                    "clusterArn": [cluster.cluster_arn],
                },
            ),
        )
        ecs_rule.add_target(targets.LambdaFunction(completion_fn))
