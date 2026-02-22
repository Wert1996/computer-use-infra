import json
import os
import time

import boto3

dynamodb = boto3.resource("dynamodb")
ecs = boto3.client("ecs")

JOBS_TABLE = os.environ["JOBS_TABLE"]
CLUSTER_ARN = os.environ["CLUSTER_ARN"]


def handler(event, context):
    table = dynamodb.Table(JOBS_TABLE)

    for record in event.get("Records", []):
        if record["eventName"] != "REMOVE":
            continue

        old_image = record.get("dynamodb", {}).get("OldImage", {})
        job_id = old_image.get("jobId", {}).get("S", "")
        task_arn = old_image.get("taskArn", {}).get("S", "")
        status = old_image.get("status", {}).get("S", "")

        if not job_id:
            continue

        if task_arn and status == "RUNNING":
            try:
                ecs.stop_task(
                    cluster=CLUSTER_ARN,
                    task=task_arn,
                    reason="TTL expired - max duration exceeded",
                )
            except Exception as e:
                print(json.dumps({
                    "action": "stop_task_failed",
                    "jobId": job_id,
                    "taskArn": task_arn,
                    "error": str(e),
                }))

        table.put_item(Item={
            "jobId": job_id,
            "status": "TIMED_OUT",
            "terminatedAt": int(time.time()),
            "reason": "max duration exceeded",
            "taskArn": task_arn or "unknown",
        })

        print(json.dumps({
            "action": "reaped",
            "jobId": job_id,
            "taskArn": task_arn,
            "previousStatus": status,
        }))
