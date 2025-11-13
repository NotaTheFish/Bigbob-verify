from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from telegram import Update
from telegram.ext import Application

from ..config import get_settings
from ..db import init_db
from ..main import build_application
from .verification import router as verification_router

app = FastAPI(title="Bigbob Verify API")
app.include_router(verification_router)
settings = get_settings()


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


@app.post("/webhook")
async def telegram_webhook(request: Request) -> dict:
    application: Application | None = getattr(app.state, "telegram_application", None)
    if application is None:
        raise HTTPException(status_code=500, detail="Бот не готов")

    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"status": "успешно"}