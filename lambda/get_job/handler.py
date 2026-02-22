import json
import os

import boto3

dynamodb = boto3.resource("dynamodb")

JOBS_TABLE = os.environ["JOBS_TABLE"]


def handler(event, context):
    job_id = event.get("pathParameters", {}).get("id", "")
    if not job_id:
        return response(400, {"error": "Missing job ID"})

    table = dynamodb.Table(JOBS_TABLE)
    result = table.get_item(Key={"jobId": job_id})
    job = result.get("Item")

    if not job:
        return response(404, {"error": "Job not found"})

    output = None
    if job.get("output"):
        try:
            output = json.loads(job["output"])
        except (json.JSONDecodeError, TypeError):
            output = job["output"]

    body = {
        "jobId": job["jobId"],
        "status": job.get("status", "UNKNOWN"),
        "tenantId": job.get("tenantId"),
        "priority": job.get("priority"),
        "taskDescription": job.get("taskDescription"),
        "createdAt": job.get("createdAt"),
        "startedAt": job.get("startedAt"),
        "completedAt": job.get("completedAt"),
        "exitCode": job.get("exitCode"),
        "output": output,
    }

    body = {k: v for k, v in body.items() if v is not None}

    return response(200, body)


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
