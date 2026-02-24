import json
import os
import time

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

JOBS_TABLE = os.environ["JOBS_TABLE"]
OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]


def handler(event, context):
    detail = event.get("detail", {})
    task_arn = detail.get("taskArn", "")
    cluster_arn = detail.get("clusterArn", "")

    if not task_arn:
        return

    table = dynamodb.Table(JOBS_TABLE)

    result = table.query(
        IndexName="taskArn-index",
        KeyConditionExpression=Key("taskArn").eq(task_arn),
        Limit=1,
    )

    if not result["Items"]:
        return

    job = result["Items"][0]
    job_id = job["jobId"]

    if job.get("status") in ("COMPLETED", "FAILED", "TIMED_OUT"):
        return

    containers = detail.get("containers", [])
    exit_code = None
    stop_reason = detail.get("stoppedReason", "")
    for c in containers:
        if "exitCode" in c:
            exit_code = c["exitCode"]
            break

    s3_prefix = f"jobs/{job_id}/"
    status = "FAILED"
    output = None

    if exit_code == 0:
        try:
            resp = s3.get_object(Bucket=OUTPUT_BUCKET, Key=f"{s3_prefix}result.json")
            output = json.loads(resp["Body"].read().decode("utf-8"))
            status = "COMPLETED"
        except s3.exceptions.NoSuchKey:
            status = "FAILED"
            stop_reason = stop_reason or "No result.json found"
        except Exception as e:
            status = "FAILED"
            stop_reason = stop_reason or str(e)
    else:
        status = "FAILED"

    update_expr = "SET #s = :s, completedAt = :ca"
    expr_values = {
        ":s": status,
        ":ca": int(time.time()),
    }
    expr_names = {"#s": "status"}

    if exit_code is not None:
        update_expr += ", exitCode = :ec"
        expr_values[":ec"] = exit_code

    if stop_reason:
        update_expr += ", stopReason = :sr"
        expr_values[":sr"] = stop_reason

    if output:
        update_expr += ", #o = :out"
        expr_values[":out"] = json.dumps(output)
        expr_names["#o"] = "output"

    table.update_item(
        Key={"jobId": job_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )
