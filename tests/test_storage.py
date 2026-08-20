from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest

from oraculo_bot.models import GuildConfig, LeaderboardPeriod, RegisteredPlayer
from oraculo_bot.storage import Storage


class StorageTest(unittest.TestCase):
    def test_registration_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = Storage(Path(tmp_dir) / "oraculo.json")
            player = RegisteredPlayer(discord_user_id=7, game_name="Player", tag_line="BR1", puuid="puuid-1")

            storage.set_registration(123, player)

            self.assertEqual(player, storage.get_registration(123, 7))
            self.assertEqual([player], storage.list_registrations(123))

    def test_guild_config_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = Storage(Path(tmp_dir) / "oraculo.json")
            config = GuildConfig(
                guild_id=999,
                channel_id=456,
                autopost_period=LeaderboardPeriod.YEARLY,
                default_queue="ranked_solo",
                last_posted_at=datetime(2026, 8, 20, tzinfo=UTC),
            )

            storage.upsert_guild_config(config)
            loaded = storage.get_guild_config(999)

            self.assertEqual(config.guild_id, loaded.guild_id)
            self.assertEqual(config.channel_id, loaded.channel_id)
            self.assertEqual(config.autopost_period, loaded.autopost_period)
            self.assertEqual(config.default_queue, loaded.default_queue)
            self.assertEqual(config.last_posted_at, loaded.last_posted_at)

    def test_remove_registration_returns_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = Storage(Path(tmp_dir) / "oraculo.json")
            player = RegisteredPlayer(discord_user_id=7, game_name="Player", tag_line="BR1", puuid="puuid-1")
            storage.set_registration(123, player)

            self.assertTrue(storage.remove_registration(123, 7))
            self.assertFalse(storage.remove_registration(123, 7))
