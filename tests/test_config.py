from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from oraculo_bot.config import load_settings


class ConfigTest(unittest.TestCase):
    def test_load_settings_normalizes_riot_api_key(self) -> None:
        env = {
            "DISCORD_BOT_TOKEN": "discord-token",
            "RIOT_API_KEY": ' "abc123" ',
        }

        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()

        self.assertEqual("abc123", settings.riot_api_key)

    def test_load_settings_removes_authorization_prefix(self) -> None:
        prefix = "Bearer"
        env = {
            "DISCORD_BOT_TOKEN": "discord-token",
            "RIOT_API_KEY": f"{prefix} abc123",
        }

        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()

        self.assertEqual("abc123", settings.riot_api_key)

    def test_load_settings_requires_riot_api_key_after_normalization(self) -> None:
        env = {
            "DISCORD_BOT_TOKEN": "discord-token",
            "RIOT_API_KEY": ' "   " ',
        }

        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "RIOT_API_KEY is required"):
                load_settings()


if __name__ == "__main__":
    unittest.main()
