import httpx
import pytest

from bot.services.roblox import (
    RobloxProfileNotFound,
    contains_verification_code,
    fetch_profile_by_nickname,
)


@pytest.mark.asyncio
async def test_fetch_profile_by_nickname_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/usernames/users":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "requestedUsername": "BigBob",
                            "id": 123456,
                        }
                    ]
                },
            )
        if request.url.path == "/v1/users/123456":
            return httpx.Response(200, json={"description": "BB-1a2b в описании"})
        if request.url.path == "/v1/users/123456/status":
            return httpx.Response(200, json={"status": "готов"})
        raise AssertionError(f"Unexpected path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://users.roblox.com")
    profile = await fetch_profile_by_nickname("BigBob", client=client)
    await client.aclose()

    assert profile.user_id == 123456
    assert profile.description == "BB-1a2b в описании"
    assert profile.status == "готов"


@pytest.mark.asyncio
async def test_fetch_profile_by_nickname_missing_user() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/usernames/users":
            return httpx.Response(200, json={"data": []})
        raise AssertionError("Unexpected request")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://users.roblox.com")

    with pytest.raises(RobloxProfileNotFound):
        await fetch_profile_by_nickname("GhostPlayer", client=client)

    await client.aclose()


def test_contains_verification_code_positive() -> None:
    assert contains_verification_code("!!bb-77ff!! в статусе", "BB-77FF")
    assert contains_verification_code("код BB-abc123 появится в тексте", "bb-ABC123")


def test_contains_verification_code_negative() -> None:
    assert not contains_verification_code("bb-77ff в статусе", "CC-9999")
    assert not contains_verification_code("", "BB-77ff")