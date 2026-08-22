# oraculo-discord-bot

Oráculo is a Python Discord bot that pulls player and match data from the Riot API and turns it into on-demand and scheduled server leaderboards.

## Features

- `/profile` shows aggregated stats for a Riot account during the active leaderboard period
- `/leaderboard join` registers a Riot account for a guild leaderboard
- `/leaderboard leave` removes the registration
- `/leaderboard show` renders the current leaderboard for a supported queue
- `/leaderboard config` lets admins change the auto-post channel, queue, and cadence
- automatic leaderboard posts for daily, weekly, or yearly completed periods
- persistent JSON storage for guild settings and registered accounts

## Supported queue filters

- `all`
- `aram`
- `normal`
- `ranked_solo`
- `ranked_flex`
- `arena`
- `urf`

## Configuration

Set these environment variables before starting the bot:

- `DISCORD_BOT_TOKEN`
- `RIOT_API_KEY` (raw key value only; surrounding quotes and `****** prefix are ignored)
- `RIOT_ACCOUNT_REGION` (default: `americas`)
- `RIOT_PLATFORM_REGION` (default: `na1`)
- `ORACULO_DATA_PATH` (default: `data/oraculo.json`)
- `ORACULO_SCHEDULER_INTERVAL_MINUTES` (default: `30`)
- `ORACULO_RIOT_CACHE_TTL_SECONDS` (default: `300`; minimum: `1`)

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m oraculo_bot
```

## Tests

```bash
python -m unittest discover -s tests
```

## Notes

Discord bots cannot read a member's linked Riot account through the public bot API, so Oráculo uses explicit account registration as the default account source for slash commands and leaderboards.
