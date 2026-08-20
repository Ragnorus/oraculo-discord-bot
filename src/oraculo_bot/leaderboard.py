from __future__ import annotations

import asyncio
from datetime import datetime

from .models import LeaderboardEntry, LeaderboardPeriod, PlayerStats, QUEUE_FILTERS, RegisteredPlayer
from .riot_api import RiotAPIClient


def sort_key(entry: LeaderboardEntry) -> tuple[int, int, int, int]:
    return (
        entry.stats.wins,
        entry.stats.kills + entry.stats.assists,
        entry.stats.total_damage,
        entry.stats.games,
    )


class LeaderboardService:
    def __init__(self, riot_client: RiotAPIClient) -> None:
        self.riot_client = riot_client

    async def build(
        self,
        players: list[RegisteredPlayer],
        queue_key: str,
        start: datetime,
        end: datetime,
    ) -> list[LeaderboardEntry]:
        semaphore = asyncio.Semaphore(4)

        async def compute(player: RegisteredPlayer) -> LeaderboardEntry:
            async with semaphore:
                stats = await self.riot_client.aggregate_player_stats(player.puuid, start, end, queue_key)
                return LeaderboardEntry(player=player, stats=stats)

        entries = await asyncio.gather(*(compute(player) for player in players))
        return sorted([entry for entry in entries if entry.stats.games > 0], key=sort_key, reverse=True)

    async def build_for_player(
        self,
        player: RegisteredPlayer,
        queue_key: str,
        period: LeaderboardPeriod,
        now: datetime,
    ) -> PlayerStats:
        start, end = period.current_window(now)
        return await self.riot_client.aggregate_player_stats(player.puuid, start, end, queue_key)


def render_leaderboard(entries: list[LeaderboardEntry], title: str, queue_key: str, period_label: str) -> str:
    if not entries:
        return f"**{title}**\nNo matches found for {QUEUE_FILTERS[queue_key]['label']} during {period_label}."

    lines = [f"**{title}**", f"Queue: {QUEUE_FILTERS[queue_key]['label']}", f"Period: {period_label}", ""]
    for index, entry in enumerate(entries, start=1):
        stats = entry.stats
        lines.append(
            f"{index}. **{entry.player.riot_id}** — "
            f"{stats.wins}/{stats.games} wins, "
            f"{stats.kills}/{stats.deaths}/{stats.assists} KDA, "
            f"{stats.total_damage} dmg, {stats.gold_earned} gold, {stats.minions_killed} CS"
        )
    return "\n".join(lines)
