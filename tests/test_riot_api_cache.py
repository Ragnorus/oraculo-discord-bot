from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest

from oraculo_bot.riot_api import RiotAPIClient
from oraculo_bot.storage import Storage


class FakeRiotAPIClient(RiotAPIClient):
    def __init__(self, storage: Storage, cache_ttl_seconds: int = 300) -> None:
        super().__init__("key", "americas", "na1", storage=storage, cache_ttl_seconds=cache_ttl_seconds)
        self.ids = ["match-1"]
        self.fetch_ids_calls = 0
        self.fetch_match_calls: list[str] = []

    async def fetch_match_ids(self, puuid: str, start: datetime, end: datetime) -> list[str]:
        self.fetch_ids_calls += 1
        return self.ids

    async def fetch_match(self, match_id: str) -> dict:
        self.fetch_match_calls.append(match_id)
        return {
            "info": {
                "gameEndTimestamp": 1_700_000_000_000,
                "queueId": 450,
                "participants": [{
                    "puuid": "player-1",
                    "win": True,
                    "kills": 2,
                    "deaths": 1,
                    "assists": 3,
                    "totalDamageDealtToChampions": 1000,
                    "goldEarned": 1000,
                    "totalMinionsKilled": 10,
                    "neutralMinionsKilled": 0,
                }],
            }
        }


class RiotCacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_cache_avoids_riot_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = Storage(Path(tmp_dir) / "oraculo.json")
            client = FakeRiotAPIClient(storage)
            start = datetime(2023, 11, 1, tzinfo=UTC)
            end = datetime(2023, 11, 30, tzinfo=UTC)

            await client.aggregate_player_stats("player-1", start, end, "aram")
            await client.aggregate_player_stats("player-1", start, end, "aram")

            self.assertEqual(1, client.fetch_ids_calls)
            self.assertEqual(["match-1"], client.fetch_match_calls)

    async def test_stale_cache_fetches_only_new_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = Storage(Path(tmp_dir) / "oraculo.json")
            client = FakeRiotAPIClient(storage, cache_ttl_seconds=0)
            start = datetime(2023, 11, 1, tzinfo=UTC)
            end = datetime(2023, 11, 30, tzinfo=UTC)

            await client.aggregate_player_stats("player-1", start, end, "aram")
            client.ids = ["match-1", "match-2"]
            await client.aggregate_player_stats("player-1", start, end, "aram")

            self.assertEqual(2, client.fetch_ids_calls)
            self.assertEqual(["match-1", "match-2"], client.fetch_match_calls)

    def test_cache_survives_storage_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "oraculo.json"
            storage = Storage(path)
            storage.set_riot_cache("americas:player-1:start", {"polled_at": "2026-08-22T00:00:00+00:00"})

            reloaded = Storage(path)
            self.assertEqual(
                "2026-08-22T00:00:00+00:00",
                reloaded.get_riot_cache("americas:player-1:start")["polled_at"],
            )


if __name__ == "__main__":
    unittest.main()