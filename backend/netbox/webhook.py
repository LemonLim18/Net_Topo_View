import logging
import hmac
import hashlib
import os
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel
from typing import Dict, Any, Optional

from db.manager import DatabaseManager
from netbox.sync_devices import DeviceSyncer

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

        # 3. Handle Events
        db = DatabaseManager("net_topo.db")
        
        if model == "device":
             # Incremental sync could be done here, but for safety we resync all or specific device
             # For MVP, let's trigger a full sync in background or just log it
             syncer = DeviceSyncer(db)
             # In production, use background task: background_tasks.add_task(syncer.sync_all_devices)
             syncer.sync_all_devices()
             
        elif model == "cable":
             # Trigger topology sync
             pass

        return {"status": "processed"}

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
