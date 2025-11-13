from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..services.security import verify_hmac
from ..verification import service as verification_service

logger = logging.getLogger(__name__)
router = APIRouter()


class VerificationCheckPayload(BaseModel):
    username: str
    player_id: int = Field(..., alias="playerId")
    code: str


class VerificationStatusPayload(BaseModel):
    username: str
    player_id: int = Field(..., alias="playerId")


async def _require_signature(signature: str = Header(..., alias="X-Signature")) -> str:
    return signature


@router.post("/bot/verification/check")
async def verification_check(
    request: Request,
    payload: VerificationCheckPayload,
    signature: str = Depends(_require_signature),
) -> dict:
    body = await request.body()
    if not verify_hmac(body, signature):
        logger.warning("Invalid HMAC for verification check username=%s", payload.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_signature")

    result = await verification_service.process_backend_confirmation(
        payload.username,
        payload.code,
        payload.player_id,
    )
    logger.info(
        "Verification check request username=%s status=%s", result.username, result.status
    )
    return {"status": result.status, "username": result.username}


@router.post("/bot/verification/status")
async def verification_status(
    request: Request,
    payload: VerificationStatusPayload,
    signature: str = Depends(_require_signature),
) -> dict:
    body = await request.body()
    if not verify_hmac(body, signature):
        logger.warning("Invalid HMAC for verification status username=%s", payload.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_signature")

    result = await verification_service.fetch_status_for_username(payload.username)
    logger.info(
        "Verification status request username=%s status=%s", result.username, result.status
    )
    return {"status": result.status, "username": result.username}


__all__ = ["router"]