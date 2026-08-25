from __future__ import annotations

import asyncio
from datetime import datetime
import logging

from .models import LeaderboardEntry, LeaderboardPeriod, PlayerStats, QUEUE_FILTERS, RegisteredPlayer
from .riot_api import RiotAPIClient, RiotAPIError


LOGGER = logging.getLogger(__name__)


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

        async def compute(player: RegisteredPlayer) -> LeaderboardEntry | RiotAPIError:
            async with semaphore:
                try:
                    stats = await self.riot_client.aggregate_player_stats(player.puuid, start, end, queue_key)
                except RiotAPIError as error:
                    LOGGER.warning("Skipping %s in leaderboard: %s", player.riot_id, error)
                    return error
                return LeaderboardEntry(player=player, stats=stats)

        results = await asyncio.gather(*(compute(player) for player in players))
        entries = [result for result in results if isinstance(result, LeaderboardEntry)]
        errors = [result for result in results if isinstance(result, RiotAPIError)]
        if not entries and errors:
            # every player failed (e.g. an expired/invalid API key) -- surface the real error
            # instead of silently reporting an empty leaderboard.
            raise errors[0]
        return sorted(
            [entry for entry in entries if entry.stats.games > 0],
            key=sort_key,
            reverse=True,
        )

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
    queue_label = QUEUE_FILTERS[queue_key]["label"]
    header = f"**{title}** · {queue_label} · {period_label}"

    if not entries:
        return f"{header}\nNo matches found."

    col_player = max(len(e.player.riot_id) for e in entries)
    col_player = max(col_player, 6)

    # fixed-width table inside a code block for monospace alignment
    head = (
        f"{'#':>2}  {'Player':<{col_player}}  {'W/G':<6}  {'K/D/A':<10}  {'DMG':>7}  {'Gold':>6}  {'CS':>4}"
    )
    sep = "-" * len(head)
    rows = [head, sep]
    for i, entry in enumerate(entries, start=1):
        s = entry.stats
        kda = f"{s.kills}/{s.deaths}/{s.assists}"
        wg = f"{s.wins}/{s.games}"
        rows.append(
            f"{i:>2}  {entry.player.riot_id:<{col_player}}  {wg:<6}  {kda:<10}  {s.total_damage:>7}  {s.gold_earned:>6}  {s.minions_killed:>4}"
        )

    table = "```\n" + "\n".join(rows) + "\n```"
    return f"{header}\n{table}"
