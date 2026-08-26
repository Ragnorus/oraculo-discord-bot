from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from .models import PlayerStats, QUEUE_FILTERS
from .storage import Storage


LOGGER = logging.getLogger(__name__)


class RiotAPIError(RuntimeError):
    pass


@dataclass(slots=True)
class RiotAccount:
    puuid: str
    game_name: str
    tag_line: str


class RiotAPIClient:
    def __init__(
        self,
        api_key: str,
        account_region: str,
        platform_region: str,
        timeout_seconds: int = 20,
        storage: Storage | None = None,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self.api_key = api_key
        self.account_region = account_region
        self.platform_region = platform_region
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None
        self.storage = storage
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache_locks: dict[str, asyncio.Lock] = {}

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
        # Riot IDs are shown as "name#tag" in-game, so users often paste the tag with its leading "#".
        game_name = game_name.strip().lstrip("#").strip()
        tag_line = tag_line.strip().lstrip("#").strip()
        if not game_name or not tag_line:
            raise RiotAPIError("Riot ID must include both a game name and a tag line, e.g. Player#TAG.")
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

    def _cache_key(self, puuid: str, start: datetime) -> str:
        return f"{self.account_region}:{puuid}:{start.astimezone(UTC).isoformat()}"

    def _cache_is_fresh(self, cached: dict[str, Any], now: datetime) -> bool:
        polled_at = cached.get("polled_at")
        if not polled_at:
            return False
        return (now - datetime.fromisoformat(polled_at).astimezone(UTC)).total_seconds() < self.cache_ttl_seconds

    @staticmethod
    def _match_timestamp(payload: dict[str, Any]) -> int:
        info = payload.get("info", {})
        return int(info.get("gameEndTimestamp") or (info.get("gameCreation", 0) + info.get("gameDuration", 0) * 1000))

    async def _get_match_payloads(self, puuid: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        if self.storage is None:
            return [await self.fetch_match(match_id) for match_id in await self.fetch_match_ids(puuid, start, end)]

        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        key = self._cache_key(puuid, start)
        cached = self.storage.get_riot_cache(key)
        now = datetime.now(UTC)
        if cached and self._cache_is_fresh(cached, now):
            LOGGER.info("Riot cache hit for %s", puuid)
            return self._filter_match_payloads(cached.get("matches", {}).values(), start, end)

        lock = self._cache_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self.storage.get_riot_cache(key) or {"match_ids": [], "matches": {}}
            now = datetime.now(UTC)
            if self._cache_is_fresh(cached, now):
                LOGGER.info("Riot cache hit after waiting for %s", puuid)
                return self._filter_match_payloads(cached.get("matches", {}).values(), start, end)

            polled_ids = await self.fetch_match_ids(puuid, start, end)
            known_ids = set(cached.get("match_ids", []))
            match_payloads = dict(cached.get("matches", {}))
            missing_ids = [match_id for match_id in polled_ids if match_id not in match_payloads]
            for match_id in missing_ids:
                match_payloads[match_id] = await self.fetch_match(match_id)
                await asyncio.sleep(0.05)
            cached = {
                "polled_at": now.isoformat(),
                "match_ids": sorted(known_ids | set(polled_ids)),
                "matches": match_payloads,
            }
            self.storage.set_riot_cache(key, cached)
            LOGGER.info("Riot cache refreshed for %s: %d new matches", puuid, len(missing_ids))
            return self._filter_match_payloads(match_payloads.values(), start, end)

    @classmethod
    def _filter_match_payloads(
        cls,
        payloads: Any,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        return [payload for payload in payloads if start_ms <= cls._match_timestamp(payload) <= end_ms]

    async def aggregate_player_stats(
        self,
        puuid: str,
        start: datetime,
        end: datetime,
        queue_key: str,
    ) -> PlayerStats:
        allowed_queue_ids = QUEUE_FILTERS[queue_key]["queue_ids"]
        stats = PlayerStats()
        for payload in await self._get_match_payloads(puuid, start, end):
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

    async def aggregate_stats_at_checkpoints(
        self,
        puuid: str,
        start: datetime,
        end: datetime,
        checkpoints: list[datetime],
        queue_key: str,
    ) -> list[PlayerStats]:
        """Fetch all matches once, then return cumulative PlayerStats at each checkpoint."""
        allowed_queue_ids = QUEUE_FILTERS[queue_key]["queue_ids"]
        payloads = await self._get_match_payloads(puuid, start, end)

        # Collect (end_timestamp_ms, participant) for qualifying matches
        timestamped: list[tuple[int, dict]] = []
        for payload in payloads:
            await asyncio.sleep(0.05)  # ~20 req/sec to stay within rate limits
            info = payload.get("info", {})
            if allowed_queue_ids and info.get("queueId") not in allowed_queue_ids:
                continue
            participant = next(
                (e for e in info.get("participants", []) if e.get("puuid") == puuid),
                None,
            )
            if participant is None:
                continue
            game_end_ms: int = info.get("gameEndTimestamp") or (
                info.get("gameCreation", 0) + info.get("gameDuration", 0) * 1000
            )
            timestamped.append((game_end_ms, participant))

        timestamped.sort(key=lambda x: x[0])

        result: list[PlayerStats] = []
        for checkpoint in checkpoints:
            cutoff_ms = int(checkpoint.astimezone(UTC).timestamp() * 1000)
            stats = PlayerStats()
            for game_end_ms, participant in timestamped:
                if game_end_ms <= cutoff_ms:
                    stats.merge(participant)
            result.append(stats)
        return result

