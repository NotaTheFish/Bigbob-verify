from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import AnyHttpUrl, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    db_url: str = Field(..., alias="DB_URL")
    redis_url: str = Field(..., alias="REDIS_URL")
    hmac_secret: str = Field(..., alias="HMAC_SECRET")
    admin_initial_token: str = Field(..., alias="ADMIN_INITIAL_TOKEN")
    sentry_dsn: Optional[str] = Field(None, alias="SENTRY_DSN")
    webhook_base_url: Optional[AnyHttpUrl] = Field(None, alias="WEBHOOK_BASE_URL")
    allowed_admin_roles: List[str] = Field(default_factory=lambda: ["main", "manager", "support"])
    verification_code_ttl_seconds: int = Field(600, alias="VERIFICATION_CODE_TTL")
    admin_token_ttl_seconds: int = Field(900, alias="ADMIN_TOKEN_TTL")
    referral_reward_daily_cap: int = Field(1000, alias="REFERRAL_REWARD_DAILY_CAP")
    referral_activity_minutes_required: int = Field(10, alias="REFERRAL_ACTIVITY_MINUTES")


@lru_cache()
def get_settings() -> Settings:
    return Settings()


class TelegramWebhookConfig(BaseModel):
    url: AnyHttpUrl
    secret_token: str