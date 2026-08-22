from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .models import GuildConfig, LeaderboardPeriod, RegisteredPlayer


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _empty_payload(self) -> dict[str, Any]:
        return {"guilds": {}, "registrations": {}, "riot_cache": {"players": {}}}

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_payload()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload.setdefault("riot_cache", {}).setdefault("players", {})
        return payload

    def save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temp_name = handle.name
        os.replace(temp_name, self.path)

    def get_guild_config(self, guild_id: int) -> GuildConfig:
        payload = self.load()
        raw = payload["guilds"].get(str(guild_id))
        if not raw:
            return GuildConfig(guild_id=guild_id)
        return GuildConfig(
            guild_id=guild_id,
            channel_id=raw.get("channel_id"),
            autopost_period=LeaderboardPeriod(raw.get("autopost_period", LeaderboardPeriod.WEEKLY.value)),
            default_queue=raw.get("default_queue", "all"),
            last_posted_at=datetime.fromisoformat(raw["last_posted_at"]) if raw.get("last_posted_at") else None,
        )

    def upsert_guild_config(self, config: GuildConfig) -> None:
        payload = self.load()
        config_payload = asdict(config)
        config_payload["autopost_period"] = config.autopost_period.value
        config_payload["last_posted_at"] = config.last_posted_at.isoformat() if config.last_posted_at else None
        payload["guilds"][str(config.guild_id)] = config_payload
        self.save(payload)

    def get_registration(self, guild_id: int, discord_user_id: int) -> RegisteredPlayer | None:
        payload = self.load()
        raw = payload["registrations"].get(str(guild_id), {}).get(str(discord_user_id))
        if not raw:
            return None
        return RegisteredPlayer(**raw)

    def set_registration(self, guild_id: int, player: RegisteredPlayer) -> None:
        payload = self.load()
        payload["registrations"].setdefault(str(guild_id), {})[str(player.discord_user_id)] = asdict(player)
        self.save(payload)

    def remove_registration(self, guild_id: int, discord_user_id: int) -> bool:
        payload = self.load()
        guild_entries = payload["registrations"].get(str(guild_id), {})
        removed = guild_entries.pop(str(discord_user_id), None)
        if removed is None:
            return False
        if guild_entries:
            payload["registrations"][str(guild_id)] = guild_entries
        else:
            payload["registrations"].pop(str(guild_id), None)
        self.save(payload)
        return True

    def list_registrations(self, guild_id: int) -> list[RegisteredPlayer]:
        payload = self.load()
        guild_entries = payload["registrations"].get(str(guild_id), {})
        return [RegisteredPlayer(**value) for value in guild_entries.values()]

    def get_riot_cache(self, key: str) -> dict[str, Any] | None:
        return self.load()["riot_cache"]["players"].get(key)

    def set_riot_cache(self, key: str, value: dict[str, Any]) -> None:
        payload = self.load()
        payload["riot_cache"]["players"][key] = value
        self.save(payload)
