import json
import os

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

JOBS_TABLE = os.environ["JOBS_TABLE"]
OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]
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

    s3_prefix = f"jobs/{job_id}/"

    try:
        objects = s3.list_objects_v2(Bucket=OUTPUT_BUCKET, Prefix=s3_prefix)
    except Exception as e:
        return response(500, {"error": str(e)})

    recording_url = None
    screenshot_urls = []

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

    return response(200, {
        "jobId": job_id,
        "recording": recording_url,
        "screenshots": screenshot_urls,
    })


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
