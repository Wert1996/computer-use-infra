import aws_cdk as cdk
import aws_cdk.aws_dynamodb as dynamodb
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_ecr_assets as ecr_assets
import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_iam as iam
import aws_cdk.aws_logs as logs
import aws_cdk.aws_s3 as s3
from constructs import Construct


class JobExecutor(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        agent_subnets: ec2.SelectedSubnets,
        agent_security_group: ec2.ISecurityGroup,
        output_bucket: s3.IBucket,
        log_group: logs.ILogGroup,
    ) -> None:
        super().__init__(scope, construct_id)

        self.cluster = ecs.Cluster(
            self, "Cluster",
            vpc=vpc,
        )

        self.jobs_table = dynamodb.Table(
            self, "JobsTable",
            partition_key=dynamodb.Attribute(
                name="jobId", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            time_to_live_attribute="expiresAt",
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
        )

        self.jobs_table.add_global_secondary_index(
            index_name="tenantId-status-index",
            partition_key=dynamodb.Attribute(
                name="tenantId", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="status", type=dynamodb.AttributeType.STRING
            ),
        )

        self.jobs_table.add_global_secondary_index(
            index_name="taskArn-index",
            partition_key=dynamodb.Attribute(
                name="taskArn", type=dynamodb.AttributeType.STRING
            ),
        )

        self.secrets_prefix = "cuseinfra/tenants"

        task_role = iam.Role(
            self, "TaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    cdk.Stack.of(self).format_arn(
                        service="secretsmanager",
                        resource="secret",
                        resource_name=f"{self.secrets_prefix}/*",
                        arn_format=cdk.ArnFormat.COLON_RESOURCE_NAME,
                    ),
                ],
            )
        )

        image = ecs.ContainerImage.from_asset(
            "agent",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        self.task_definition = ecs.FargateTaskDefinition(
            self, "TaskDef",
            cpu=1024,
            memory_limit_mib=2048,
            task_role=task_role,
        )

        self.container_name = "agent"

        self.task_definition.add_container(
            self.container_name,
            image=image,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="agent",
                log_group=log_group,
            ),
            environment={},
        )
