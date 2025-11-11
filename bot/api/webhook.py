from __future__ import annotations

import json
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db import init_db, session_scope
from ..models import EventQueue
from ..services.queue import enqueue_event
from ..services.security import verify_hmac

app = FastAPI(title="Bigbob Verify API")
settings = get_settings()


class VerifyCallback(BaseModel):
    event_id: str = Field(..., alias="eventId")
    player_id: int = Field(..., alias="playerId")
    code: str
    ts: datetime


async def validate_signature(signature: str = Header(..., alias="X-Signature")) -> str:
    return signature


@app.on_event("startup")
async def startup_event() -> None:
    await init_db()


@app.post("/api/verify-callback")
async def verify_callback(
    request: Request,
    payload: VerifyCallback,
    signature: str = Depends(validate_signature),
) -> dict:
    body = await request.body()
    if not verify_hmac(body, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    event = {
        "type": "verification",
        "payload": payload.model_dump(by_alias=True),
    }
    async with session_scope() as session:
        entry = EventQueue(event_id=payload.event_id, payload=json.dumps(event))
        session.add(entry)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {"status": "duplicate"}

    await enqueue_event(event)
    return {"status": "queued"}