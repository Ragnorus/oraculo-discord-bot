from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import os


LOGGER = logging.getLogger(__name__)

_QUOTES = {"'", '"'}


def _strip_surrounding_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in _QUOTES:
        return token[1:-1]
    return token


def _normalize_riot_api_key(raw_value: str) -> str:
    token = raw_value.strip()
    # Strip surrounding quotes first (e.g. "RGAPI-xxx" or 'RGAPI-xxx')
    token = _strip_surrounding_quotes(token).strip()
    # Strip bearer prefix (case-insensitive), e.g. bearer RGAPI-xxx or bearer 'RGAPI-xxx'
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
        # Strip quotes that may have been wrapped around the key after the bearer prefix
        token = _strip_surrounding_quotes(token).strip()
    return token


@dataclass(slots=True)
class BotSettings:
    discord_token: str
    riot_api_key: str
    riot_account_region: str
    riot_platform_region: str
    data_path: Path
    scheduler_interval_minutes: int
    chart_port: int
    public_url: str | None
    riot_cache_ttl_seconds: int


def load_settings() -> BotSettings:
    discord_token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    riot_api_key = _normalize_riot_api_key(os.getenv("RIOT_API_KEY", ""))
    if not discord_token:
        raise ValueError("DISCORD_BOT_TOKEN is required")
    if not riot_api_key:
        raise ValueError("RIOT_API_KEY is required")

    LOGGER.info("Riot API key loaded: length=%d", len(riot_api_key))

    data_path = Path(os.getenv("ORACULO_DATA_PATH", "data/oraculo.json")).expanduser()
    scheduler_interval = int(os.getenv("ORACULO_SCHEDULER_INTERVAL_MINUTES", "30"))
    cache_ttl = max(int(os.getenv("ORACULO_RIOT_CACHE_TTL_SECONDS", "300")), 1)

    return BotSettings(
        discord_token=discord_token,
        riot_api_key=riot_api_key,
        riot_account_region=os.getenv("RIOT_ACCOUNT_REGION", "americas"),
        riot_platform_region=os.getenv("RIOT_PLATFORM_REGION", "na1"),
        data_path=data_path,
        scheduler_interval_minutes=max(scheduler_interval, 5),
        chart_port=int(os.getenv("ORACULO_CHART_PORT", "8080")),
        public_url=os.getenv("ORACULO_PUBLIC_URL", "").strip() or None,
        riot_cache_ttl_seconds=cache_ttl,
    )
