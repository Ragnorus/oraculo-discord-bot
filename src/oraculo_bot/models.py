from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class LeaderboardPeriod(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"

    def current_window(self, now: datetime) -> tuple[datetime, datetime]:
        now = ensure_utc(now)
        if self is LeaderboardPeriod.DAILY:
            start = datetime(now.year, now.month, now.day, tzinfo=UTC)
        elif self is LeaderboardPeriod.WEEKLY:
            start = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=now.weekday())
        elif self is LeaderboardPeriod.MONTHLY:
            start = datetime(now.year, now.month, 1, tzinfo=UTC)
        else:
            start = datetime(now.year, 1, 1, tzinfo=UTC)
        return start, now

    def completed_window(self, now: datetime) -> tuple[datetime, datetime]:
        start, _ = self.current_window(now)
        if self is LeaderboardPeriod.DAILY:
            previous_start = start - timedelta(days=1)
        elif self is LeaderboardPeriod.WEEKLY:
            previous_start = start - timedelta(days=7)
        elif self is LeaderboardPeriod.MONTHLY:
            previous_start = (
                datetime(start.year - 1, 12, 1, tzinfo=UTC)
                if start.month == 1
                else datetime(start.year, start.month - 1, 1, tzinfo=UTC)
            )
        else:
            previous_start = datetime(start.year - 1, 1, 1, tzinfo=UTC)
        return previous_start, start


@dataclass(slots=True)
class RegisteredPlayer:
    discord_user_id: int
    game_name: str
    tag_line: str
    puuid: str

    @property
    def riot_id(self) -> str:
        return f"{self.game_name}#{self.tag_line}"


@dataclass(slots=True)
class GuildConfig:
    guild_id: int
    channel_id: int | None = None
    autopost_period: LeaderboardPeriod = LeaderboardPeriod.WEEKLY
    default_queue: str = "all"
    last_posted_at: datetime | None = None

    def should_post(self, now: datetime) -> bool:
        _, current_period_start = self.autopost_period.completed_window(now)
        if self.channel_id is None:
            return False
        if self.last_posted_at is None:
            return True
        return ensure_utc(self.last_posted_at) < current_period_start


@dataclass(slots=True)
class PlayerStats:
    games: int = 0
    wins: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    total_damage: int = 0
    gold_earned: int = 0
    minions_killed: int = 0

    def merge(self, payload: dict[str, Any]) -> None:
        self.games += 1
        self.wins += 1 if payload.get("win") else 0
        self.kills += int(payload.get("kills", 0))
        self.deaths += int(payload.get("deaths", 0))
        self.assists += int(payload.get("assists", 0))
        self.total_damage += int(payload.get("totalDamageDealtToChampions", 0))
        self.gold_earned += int(payload.get("goldEarned", 0))
        self.minions_killed += int(payload.get("totalMinionsKilled", 0)) + int(payload.get("neutralMinionsKilled", 0))

    @property
    def kda(self) -> float:
        return (self.kills + self.assists) / max(self.deaths, 1)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LeaderboardEntry:
    player: RegisteredPlayer
    stats: PlayerStats = field(default_factory=PlayerStats)


QUEUE_FILTERS: dict[str, dict[str, Any]] = {
    "all": {"label": "All queues", "queue_ids": None},
    "aram": {"label": "ARAM", "queue_ids": {450}},
    "normal": {"label": "Normal Summoner's Rift", "queue_ids": {400, 430}},
    "ranked_solo": {"label": "Ranked Solo/Duo", "queue_ids": {420}},
    "ranked_flex": {"label": "Ranked Flex", "queue_ids": {440}},
    "arena": {"label": "Arena", "queue_ids": {1700, 1710}},
    "urf": {"label": "URF", "queue_ids": {76, 900}},
}
