from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _strip_quotes(v: object) -> object:
    if not isinstance(v, str):
        return v
    s = v.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    discord_bot_token: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    allowed_discord_user_id: int = 0
    instagram_cookies_file: Optional[str] = None
    instagram_cookies_b64: Optional[str] = None   # base64-encoded cookies.txt for Railway
    daily_request_limit: int = 50

    @field_validator("discord_bot_token", "gemini_api_key", "anthropic_api_key", mode="before")
    @classmethod
    def strip_secrets(cls, v: object) -> object:
        return _strip_quotes(v)


settings = Settings()
