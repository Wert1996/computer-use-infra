import aws_cdk as cdk
import aws_cdk.aws_logs as logs
import aws_cdk.aws_s3 as s3
from constructs import Construct


class Observability(Construct):
    def __init__(self, scope: Construct, construct_id: str) -> None:
        super().__init__(scope, construct_id)

        self.output_bucket = s3.Bucket(
            self, "OutputBucket",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            lifecycle_rules=[
                s3.LifecycleRule(expiration=cdk.Duration.days(7)),
            ],
        )

        self.log_group = logs.LogGroup(
            self, "AgentLogGroup",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
