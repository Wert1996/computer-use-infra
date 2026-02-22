import json
import os
import time

import boto3
from boto3.dynamodb.conditions import Attr, Key

dynamodb = boto3.resource("dynamodb")
ecs = boto3.client("ecs")

JOBS_TABLE = os.environ["JOBS_TABLE"]
CLUSTER_ARN = os.environ["CLUSTER_ARN"]
ORPHAN_THRESHOLD_MINUTES = 20


def handler(event, context):
    table = dynamodb.Table(JOBS_TABLE)
    cutoff = int(time.time()) - (ORPHAN_THRESHOLD_MINUTES * 60)

    scan_result = table.scan(
        FilterExpression=Attr("status").eq("RUNNING") & Attr("createdAt").lt(cutoff),
    )

    for job in scan_result.get("Items", []):
        job_id = job["jobId"]
        task_arn = job.get("taskArn", "")

        if task_arn:
            try:
                ecs.stop_task(
                    cluster=CLUSTER_ARN,
                    task=task_arn,
                    reason="Sweep: exceeded maximum duration",
                )
            except Exception as e:
                print(json.dumps({
                    "action": "sweep_stop_failed",
                    "jobId": job_id,
                    "error": str(e),
                }))

        table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #s = :s, terminatedAt = :t, reason = :r",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "TIMED_OUT",
                ":t": int(time.time()),
                ":r": "sweep: exceeded maximum duration",
            },
        )

        print(json.dumps({"action": "swept", "jobId": job_id, "taskArn": task_arn}))

    try:
        running_tasks = ecs.list_tasks(cluster=CLUSTER_ARN, desiredStatus="RUNNING")
        task_arns = running_tasks.get("taskArns", [])

        if task_arns:
            known_arns = set()
            scan_all = table.scan(
                FilterExpression=Attr("status").eq("RUNNING"),
                ProjectionExpression="taskArn",
            )
            for item in scan_all.get("Items", []):
                if item.get("taskArn"):
                    known_arns.add(item["taskArn"])

            for arn in task_arns:
                if arn not in known_arns:
                    try:
                        ecs.stop_task(
                            cluster=CLUSTER_ARN,
                            task=arn,
                            reason="Sweep: orphaned task not in DynamoDB",
                        )
                        print(json.dumps({"action": "swept_orphan", "taskArn": arn}))
                    except Exception as e:
                        print(json.dumps({
                            "action": "sweep_orphan_failed",
                            "taskArn": arn,
                            "error": str(e),
                        }))
    except Exception as e:
        print(json.dumps({"action": "list_tasks_failed", "error": str(e)}))
