from __future__ import annotations

from datetime import UTC, datetime
import unittest

from oraculo_bot.leaderboard import LeaderboardService
from oraculo_bot.models import LeaderboardPeriod, PlayerStats, RegisteredPlayer


class FakeRiotClient:
    def __init__(self, payloads: dict[tuple[str, str], PlayerStats]) -> None:
        self.payloads = payloads

    async def aggregate_player_stats(self, puuid: str, start: datetime, end: datetime, queue_key: str) -> PlayerStats:
        return self.payloads[(puuid, queue_key)]


class LeaderboardServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_build_sorts_entries_and_skips_empty_players(self) -> None:
        players = [
            RegisteredPlayer(discord_user_id=1, game_name="A", tag_line="EUW", puuid="1"),
            RegisteredPlayer(discord_user_id=2, game_name="B", tag_line="EUW", puuid="2"),
            RegisteredPlayer(discord_user_id=3, game_name="C", tag_line="EUW", puuid="3"),
        ]
        service = LeaderboardService(
            FakeRiotClient(
                {
                    ("1", "aram"): PlayerStats(games=5, wins=4, kills=40, assists=20),
                    ("2", "aram"): PlayerStats(games=8, wins=4, kills=30, assists=10),
                    ("3", "aram"): PlayerStats(),
                }
            )
        )

        entries = await service.build(
            players=players,
            queue_key="aram",
            start=datetime(2026, 8, 1, tzinfo=UTC),
            end=datetime(2026, 8, 20, tzinfo=UTC),
        )

        self.assertEqual(["A#EUW", "B#EUW"], [entry.player.riot_id for entry in entries])

    async def test_build_for_player_uses_current_period_window(self) -> None:
        player = RegisteredPlayer(discord_user_id=1, game_name="A", tag_line="EUW", puuid="1")
        expected = PlayerStats(games=3, wins=2)
        client = FakeRiotClient({("1", "all"): expected})
        service = LeaderboardService(client)

        result = await service.build_for_player(
            player=player,
            queue_key="all",
            period=LeaderboardPeriod.WEEKLY,
            now=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        )

        self.assertIs(result, expected)
