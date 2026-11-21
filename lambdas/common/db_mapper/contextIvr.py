import os
import boto3
from botocore.exceptions import ClientError
from typing import Optional, Dict, Any

# --------------------------------------------------
# ENV
# --------------------------------------------------

AWS_REGION = os.getenv("AWS_REGION", "ap-south-2")
CONTEXT_TABLE = os.getenv("CONTEXT_TABLE", "ContextIvr")

# --------------------------------------------------
# DYNAMODB RESOURCE
# --------------------------------------------------

_dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
_table = _dynamodb.Table(CONTEXT_TABLE)

# --------------------------------------------------
# INTERNAL NORMALIZER
# --------------------------------------------------

def _normalize_context_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert raw DynamoDB item into clean Python dict
    """

    return {
        "contextId": item["contextId"],

        # Basic metadata
        "contextName": item.get("contextName"),
        "industry": item.get("industry"),
        "description": item.get("description"),
        "vendorId": item.get("vendorId"),

        # Voice configuration
        "language": item.get("language"),
        "gender": item.get("gender"),

        # Prompts
        "prompt": item.get("prompt"),

        # Conversation messages
        "greeting": item.get("greeting"),
        "closing": item.get("closing"),

        # Knowledge base
        "knowledgeBaseId": item.get("knowledgeBaseId"),
        "documentUrlList": item.get("documentUrlList"),

        # Model parameters
        "noOfResults": int(item.get("noOfResults", 3)),
        "temperature": float(item.get("temperature", 0.5)),
        "topP": float(item.get("topP", 0.9)),

        # Output schema
        "responseFormat": item.get("responseFormat", {}),

        # Status
        "isActive": item.get("isActive", False),

        # Timestamps
        "createdDate": item.get("createdDate"),
        "updatedDate": item.get("updatedDate"),
    }
# --------------------------------------------------
# PUBLIC API
# --------------------------------------------------

def get_context_by_id(context_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch a context by UUID.

    Returns:
        dict -> normalized context
        None -> not found or inactive
    """

    try:
        response = _table.get_item(
            Key={"contextId": context_id},
            ConsistentRead=True
        )

    except ClientError as e:
        raise RuntimeError(
            f"DynamoDB get_item failed: {e.response['Error']['Message']}"
        )

    item = response.get("Item")

    if not item:
        return None

    if not item.get("isActive", False):
        return None

    return _normalize_context_item(item)
