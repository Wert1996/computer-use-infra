import json
import os
import urllib.request
import urllib.error
import base64
from typing import Dict, List, Optional, Any
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import logging

logger = logging.getLogger(__name__)


class JWTValidationError(Exception):
    """Custom exception for JWT validation failures"""
    pass


def get_cognito_public_keys(user_pool_id: str, region: str) -> Dict[str, Any]:
    """
    Fetch public keys from Cognito's JWKS endpoint.

    Args:
        user_pool_id: The Cognito User Pool ID
        region: AWS region

    Returns:
        Dict containing the JWKS public keys
    """
    jwks_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"

    try:
        with urllib.request.urlopen(jwks_url) as response:
            jwks = json.loads(response.read().decode())
            return jwks
    except urllib.error.URLError as e:
        logger.error(f"Failed to fetch JWKS from {jwks_url}: {e}")
        raise JWTValidationError(f"Could not fetch public keys: {e}")


def validate_jwt(token: str, user_pool_id: str, region: str = None) -> Dict[str, Any]:
    """
    Validate a JWT token from Cognito and return the decoded claims.

    Args:
        token: The JWT token to validate
        user_pool_id: The Cognito User Pool ID
        region: AWS region (defaults to environment variable)

    Returns:
        Dict containing the decoded JWT claims

    Raises:
        JWTValidationError: If the token is invalid or expired
    """
    if not region:
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

    if not token:
        raise JWTValidationError("No token provided")

    # Remove 'Bearer ' prefix if present
    if token.startswith("Bearer "):
        token = token[7:]

    try:
        # Decode header to get kid
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")

        if not kid:
            raise JWTValidationError("Token header missing 'kid' field")

        # Get public keys
        jwks = get_cognito_public_keys(user_pool_id, region)

        # Find the matching key
        public_key = None
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                # Convert JWK to PEM
                n = base64.urlsafe_b64decode(key["n"] + "===")  # Add padding
                e = base64.urlsafe_b64decode(key["e"] + "===")  # Add padding

                # Convert to int
                n_int = int.from_bytes(n, byteorder="big")
                e_int = int.from_bytes(e, byteorder="big")

                # Create RSA public key
                public_numbers = rsa.RSAPublicNumbers(e_int, n_int)
                public_key = public_numbers.public_key()
                break

        if not public_key:
            raise JWTValidationError(f"Public key not found for kid: {kid}")

        # Verify and decode the token
        decoded_token = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=None,  # We'll verify audience separately if needed
            options={"verify_aud": False}  # Disable audience verification
        )

        # Verify token usage (should be 'id' or 'access')
        token_use = decoded_token.get("token_use")
        if token_use not in ["id", "access"]:
            raise JWTValidationError(f"Invalid token_use: {token_use}")

        return decoded_token

    except jwt.ExpiredSignatureError:
        raise JWTValidationError("Token has expired")
    except jwt.InvalidTokenError as e:
        raise JWTValidationError(f"Invalid token: {e}")
    except Exception as e:
        logger.error(f"Unexpected error validating JWT: {e}")
        raise JWTValidationError(f"Token validation failed: {e}")


def get_user_permissions(claims: Dict[str, Any], tenant_id: str) -> List[str]:
    """
    Extract user permissions for a specific tenant from JWT claims.

    Args:
        claims: Decoded JWT claims
        tenant_id: The tenant ID to check permissions for

    Returns:
        List of permissions for the tenant (e.g., ["read", "write"])
    """
    # Look for custom:tenants claim
    tenants_claim = claims.get("custom:tenants")
    if not tenants_claim:
        return []

    try:
        # The tenants claim should be a JSON string
        if isinstance(tenants_claim, str):
            tenants = json.loads(tenants_claim)
        else:
            tenants = tenants_claim

        # Find permissions for the specific tenant
        for tenant in tenants:
            if tenant.get("tenantId") == tenant_id:
                return tenant.get("permissions", [])

        return []
    except (json.JSONDecodeError, TypeError, AttributeError):
        logger.warning(f"Invalid tenants claim format: {tenants_claim}")
        return []


def has_permission(claims: Dict[str, Any], tenant_id: str, required_permission: str) -> bool:
    """
    Check if the user has a specific permission for a tenant.

    Args:
        claims: Decoded JWT claims
        tenant_id: The tenant ID to check
        required_permission: The permission to check for ("admin", "read", "write")

    Returns:
        True if the user has the required permission
    """
    permissions = get_user_permissions(claims, tenant_id)
    return required_permission in permissions


def get_user_id(claims: Dict[str, Any]) -> Optional[str]:
    """
    Extract the user ID from JWT claims.

    Args:
        claims: Decoded JWT claims

    Returns:
        The user ID (Cognito sub claim)
    """
    return claims.get("sub")


def get_all_user_tenants(claims: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get all tenants and permissions for the user from JWT claims.

    Args:
        claims: Decoded JWT claims

    Returns:
        List of tenant objects with tenantId and permissions
    """
    tenants_claim = claims.get("custom:tenants")
    if not tenants_claim:
        return []

    try:
        if isinstance(tenants_claim, str):
            return json.loads(tenants_claim)
        else:
            return tenants_claim
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"Invalid tenants claim format: {tenants_claim}")
        return []


def create_authorization_response(allowed: bool, context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Create a standard authorization response for API Gateway.

    Args:
        allowed: Whether the request should be allowed
        context: Additional context to pass to the Lambda function

    Returns:
        Authorization response dict
    """
    return {
        "isAuthorized": allowed,
        "context": context or {}
    }