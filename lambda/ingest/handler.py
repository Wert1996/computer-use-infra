import json
import os
import time
import uuid
import sys

import boto3

# Import shared JWT validation utilities
sys.path.append('/opt/python')  # Lambda layer path
sys.path.append('../shared')   # Local development path

try:
    from jwt_validator import (
        validate_jwt, has_permission, get_all_user_tenants,
        JWTValidationError
    )
except ImportError:
    # Fallback for when shared module isn't available
    def validate_jwt(*args, **kwargs):
        raise JWTValidationError("JWT validation not available")
    def has_permission(*args, **kwargs):
        return False
    def get_all_user_tenants(*args, **kwargs):
        return []
    class JWTValidationError(Exception):
        pass

dynamodb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")

JOBS_TABLE = os.environ["JOBS_TABLE"]
QUEUE_URLS = {
    "high": os.environ["QUEUE_HIGH_URL"],
    "medium": os.environ["QUEUE_MEDIUM_URL"],
    "low": os.environ["QUEUE_LOW_URL"],
}
MAX_TASK_DURATION = int(os.environ.get("MAX_TASK_DURATION_SECONDS", "900"))

VALID_PRIORITIES = {"high", "medium", "low"}


def handler(event, context):
    # Validate JWT token first
    headers = event.get('headers', {})
    auth_header = headers.get('authorization') or headers.get('Authorization')

    if not auth_header:
        return response(401, {'error': 'Authorization header required'})

    try:
        user_pool_id = os.environ.get('USER_POOL_ID')
        if not user_pool_id:
            return response(500, {'error': 'Server configuration error'})

        claims = validate_jwt(auth_header, user_pool_id)
        user_tenants = get_all_user_tenants(claims)

    except JWTValidationError as e:
        return response(401, {'error': 'Invalid or expired token'})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"error": "Invalid JSON"})

    task_description = body.get("taskDescription")
    tenant_id = body.get("tenantId")
    priority = body.get("priority", "medium")

    if not task_description or not isinstance(task_description, str):
        return response(400, {"error": "taskDescription is required"})
    if not tenant_id or not isinstance(tenant_id, str):
        return response(400, {"error": "tenantId is required"})
    if priority not in VALID_PRIORITIES:
        return response(400, {"error": f"priority must be one of: {VALID_PRIORITIES}"})

    # Validate user has write permission for the tenant
    if not has_permission(claims, tenant_id, 'write'):
        return response(403, {"error": f"Write permission required for tenant {tenant_id}"})

    job_id = str(uuid.uuid4())
    now = int(time.time())

    table = dynamodb.Table(JOBS_TABLE)
    table.put_item(Item={
        "jobId": job_id,
        "tenantId": tenant_id,
        "priority": priority,
        "status": "PENDING",
        "taskDescription": task_description,
        "createdAt": now,
        "expiresAt": now + MAX_TASK_DURATION,
    })

    queue_url = QUEUE_URLS[priority]
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps({
            "jobId": job_id,
            "tenantId": tenant_id,
            "taskDescription": task_description,
            "priority": priority,
        }),
        MessageGroupId=tenant_id,
    )

    return response(202, {"jobId": job_id, "status": "PENDING"})


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"
        },
        "body": json.dumps(body),
    }
