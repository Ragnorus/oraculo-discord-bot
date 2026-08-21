from __future__ import annotations

from datetime import UTC, datetime
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .config import BotSettings, load_settings
from .leaderboard import LeaderboardService, render_leaderboard
from .models import GuildConfig, LeaderboardEntry, LeaderboardPeriod, QUEUE_FILTERS, RegisteredPlayer
from .riot_api import RiotAPIClient, RiotAPIError
from .storage import Storage


LOGGER = logging.getLogger(__name__)


class CommandInputError(RuntimeError):
    pass


QUEUE_CHOICES = [
    app_commands.Choice(name=payload["label"], value=key)
    for key, payload in QUEUE_FILTERS.items()
]


class OraculoBot(commands.Bot):
    def __init__(self, settings: BotSettings, storage: Storage, riot_client: RiotAPIClient) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.storage = storage
        self.riot_client = riot_client
        self.leaderboard_service = LeaderboardService(riot_client)

    async def setup_hook(self) -> None:
        await self.add_cog(OraculoCog(self))
        self.autopost_loop.change_interval(minutes=self.settings.scheduler_interval_minutes)
        self.autopost_loop.start()
        await self.tree.sync()

    async def close(self) -> None:
        self.autopost_loop.cancel()
        await self.riot_client.close()
        await super().close()

    @tasks.loop(minutes=30)
    async def autopost_loop(self) -> None:
        await self.wait_until_ready()
        now = datetime.now(UTC)
        for guild in self.guilds:
            config = self.storage.get_guild_config(guild.id)
            if not config.should_post(now):
                continue
            channel = guild.get_channel(config.channel_id) if config.channel_id else None
            if not isinstance(channel, discord.TextChannel):
                continue
            players = self.storage.list_registrations(guild.id)
            if not players:
                continue
            start, end = config.autopost_period.completed_window(now)
            try:
                entries = await self.leaderboard_service.build(players, config.default_queue, start, end)
            except RiotAPIError as error:
                LOGGER.warning("Failed to autopost leaderboard for guild %s: %s", guild.id, error)
                continue
            title = f"{guild.name} leaderboard"
            label = f"{start.date().isoformat()} to {end.date().isoformat()}"
            await channel.send(render_leaderboard(entries, title, config.default_queue, label))
            config.last_posted_at = now
            self.storage.upsert_guild_config(config)


class OraculoCog(commands.Cog):
    def __init__(self, bot: OraculoBot) -> None:
        self.bot = bot

    leaderboard_group = app_commands.Group(name="leaderboard", description="Manage and view server leaderboards")

    @app_commands.command(name="profile", description="Show your current leaderboard-period stats")
    @app_commands.guild_only()
    @app_commands.describe(
        game_name="Optional Riot game name override",
        tag_line="Optional Riot tag line override",
        queue="Queue filter to use",
        period="daily, weekly, or yearly",
    )
    @app_commands.choices(queue=QUEUE_CHOICES)
    @app_commands.choices(
        period=[
            app_commands.Choice(name="Daily", value=LeaderboardPeriod.DAILY.value),
            app_commands.Choice(name="Weekly", value=LeaderboardPeriod.WEEKLY.value),
            app_commands.Choice(name="Yearly", value=LeaderboardPeriod.YEARLY.value),
        ]
    )
    async def profile(
        self,
        interaction: discord.Interaction,
        game_name: str | None = None,
        tag_line: str | None = None,
        queue: str = "all",
        period: str = LeaderboardPeriod.WEEKLY.value,
    ) -> None:
        assert interaction.guild_id is not None
        await interaction.response.defer(thinking=True)
        try:
            player = await self._resolve_player(interaction, game_name, tag_line)
            stats = await self.bot.leaderboard_service.build_for_player(
                player=player,
                queue_key=queue,
                period=LeaderboardPeriod(period),
                now=datetime.now(UTC),
            )
        except (CommandInputError, RiotAPIError) as error:
            await interaction.followup.send(str(error))
            return
        label = LeaderboardPeriod(period).value
        message = render_leaderboard(
            [LeaderboardEntry(player=player, stats=stats)],
            f"{player.riot_id} profile",
            queue,
            label,
        )
        await interaction.followup.send(message)

    @leaderboard_group.command(name="join", description="Register your Riot account for this server leaderboard")
    @app_commands.guild_only()
    async def leaderboard_join(self, interaction: discord.Interaction, game_name: str, tag_line: str) -> None:
        assert interaction.guild_id is not None
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            account = await self.bot.riot_client.resolve_account(game_name, tag_line)
        except RiotAPIError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return

        player = RegisteredPlayer(
            discord_user_id=interaction.user.id,
            game_name=account.game_name,
            tag_line=account.tag_line,
            puuid=account.puuid,
        )
        self.bot.storage.set_registration(interaction.guild_id, player)
        await interaction.followup.send(f"Registered **{player.riot_id}** for this server.", ephemeral=True)

    @leaderboard_group.command(name="leave", description="Remove your Riot account from this server leaderboard")
    @app_commands.guild_only()
    async def leaderboard_leave(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None
        removed = self.bot.storage.remove_registration(interaction.guild_id, interaction.user.id)
        if removed:
            await interaction.response.send_message("Your leaderboard registration was removed.", ephemeral=True)
            return
        await interaction.response.send_message("You are not registered in this server leaderboard.", ephemeral=True)

    @leaderboard_group.command(name="show", description="Show the current server leaderboard")
    @app_commands.guild_only()
    @app_commands.choices(queue=QUEUE_CHOICES)
    @app_commands.choices(
        period=[
            app_commands.Choice(name="Daily", value=LeaderboardPeriod.DAILY.value),
            app_commands.Choice(name="Weekly", value=LeaderboardPeriod.WEEKLY.value),
            app_commands.Choice(name="Yearly", value=LeaderboardPeriod.YEARLY.value),
        ]
    )
    async def leaderboard_show(
        self,
        interaction: discord.Interaction,
        queue: str = "all",
        period: str = LeaderboardPeriod.WEEKLY.value,
    ) -> None:
        assert interaction.guild_id is not None
        await interaction.response.defer(thinking=True)
        players = self.bot.storage.list_registrations(interaction.guild_id)
        if not players:
            await interaction.followup.send("No players are registered in this server leaderboard yet.")
            return
        selected_period = LeaderboardPeriod(period)
        start, end = selected_period.current_window(datetime.now(UTC))
        try:
            entries = await self.bot.leaderboard_service.build(players, queue, start, end)
        except RiotAPIError as error:
            await interaction.followup.send(str(error))
            return
        await interaction.followup.send(
            render_leaderboard(entries, f"{interaction.guild.name} leaderboard", queue, selected_period.value)
        )

    @leaderboard_group.command(name="config", description="Configure automatic leaderboard posts")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(queue=QUEUE_CHOICES)
    @app_commands.choices(
        period=[
            app_commands.Choice(name="Daily", value=LeaderboardPeriod.DAILY.value),
            app_commands.Choice(name="Weekly", value=LeaderboardPeriod.WEEKLY.value),
            app_commands.Choice(name="Yearly", value=LeaderboardPeriod.YEARLY.value),
        ]
    )
    async def leaderboard_config(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        queue: str = "all",
        period: str = LeaderboardPeriod.WEEKLY.value,
    ) -> None:
        assert interaction.guild_id is not None
        current = self.bot.storage.get_guild_config(interaction.guild_id)
        selected_channel_id = channel.id if channel else current.channel_id or interaction.channel_id
        config = GuildConfig(
            guild_id=interaction.guild_id,
            channel_id=selected_channel_id,
            autopost_period=LeaderboardPeriod(period),
            default_queue=queue,
            last_posted_at=current.last_posted_at,
        )
        self.bot.storage.upsert_guild_config(config)
        await interaction.response.send_message(
            f"Auto-posts set to **{config.autopost_period.value}** in "
            f"<#{config.channel_id}> for **{QUEUE_FILTERS[config.default_queue]['label']}**.",
            ephemeral=True,
        )

    @leaderboard_config.error
    async def leaderboard_config_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need Manage Server to change leaderboard settings.", ephemeral=True)
            return
        raise error

    async def _resolve_player(
        self,
        interaction: discord.Interaction,
        game_name: str | None,
        tag_line: str | None,
    ) -> RegisteredPlayer:
        assert interaction.guild_id is not None
        if bool(game_name) ^ bool(tag_line):
            raise CommandInputError("Provide both game_name and tag_line together, or omit both.")
        if game_name and tag_line:
            account = await self.bot.riot_client.resolve_account(game_name, tag_line)
            return RegisteredPlayer(
                discord_user_id=interaction.user.id,
                game_name=account.game_name,
                tag_line=account.tag_line,
                puuid=account.puuid,
            )
        stored = self.bot.storage.get_registration(interaction.guild_id, interaction.user.id)
        if stored:
            return stored
        raise CommandInputError("No default Riot account is registered. Use /leaderboard join or pass game_name and tag_line.")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    bot = OraculoBot(
        settings=settings,
        storage=Storage(settings.data_path),
        riot_client=RiotAPIClient(
            api_key=settings.riot_api_key,
            account_region=settings.riot_account_region,
            platform_region=settings.riot_platform_region,
        ),
    )
    bot.run(settings.discord_token)
