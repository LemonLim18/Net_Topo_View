import logging
import hmac
import hashlib
import os
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel
from typing import Dict, Any, Optional

from llm.context_contract import CONTEXT_DOMAIN_INVENTORY, CONTEXT_DOMAIN_PLANNED_TOPOLOGY
from llm.refresh_fabric import publish_context_refresh_event

logger = logging.getLogger("netbox.webhook")
router = APIRouter()

# Secret to verify webhook signature
NETBOX_WEBHOOK_SECRET = os.environ.get("NETBOX_WEBHOOK_SECRET", "mysecretEncoded").encode()

class WebhookPayload(BaseModel):
    event: str
    timestamp: str
    model: str
    username: str
    request_id: str
    data: Dict[str, Any]
    snapshots: Optional[Dict[str, Any]] = None

@router.post("/webhook")
async def netbox_webhook(
    request: Request,
    x_hook_signature: str = Header(None)
):
    """
    Receives real-time updates from NetBox.
    Verifies HMAC signature and triggers appropriate sync action.
    """
    # 1. Verify Signature
    body = await request.body()
    if not verify_signature(body, x_hook_signature):
        logger.warning(f"Invalid webhook signature from {request.client.host}")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # 2. Parse Payload
    try:
        payload = await request.json()
        model = payload.get("model")
        event = payload.get("event") # created, updated, deleted
        data = payload.get("data")
        
        logger.info(f"Received NetBox Webhook: {event} {model} ({data.get('name')})")
        domains = []
        refresh_targets = []
        if model in {"device", "interface", "ipaddress"}:
            domains.append(CONTEXT_DOMAIN_INVENTORY)
            refresh_targets.append("inventory")
        if model in {"device", "cable", "interface"}:
            domains.append(CONTEXT_DOMAIN_PLANNED_TOPOLOGY)
            refresh_targets.append("planned_topology")

        publication = publish_context_refresh_event(
            source="netbox_webhook",
            action=f"netbox_{event}_{model}",
            summary=f"{event} {model}",
            domains=tuple(domains),
            refresh_targets=tuple(refresh_targets),
            metadata={"model": model, "event": event, "name": data.get("name")},
        )

        return {"status": "processed", "publication": publication}

    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return {"status": "error", "detail": str(e)}

def verify_signature(body: bytes, signature: str) -> bool:
    if not NETBOX_WEBHOOK_SECRET or not signature:
        return True # Fail open in dev if secret not set, or False to be strict
    
    # NetBox sends "sha512 <hex>"
    algo, signature_digest = signature.split(" ")
    computed_digest = hmac.new(NETBOX_WEBHOOK_SECRET, body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(computed_digest, signature_digest)
