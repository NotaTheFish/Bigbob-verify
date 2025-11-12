from __future__ import annotations

import json
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from telegram import Update
from telegram.ext import Application
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db import init_db, session_scope
from ..main import build_application
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
    application = await build_application()
    await application.initialize()
    await application.start()
    app.state.telegram_application = application


@app.on_event("shutdown")
async def shutdown_event() -> None:
    application: Application | None = getattr(app.state, "telegram_application", None)
    if not application:
        return
    await application.stop()
    await application.shutdown()
    app.state.telegram_application = None


@app.post("/api/verify-callback")
async def verify_callback(
    request: Request,
    payload: VerifyCallback,
    signature: str = Depends(validate_signature),
) -> dict:
    body = await request.body()
    if not verify_hmac(body, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Недопустимая подпись")

    event = {
        "type": "verification",
        "payload": payload.model_dump(by_alias=True, mode="json"),
    }
    async with session_scope() as session:
        entry = EventQueue(event_id=payload.event_id, payload=json.dumps(event))
        session.add(entry)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {"status": "дубликат"}

    await enqueue_event(event)
    return {"status": "в_очереди"}


@app.post("/webhook")
async def telegram_webhook(request: Request) -> dict:
    application: Application | None = getattr(app.state, "telegram_application", None)
    if application is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Бот не готов")

    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"status": "успешно"}