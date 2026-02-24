import aws_cdk as cdk
import aws_cdk.assertions as assertions

from stacks.cuseinfra_stack import CuseinfraStack


def get_template():
    app = cdk.App()
    stack = CuseinfraStack(app, "TestStack")
    return assertions.Template.from_stack(stack)


def test_vpc_created():
    template = get_template()
    template.resource_count_is("AWS::EC2::VPC", 1)


def test_single_az_two_subnets():
    template = get_template()
    template.resource_count_is("AWS::EC2::Subnet", 2)


def test_agent_security_group_no_ingress():
    template = get_template()
    template.has_resource_properties("AWS::EC2::SecurityGroup", {
        "GroupDescription": "Security group for agent Fargate tasks",
        "SecurityGroupIngress": assertions.Match.absent(),
    })


def test_nacls_exist():
    template = get_template()
    nacls = template.find_resources("AWS::EC2::NetworkAcl")
    assert len(nacls) > 0, "Expected at least one NACL for agent subnets"


def test_fargate_task_role_no_s3():
    template = get_template()
    policies = template.find_resources("AWS::IAM::Policy")
    for logical_id, policy in policies.items():
        props = policy.get("Properties", {})
        # Check if this policy is attached to the task role
        roles = props.get("Roles", [])
        is_task_role = any(
            "TaskRole" in str(role) for role in roles
        )
        if is_task_role:
            statements = props.get("PolicyDocument", {}).get("Statement", [])
            for stmt in statements:
                actions = stmt.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                for action in actions:
                    assert "s3:PutObject" not in action, (
                        f"Task role should not have S3 put permissions, found {action}"
                    )


def test_three_sqs_fifo_queues_no_dlqs():
    template = get_template()
    template.resource_count_is("AWS::SQS::Queue", 3)


def test_sqs_fifo_queues_have_correct_names():
    template = get_template()
    for priority in ["high", "medium", "low"]:
        template.has_resource_properties("AWS::SQS::Queue", {
            "QueueName": f"jobs-{priority}.fifo",
            "FifoQueue": True,
            "ContentBasedDeduplication": True,
        })


def test_dynamodb_jobs_table():
    template = get_template()
    template.has_resource_properties("AWS::DynamoDB::Table", {
        "KeySchema": [{"AttributeName": "jobId", "KeyType": "HASH"}],
        "TimeToLiveSpecification": {
            "AttributeName": "expiresAt",
            "Enabled": True,
        },
        "StreamSpecification": {
            "StreamViewType": "NEW_AND_OLD_IMAGES",
        },
    })


def test_dynamodb_gsi_tenant_status():
    template = get_template()
    template.has_resource_properties("AWS::DynamoDB::Table", {
        "GlobalSecondaryIndexes": assertions.Match.array_with([
            assertions.Match.object_like({
                "IndexName": "tenantId-status-index",
                "KeySchema": [
                    {"AttributeName": "tenantId", "KeyType": "HASH"},
                    {"AttributeName": "status", "KeyType": "RANGE"},
                ],
            }),
        ]),
    })


def test_dynamodb_gsi_task_arn():
    template = get_template()
    template.has_resource_properties("AWS::DynamoDB::Table", {
        "GlobalSecondaryIndexes": assertions.Match.array_with([
            assertions.Match.object_like({
                "IndexName": "taskArn-index",
                "KeySchema": [
                    {"AttributeName": "taskArn", "KeyType": "HASH"},
                ],
            }),
        ]),
    })


def test_ecs_cluster_created():
    template = get_template()
    template.resource_count_is("AWS::ECS::Cluster", 1)


def test_fargate_task_definition():
    template = get_template()
    template.has_resource_properties("AWS::ECS::TaskDefinition", {
        "Cpu": "1024",
        "Memory": "2048",
        "RequiresCompatibilities": ["FARGATE"],
        "NetworkMode": "awsvpc",
    })


def test_eventbridge_ecs_stopped_rule():
    template = get_template()
    template.has_resource_properties("AWS::Events::Rule", {
        "EventPattern": {
            "source": ["aws.ecs"],
            "detail-type": ["ECS Task State Change"],
            "detail": {
                "lastStatus": ["STOPPED"],
            },
        },
    })


def test_eventbridge_sweep_schedule():
    template = get_template()
    template.has_resource_properties("AWS::Events::Rule", {
        "ScheduleExpression": "rate(10 minutes)",
    })


def test_s3_bucket_lifecycle():
    template = get_template()
    template.has_resource_properties("AWS::S3::Bucket", {
        "LifecycleConfiguration": {
            "Rules": assertions.Match.array_with([
                assertions.Match.object_like({
                    "ExpirationInDays": 7,
                    "Status": "Enabled",
                }),
            ]),
        },
    })


def test_api_gateway_created():
    template = get_template()
    template.has_resource_properties("AWS::ApiGatewayV2::Api", {
        "Name": "cuseinfra-api",
        "ProtocolType": "HTTP",
    })


def test_api_routes_exist():
    template = get_template()
    template.has_resource_properties("AWS::ApiGatewayV2::Route", {
        "RouteKey": "POST /jobs",
    })
    template.has_resource_properties("AWS::ApiGatewayV2::Route", {
        "RouteKey": "GET /jobs/{id}",
    })
