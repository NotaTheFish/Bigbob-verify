from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

ROBLOX_API_BASE_URL = "https://users.roblox.com"
_NORMALIZE_PATTERN = re.compile(r"[\W_]+", re.UNICODE)


@dataclass(slots=True)
class RobloxProfile:
    user_id: int
    description: str
    status: str


class RobloxServiceError(Exception):
    """Base exception for Roblox API helper."""


class RobloxProfileNotFound(RobloxServiceError):
    """Raised when the requested profile cannot be located."""


class RobloxRateLimitError(RobloxServiceError):
    """Raised when the public Roblox API responds with HTTP 429."""


async def fetch_profile_by_nickname(
    nickname: str, *, client: Optional[httpx.AsyncClient] = None
) -> RobloxProfile:
    """Resolve a Roblox nickname to a player ID and fetch their profile text."""

    normalized_nick = (nickname or "").strip()
    if not normalized_nick:
        raise ValueError("Roblox nickname must not be empty")

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(base_url=ROBLOX_API_BASE_URL, timeout=10.0)

    try:
        lookup = await _roblox_request(
            client,
            "POST",
            "/v1/usernames/users",
            json={"usernames": [normalized_nick], "excludeBannedUsers": False},
        )
        data = lookup.get("data") or []
        if not data:
            raise RobloxProfileNotFound(f"Roblox nickname '{normalized_nick}' was not found")

        user_id = int(data[0]["id"])
        profile_data = await _roblox_request(client, "GET", f"/v1/users/{user_id}")
        description = profile_data.get("description") or ""

        status = ""
        try:
            status_data = await _roblox_request(client, "GET", f"/v1/users/{user_id}/status")
            status = status_data.get("status") or ""
        except RobloxProfileNotFound:
            # The status endpoint may return 404 for new accounts. This should not block verification.
            status = ""

        return RobloxProfile(user_id=user_id, description=description, status=status)
    finally:
        if owns_client:
            await client.aclose()


def normalize_verification_text(value: Optional[str]) -> str:
    """Normalize strings for verification code matching."""

    if not value:
        return ""
    return _NORMALIZE_PATTERN.sub("", value.lower())


def contains_verification_code(text: Optional[str], code: Optional[str]) -> bool:
    normalized_text = normalize_verification_text(text)
    normalized_code = normalize_verification_text(code)
    if not normalized_text or not normalized_code:
        return False
    return normalized_code in normalized_text


async def _roblox_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> dict:
    retrying = AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type((RobloxRateLimitError, httpx.TransportError)),
        reraise=True,
    )
    async for attempt in retrying:
        with attempt:
            response = await client.request(method, url, **kwargs)
            if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                raise RobloxRateLimitError("Roblox API rate limited the request")
            if response.status_code == httpx.codes.NOT_FOUND:
                raise RobloxProfileNotFound("Roblox resource not found")
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:  # pragma: no cover - defensive logging
                raise RobloxServiceError("Roblox API request failed") from exc
            return response.json()

    raise RobloxServiceError("Roblox API request retries exhausted")


__all__ = [
    "RobloxProfile",
    "RobloxProfileNotFound",
    "RobloxRateLimitError",
    "RobloxServiceError",
    "fetch_profile_by_nickname",
    "normalize_verification_text",
    "contains_verification_code",
]