import json
import os
import boto3
import logging
from typing import Dict, Any, List
from botocore.exceptions import ClientError

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize DynamoDB client
dynamodb = boto3.resource('dynamodb')


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Cognito Pre Token Generation trigger handler.

    This function queries the user_permissions table and injects
    tenant permissions into the JWT token as custom claims.

    Args:
        event: Cognito trigger event
        context: Lambda context

    Returns:
        Modified event with custom claims added
    """
    logger.info(f"Pre-token generation event: {json.dumps(event, default=str)}")

    try:
        # Extract user information
        user_id = event['request']['userAttributes']['sub']
        user_pool_id = event['userPoolId']
        client_id = event['callerContext']['clientId']

        logger.info(f"Processing token generation for user: {user_id}")

        # Get permissions table name from environment
        permissions_table_name = os.environ.get('PERMISSIONS_TABLE')
        if not permissions_table_name:
            logger.error("PERMISSIONS_TABLE environment variable not set")
            # Return event unchanged - fail gracefully
            return event

        # Query user permissions
        permissions = get_user_permissions(permissions_table_name, user_id)
        logger.info(f"Found {len(permissions)} permissions for user {user_id}")

        # Build tenant permissions array
        tenant_permissions = build_tenant_permissions(permissions)

        # Add custom claims to the token
        if tenant_permissions:
            # Convert to JSON string for custom claim
            tenants_claim = json.dumps(tenant_permissions)

            # Add to claims override
            if 'claimsOverrideDetails' not in event['response']:
                event['response']['claimsOverrideDetails'] = {}

            if 'claimsToAddOrOverride' not in event['response']['claimsOverrideDetails']:
                event['response']['claimsOverrideDetails']['claimsToAddOrOverride'] = {}

            event['response']['claimsOverrideDetails']['claimsToAddOrOverride']['custom:tenants'] = tenants_claim

            logger.info(f"Added custom:tenants claim with {len(tenant_permissions)} tenants")
        else:
            logger.info(f"No permissions found for user {user_id}")

    except Exception as e:
        logger.error(f"Error in pre-token generation: {e}")
        # Fail gracefully - return original event to allow login
        # The authorization will fail later at the API level
        pass

    return event


def get_user_permissions(table_name: str, user_id: str) -> List[Dict[str, Any]]:
    """
    Query DynamoDB for all permissions for a specific user.

    Args:
        table_name: Name of the permissions table
        user_id: The user ID to query for

    Returns:
        List of permission items from DynamoDB
    """
    try:
        table = dynamodb.Table(table_name)

        response = table.query(
            KeyConditionExpression='userId = :user_id',
            ExpressionAttributeValues={
                ':user_id': user_id
            }
        )

        return response.get('Items', [])

    except ClientError as e:
        logger.error(f"DynamoDB error querying permissions for user {user_id}: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error querying permissions for user {user_id}: {e}")
        return []


def build_tenant_permissions(permissions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert DynamoDB permission items to tenant permissions format.

    Args:
        permissions: List of permission items from DynamoDB

    Returns:
        List of tenant permission objects for JWT claims
    """
    tenant_permissions = []

    for perm in permissions:
        try:
            logger.info(f"Processing permission item: {json.dumps(perm, default=str)}")

            tenant_id = perm.get('tenantId')
            permissions_set = perm.get('permissions')

            if not tenant_id:
                logger.warning(f"Missing tenantId in permission item: {perm}")
                continue

            if permissions_set is None:
                logger.warning(f"No permissions found for tenant {tenant_id}")
                continue

            logger.info(f"Permissions set type: {type(permissions_set)}, value: {permissions_set}")

            # Convert DynamoDB Set to list
            permissions_list = []

            if isinstance(permissions_set, set):
                # Python set (from DynamoDB resource conversion)
                permissions_list = list(permissions_set)
            elif isinstance(permissions_set, list):
                # Already a list
                permissions_list = permissions_set
            elif hasattr(permissions_set, '__iter__') and not isinstance(permissions_set, str):
                # Other iterable types
                permissions_list = list(permissions_set)
            elif isinstance(permissions_set, str):
                # Single permission as string
                permissions_list = [permissions_set]
            else:
                logger.warning(f"Unexpected permissions format for tenant {tenant_id}: {type(permissions_set)} - {permissions_set}")
                continue

            logger.info(f"Converted permissions for tenant {tenant_id}: {permissions_list}")

            tenant_permissions.append({
                'tenantId': tenant_id,
                'permissions': permissions_list
            })

        except Exception as e:
            logger.error(f"Error processing permission item {perm}: {e}")
            continue

    logger.info(f"Final tenant permissions: {tenant_permissions}")
    return tenant_permissions