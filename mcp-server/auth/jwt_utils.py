"""Extract client_id from Cognito JWT passed through AgentCore Gateway."""

import base64
import json
import os


def get_client_id(event: dict) -> str:
    """
    Extract client_id from the Lambda event.
    
    AgentCore Gateway validates the JWT and passes claims in the event.
    For client_credentials flow, the 'client_id' claim identifies the caller.
    Falls back to checking Authorization header if claims not in event.
    """
    # AgentCore may inject identity info directly
    if "requestContext" in event:
        authorizer = event["requestContext"].get("authorizer", {})
        if "claims" in authorizer:
            return authorizer["claims"].get("client_id", "")

    # Try extracting from JWT in Authorization header
    headers = event.get("headers", {})
    auth = headers.get("Authorization", headers.get("authorization", ""))
    if auth.startswith("Bearer "):
        token = auth[7:]
        try:
            # Decode payload (middle segment) without verification
            # Gateway already verified the signature
            payload = token.split(".")[1]
            # Fix padding
            payload += "=" * (4 - len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            return claims.get("client_id", claims.get("sub", ""))
        except Exception:
            pass

    return ""
