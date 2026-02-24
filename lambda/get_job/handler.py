import json
import os

import boto3

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

JOBS_TABLE = os.environ["JOBS_TABLE"]
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "")
URL_EXPIRY = 3600


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

    if OUTPUT_BUCKET and job.get("status") in ("COMPLETED", "FAILED"):
        recording_url, screenshot_urls = get_presigned_urls(job_id)
        body["recording"] = recording_url
        body["screenshots"] = screenshot_urls

    body = {k: v for k, v in body.items() if v is not None}

    return response(200, body)


def get_presigned_urls(job_id):
    s3_prefix = f"jobs/{job_id}/"
    recording_url = None
    screenshot_urls = []

    try:
        objects = s3.list_objects_v2(Bucket=OUTPUT_BUCKET, Prefix=s3_prefix)
    except Exception:
        return None, []

    for obj in objects.get("Contents", []):
        key = obj["Key"]
        presigned = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": OUTPUT_BUCKET, "Key": key},
            ExpiresIn=URL_EXPIRY,
        )
        if key.endswith("recording.mp4"):
            recording_url = presigned
        elif "screenshots/" in key and key.endswith(".png"):
            screenshot_urls.append(presigned)

    return recording_url, screenshot_urls


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
