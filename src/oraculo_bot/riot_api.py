from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import aiohttp

from .models import PlayerStats, QUEUE_FILTERS


class RiotAPIError(RuntimeError):
    pass


@dataclass(slots=True)
class RiotAccount:
    puuid: str
    game_name: str
    tag_line: str


class RiotAPIClient:
    def __init__(self, api_key: str, account_region: str, platform_region: str, timeout_seconds: int = 20) -> None:
        self.api_key = api_key
        self.account_region = account_region
        self.platform_region = platform_region
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={"X-Riot-Token": self.api_key},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request_json(self, url: str, params: dict[str, Any] | None = None, _retries: int = 3) -> Any:
        session = await self.session()
        async with session.get(url, params=params) as response:
            if response.status == 404:
                raise RiotAPIError("Riot account or match data was not found.")
            if response.status == 429:
                retry_after = int(response.headers.get("Retry-After", "1"))
                if _retries > 0:
                    await asyncio.sleep(retry_after + 0.5)
                    return await self._request_json(url, params, _retries - 1)
                raise RiotAPIError(f"Riot API rate limit exceeded. Retry after {retry_after} seconds.")
            if response.status >= 400:
                detail = await response.text()
                raise RiotAPIError(f"Riot API request failed with status {response.status}: {detail}")
            return await response.json()

    async def resolve_account(self, game_name: str, tag_line: str) -> RiotAccount:
        url = (
            f"https://{self.account_region}.api.riotgames.com/riot/account/v1/accounts"
            f"/by-riot-id/{quote(game_name)}/{quote(tag_line)}"
        )
        payload = await self._request_json(url)
        return RiotAccount(
            puuid=payload["puuid"],
            game_name=payload.get("gameName", game_name),
            tag_line=payload.get("tagLine", tag_line),
        )

    async def fetch_match_ids(self, puuid: str, start: datetime, end: datetime, count_limit: int = 300) -> list[str]:
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        base_url = f"https://{self.account_region}.api.riotgames.com/lol/match/v5/matches/by-puuid/{quote(puuid)}/ids"
        start_index = 0
        match_ids: list[str] = []
        while start_index < count_limit:
            page_size = min(100, count_limit - start_index)
            batch = await self._request_json(
                base_url,
                params={
                    "startTime": int(start.timestamp()),
                    "endTime": int(end.timestamp()),
                    "start": start_index,
                    "count": page_size,
                },
            )
            if not batch:
                break
            match_ids.extend(batch)
            if len(batch) < page_size:
                break
            start_index += page_size
        return match_ids

    async def fetch_match(self, match_id: str) -> dict[str, Any]:
        url = f"https://{self.account_region}.api.riotgames.com/lol/match/v5/matches/{quote(match_id)}"
        return await self._request_json(url)

    async def aggregate_player_stats(
        self,
        puuid: str,
        start: datetime,
        end: datetime,
        queue_key: str,
    ) -> PlayerStats:
        allowed_queue_ids = QUEUE_FILTERS[queue_key]["queue_ids"]
        stats = PlayerStats()
        for match_id in await self.fetch_match_ids(puuid, start, end):
            payload = await self.fetch_match(match_id)
            await asyncio.sleep(0.05)  # ~20 req/sec to stay within rate limits
            info = payload.get("info", {})
            if allowed_queue_ids and info.get("queueId") not in allowed_queue_ids:
                continue
            participant = next(
                (entry for entry in info.get("participants", []) if entry.get("puuid") == puuid),
                None,
            )
            if participant:
                stats.merge(participant)
        return stats
