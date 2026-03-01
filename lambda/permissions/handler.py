import json
import os
import time
import boto3
import logging
from typing import Dict, Any, List, Optional
from botocore.exceptions import ClientError

# Import shared JWT validation utilities
import sys
sys.path.append('/opt/python')  # Lambda layer path
sys.path.append('../shared')   # Local development path

try:
    from jwt_validator import (
        validate_jwt, has_permission, get_user_id,
        JWTValidationError
    )
except ImportError:
    # Fallback for when shared module isn't available
    def validate_jwt(*args, **kwargs):
        raise JWTValidationError("JWT validation not available")
    def has_permission(*args, **kwargs):
        return False
    def get_user_id(*args, **kwargs):
        return None
    class JWTValidationError(Exception):
        pass

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize DynamoDB client
dynamodb = boto3.resource('dynamodb')


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Permissions management Lambda handler.

    Handles admin-only operations for managing user permissions:
    - PUT /permissions/{userId} - Update user permissions for a tenant
    - POST /tenants/{tenantId}/users - Add user to tenant
    - DELETE /permissions/{userId}/{tenantId} - Remove user from tenant
    - GET /tenants/{tenantId}/users - List users in tenant

    Args:
        event: API Gateway event
        context: Lambda context

    Returns:
        HTTP response
    """
    try:
        # Extract request information
        http_method = event.get('requestContext', {}).get('http', {}).get('method')
        path = event.get('requestContext', {}).get('http', {}).get('path', '')
        path_parameters = event.get('pathParameters', {})
        query_parameters = event.get('queryStringParameters') or {}
        headers = event.get('headers', {})

        logger.info(f"Processing {http_method} {path}")

        # Validate JWT and extract claims
        auth_header = headers.get('authorization') or headers.get('Authorization')
        if not auth_header:
            return create_response(401, {'error': 'Authorization header required'})

        try:
            user_pool_id = os.environ.get('USER_POOL_ID')
            if not user_pool_id:
                logger.error("USER_POOL_ID environment variable not set")
                return create_response(500, {'error': 'Server configuration error'})

            claims = validate_jwt(auth_header, user_pool_id)
            requesting_user_id = get_user_id(claims)

            if not requesting_user_id:
                return create_response(401, {'error': 'Invalid token claims'})

        except JWTValidationError as e:
            logger.warning(f"JWT validation failed: {e}")
            return create_response(401, {'error': 'Invalid or expired token'})

        # Route to appropriate handler
        if http_method == 'PUT' and '/permissions/' in path:
            return handle_update_permissions(event, claims, path_parameters)
        elif http_method == 'POST' and '/tenants/' in path and '/users' in path:
            return handle_add_user_to_tenant(event, claims, path_parameters)
        elif http_method == 'DELETE' and '/permissions/' in path:
            return handle_remove_user_from_tenant(event, claims, path_parameters)
        elif http_method == 'GET' and '/tenants/' in path and '/users' in path:
            return handle_list_tenant_users(event, claims, path_parameters, query_parameters)
        else:
            return create_response(404, {'error': 'Endpoint not found'})

    except Exception as e:
        logger.error(f"Unexpected error in permissions handler: {e}")
        return create_response(500, {'error': 'Internal server error'})


def handle_update_permissions(event: Dict[str, Any], claims: Dict[str, Any], path_params: Dict[str, str]) -> Dict[str, Any]:
    """
    Handle PUT /permissions/{userId} - Update user permissions for a tenant.
    """
    try:
        target_user_id = path_params.get('userId')
        if not target_user_id:
            return create_response(400, {'error': 'userId path parameter required'})

        # Parse request body
        try:
            body = json.loads(event.get('body', '{}'))
        except json.JSONDecodeError:
            return create_response(400, {'error': 'Invalid JSON in request body'})

        tenant_id = body.get('tenantId')
        permissions = body.get('permissions', [])

        if not tenant_id:
            return create_response(400, {'error': 'tenantId is required'})

        if not isinstance(permissions, list):
            return create_response(400, {'error': 'permissions must be an array'})

        # Validate permission values
        valid_permissions = {'admin', 'read', 'write'}
        invalid_perms = set(permissions) - valid_permissions
        if invalid_perms:
            return create_response(400, {'error': f'Invalid permissions: {list(invalid_perms)}'})

        # Check if requesting user has admin permission for this tenant
        if not has_permission(claims, tenant_id, 'admin'):
            return create_response(403, {'error': 'Admin permission required for this tenant'})

        # Update permissions in DynamoDB
        table_name = os.environ.get('PERMISSIONS_TABLE')
        if not table_name:
            return create_response(500, {'error': 'Server configuration error'})

        table = dynamodb.Table(table_name)
        requesting_user_id = get_user_id(claims)

        if permissions:
            # Update or create permissions
            table.put_item(
                Item={
                    'userId': target_user_id,
                    'tenantId': tenant_id,
                    'permissions': set(permissions),  # DynamoDB StringSet
                    'createdAt': int(time.time()),
                    'updatedAt': int(time.time()),
                    'createdBy': requesting_user_id
                }
            )
        else:
            # Remove permissions (empty array means remove)
            table.delete_item(
                Key={
                    'userId': target_user_id,
                    'tenantId': tenant_id
                }
            )

        return create_response(200, {
            'userId': target_user_id,
            'tenantId': tenant_id,
            'permissions': permissions
        })

    except ClientError as e:
        logger.error(f"DynamoDB error updating permissions: {e}")
        return create_response(500, {'error': 'Failed to update permissions'})


def handle_add_user_to_tenant(event: Dict[str, Any], claims: Dict[str, Any], path_params: Dict[str, str]) -> Dict[str, Any]:
    """
    Handle POST /tenants/{tenantId}/users - Add user to tenant.
    """
    try:
        tenant_id = path_params.get('tenantId')
        if not tenant_id:
            return create_response(400, {'error': 'tenantId path parameter required'})

        # Check admin permission
        if not has_permission(claims, tenant_id, 'admin'):
            return create_response(403, {'error': 'Admin permission required for this tenant'})

        # Parse request body
        try:
            body = json.loads(event.get('body', '{}'))
        except json.JSONDecodeError:
            return create_response(400, {'error': 'Invalid JSON in request body'})

        user_id = body.get('userId')
        permissions = body.get('permissions', ['read'])

        if not user_id:
            return create_response(400, {'error': 'userId is required'})

        # Validate permissions
        valid_permissions = {'admin', 'read', 'write'}
        invalid_perms = set(permissions) - valid_permissions
        if invalid_perms:
            return create_response(400, {'error': f'Invalid permissions: {list(invalid_perms)}'})

        # Add user to tenant
        table_name = os.environ.get('PERMISSIONS_TABLE')
        table = dynamodb.Table(table_name)
        requesting_user_id = get_user_id(claims)

        table.put_item(
            Item={
                'userId': user_id,
                'tenantId': tenant_id,
                'permissions': set(permissions),
                'createdAt': int(time.time()),
                'updatedAt': int(time.time()),
                'createdBy': requesting_user_id
            }
        )

        return create_response(201, {
            'userId': user_id,
            'tenantId': tenant_id,
            'permissions': permissions
        })

    except ClientError as e:
        logger.error(f"DynamoDB error adding user to tenant: {e}")
        return create_response(500, {'error': 'Failed to add user to tenant'})


def handle_remove_user_from_tenant(event: Dict[str, Any], claims: Dict[str, Any], path_params: Dict[str, str]) -> Dict[str, Any]:
    """
    Handle DELETE /permissions/{userId}/{tenantId} - Remove user from tenant.
    """
    try:
        user_id = path_params.get('userId')
        tenant_id = path_params.get('tenantId')

        if not user_id or not tenant_id:
            return create_response(400, {'error': 'userId and tenantId path parameters required'})

        # Check admin permission
        if not has_permission(claims, tenant_id, 'admin'):
            return create_response(403, {'error': 'Admin permission required for this tenant'})

        # Remove user from tenant
        table_name = os.environ.get('PERMISSIONS_TABLE')
        table = dynamodb.Table(table_name)

        table.delete_item(
            Key={
                'userId': user_id,
                'tenantId': tenant_id
            }
        )

        return create_response(200, {'message': 'User removed from tenant'})

    except ClientError as e:
        logger.error(f"DynamoDB error removing user from tenant: {e}")
        return create_response(500, {'error': 'Failed to remove user from tenant'})


def handle_list_tenant_users(
    event: Dict[str, Any],
    claims: Dict[str, Any],
    path_params: Dict[str, str],
    query_params: Dict[str, str]
) -> Dict[str, Any]:
    """
    Handle GET /tenants/{tenantId}/users - List users in tenant.
    """
    try:
        tenant_id = path_params.get('tenantId')
        if not tenant_id:
            return create_response(400, {'error': 'tenantId path parameter required'})

        # Check admin permission
        if not has_permission(claims, tenant_id, 'admin'):
            return create_response(403, {'error': 'Admin permission required for this tenant'})

        # Query users in tenant using GSI
        table_name = os.environ.get('PERMISSIONS_TABLE')
        table = dynamodb.Table(table_name)

        response = table.query(
            IndexName='tenantId-index',
            KeyConditionExpression='tenantId = :tenant_id',
            ExpressionAttributeValues={
                ':tenant_id': tenant_id
            }
        )

        users = []
        for item in response.get('Items', []):
            permissions = item.get('permissions', set())
            # Convert DynamoDB Set to list
            if hasattr(permissions, 'value'):
                permissions_list = list(permissions)
            else:
                permissions_list = list(permissions)

            users.append({
                'userId': item.get('userId'),
                'permissions': permissions_list,
                'createdAt': item.get('createdAt'),
                'createdBy': item.get('createdBy')
            })

        return create_response(200, {
            'tenantId': tenant_id,
            'users': users
        })

    except ClientError as e:
        logger.error(f"DynamoDB error listing tenant users: {e}")
        return create_response(500, {'error': 'Failed to list tenant users'})


def create_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a standardized HTTP response.
    """
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
        },
        'body': json.dumps(body)
    }