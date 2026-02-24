import json
import os
import time

import boto3
from boto3.dynamodb.conditions import Attr, Key

dynamodb = boto3.resource("dynamodb")
ecs = boto3.client("ecs")
s3 = boto3.client("s3")

JOBS_TABLE = os.environ["JOBS_TABLE"]
CLUSTER_ARN = os.environ["CLUSTER_ARN"]
TASK_DEFINITION_ARN = os.environ["TASK_DEFINITION_ARN"]
CONTAINER_NAME = os.environ["CONTAINER_NAME"]
SUBNET_IDS = os.environ["SUBNET_IDS"].split(",")
SECURITY_GROUP_ID = os.environ["SECURITY_GROUP_ID"]
OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]
SECRETS_PREFIX = os.environ["SECRETS_PREFIX"]
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_PER_TENANT", "5"))


def handler(event, context):
    for record in event["Records"]:
        body = json.loads(record["body"])
        job_id = body["jobId"]
        tenant_id = body["tenantId"]
        task_description = body["taskDescription"]

        table = dynamodb.Table(JOBS_TABLE)

        running = table.query(
            IndexName="tenantId-status-index",
            KeyConditionExpression=Key("tenantId").eq(tenant_id) & Key("status").eq("RUNNING"),
            Select="COUNT",
        )
        if running["Count"] >= MAX_CONCURRENT:
            raise Exception(
                f"Tenant {tenant_id} has {running['Count']} running tasks, max is {MAX_CONCURRENT}"
            )

        s3_prefix = f"jobs/{job_id}/"

        presigned_post = s3.generate_presigned_post(
            Bucket=OUTPUT_BUCKET,
            Key="${filename}",
            Conditions=[
                ["starts-with", "$key", s3_prefix],
                ["content-length-range", 0, 104857600],
            ],
            ExpiresIn=3600,
        )

        result = ecs.run_task(
            cluster=CLUSTER_ARN,
            taskDefinition=TASK_DEFINITION_ARN,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": SUBNET_IDS,
                    "securityGroups": [SECURITY_GROUP_ID],
                    "assignPublicIp": "DISABLED",
                }
            },
            overrides={
                "containerOverrides": [
                    {
                        "name": CONTAINER_NAME,
                        "environment": [
                            {"name": "JOB_ID", "value": job_id},
                            {"name": "PRESIGNED_POST_DATA", "value": json.dumps(presigned_post)},
                            {"name": "S3_PREFIX", "value": s3_prefix},
                            {"name": "TENANT_ID", "value": tenant_id},
                            {"name": "SECRETS_PREFIX", "value": SECRETS_PREFIX},
                            {"name": "TASK_DESCRIPTION", "value": task_description},
                        ],
                    }
                ]
            },
        )

        if not result.get("tasks"):
            failures = result.get("failures", [])
            raise Exception(f"Failed to start task: {failures}")

        task_arn = result["tasks"][0]["taskArn"]

        table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #s = :s, taskArn = :ta, startedAt = :sa",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "RUNNING",
                ":ta": task_arn,
                ":sa": int(time.time()),
            },
        )
