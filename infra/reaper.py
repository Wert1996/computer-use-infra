import aws_cdk as cdk
import aws_cdk.aws_dynamodb as dynamodb
import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_events as events
import aws_cdk.aws_events_targets as targets
import aws_cdk.aws_iam as iam
import aws_cdk.aws_lambda as _lambda
import aws_cdk.aws_lambda_event_sources as event_sources
from constructs import Construct

LAMBDA_DIR = "lambda"


class Reaper(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        jobs_table: dynamodb.ITable,
        cluster: ecs.ICluster,
    ) -> None:
        super().__init__(scope, construct_id)

        reaper_fn = _lambda.Function(
            self, "ReaperFn",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset(f"{LAMBDA_DIR}/reaper"),
            timeout=cdk.Duration.seconds(60),
            environment={
                "JOBS_TABLE": jobs_table.table_name,
                "CLUSTER_ARN": cluster.cluster_arn,
            },
        )
        jobs_table.grant_read_write_data(reaper_fn)
        reaper_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ecs:StopTask"],
                resources=["*"],
                conditions={
                    "ArnEquals": {"ecs:cluster": cluster.cluster_arn},
                },
            )
        )

        reaper_fn.add_event_source(
            event_sources.DynamoEventSource(
                jobs_table,
                starting_position=_lambda.StartingPosition.LATEST,
                batch_size=10,
                filters=[
                    _lambda.FilterCriteria.filter(
                        {"eventName": _lambda.FilterRule.is_equal("REMOVE")}
                    )
                ],
            )
        )

        sweep_fn = _lambda.Function(
            self, "SweepFn",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset(f"{LAMBDA_DIR}/sweep"),
            timeout=cdk.Duration.minutes(2),
            environment={
                "JOBS_TABLE": jobs_table.table_name,
                "CLUSTER_ARN": cluster.cluster_arn,
            },
        )
        jobs_table.grant_read_write_data(sweep_fn)
        sweep_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ecs:StopTask", "ecs:ListTasks", "ecs:DescribeTasks"],
                resources=["*"],
                conditions={
                    "ArnEquals": {"ecs:cluster": cluster.cluster_arn},
                },
            )
        )

        events.Rule(
            self, "SweepSchedule",
            schedule=events.Schedule.rate(cdk.Duration.minutes(10)),
            targets=[targets.LambdaFunction(sweep_fn)],
        )
