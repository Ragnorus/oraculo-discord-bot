from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .chart_server import ChartServer
from .config import BotSettings, load_settings
from .gif_renderer import render_race_gif
from .leaderboard import LeaderboardService, render_leaderboard
from .models import GuildConfig, LeaderboardEntry, LeaderboardPeriod, QUEUE_FILTERS, RegisteredPlayer
from .riot_api import RiotAPIClient, RiotAPIError
from .storage import Storage
from .visualize_chart import build_race_payload, compute_checkpoints, generate_chart_html, performance_score


LOGGER = logging.getLogger(__name__)


class CommandInputError(RuntimeError):
    pass


QUEUE_CHOICES = [
    app_commands.Choice(name=payload["label"], value=key)
    for key, payload in QUEUE_FILTERS.items()
]


class OraculoBot(commands.Bot):
    def __init__(self, settings: BotSettings, storage: Storage, riot_client: RiotAPIClient, chart_server: ChartServer) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.storage = storage
        self.riot_client = riot_client
        self.leaderboard_service = LeaderboardService(riot_client)
        self.chart_server = chart_server

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

    @app_commands.command(name="help", description="Show how to use Oráculo")
    async def help_command(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="Oráculo — Bot Guide", color=0x7B56F3)
        embed.add_field(name="Registration", value=(
            "`/leaderboard join game_name tag_line` — Register your Riot account\n"
            "`/leaderboard leave` — Remove your registration\n"
            "`/leaderboard add @member game_name tag_line` — Register someone *(admin)*\n"
            "`/leaderboard remove @member` — Remove someone *(admin)*"
        ), inline=False)
        embed.add_field(name="Viewing Stats", value=(
            "`/profile` — Your stats for the current period\n"
            "`/profile game_name tag_line` — Stats for any Riot account\n"
            "`/leaderboard show` — Full server leaderboard\n"
            "`/leaderboard show queue:aram period:Weekly` — Filtered view"
        ), inline=False)
        embed.add_field(name="Admin", value=(
            "`/leaderboard config` — Set auto-post channel, queue, and period\n"
            "`/leaderboard list` — View all registered members\n"
            "*Requires Manage Server permission.*"
        ), inline=False)
        embed.add_field(name="Queue Options", value="All · ARAM · Normal · Ranked Solo/Duo · Ranked Flex · Arena · URF", inline=False)
        embed.add_field(name="Period Options", value="Daily · Weekly · Yearly", inline=False)
        embed.set_footer(text="game_name#tag_line is your Riot ID shown in the Riot client")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="race", description="Animated bar chart race of player performance scores")
    @app_commands.guild_only()
    @app_commands.choices(queue=QUEUE_CHOICES)
    @app_commands.choices(
        period=[
            app_commands.Choice(name="Weekly", value="weekly"),
            app_commands.Choice(name="Monthly", value="monthly"),
            app_commands.Choice(name="Yearly", value="yearly"),
        ]
    )
    async def race_command(
        self,
        interaction: discord.Interaction,
        queue: str = "all",
        period: str = "weekly",
    ) -> None:
        assert interaction.guild_id is not None
        if not self.bot.settings.public_url:
            await interaction.response.send_message(
                "The `ORACULO_PUBLIC_URL` environment variable is not set. "
                "Ask the bot admin to add it in Railway → Variables (e.g. `https://your-app.up.railway.app`).",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True)
        players = self.bot.storage.list_registrations(interaction.guild_id)
        if not players:
            await interaction.followup.send("No players are registered in this server leaderboard yet.")
            return
        period_enum = LeaderboardPeriod(period)
        now = datetime.now(UTC)
        start, end = period_enum.current_window(now)
        checkpoints = compute_checkpoints(start, end, period)
        semaphore = asyncio.Semaphore(4)

        async def _scores(player: RegisteredPlayer) -> tuple[str, list[tuple[datetime, float]]]:
            async with semaphore:
                stats_list = await self.bot.riot_client.aggregate_stats_at_checkpoints(
                    player.puuid, start, end, checkpoints, queue
                )
                return player.riot_id, [(checkpoints[i], performance_score(s)) for i, s in enumerate(stats_list)]

        results = await asyncio.gather(*(_scores(p) for p in players), return_exceptions=True)
        player_scores: dict[str, list[tuple[datetime, float]]] = {}
        for result in results:
            if isinstance(result, Exception):
                LOGGER.warning("Race fetch error: %s", result)
                continue
            name, scores = result
            if any(v > 0 for _, v in scores):
                player_scores[name] = scores

        if not player_scores:
            await interaction.followup.send("No match data found for the selected period and queue.")
            return

        payload = build_race_payload(
            player_scores,
            f"{interaction.guild.name} Performance Race",
            QUEUE_FILTERS[queue]["label"],
            period,
        )
        token = self.bot.chart_server.store_chart(generate_chart_html(payload))
        url = f"{self.bot.settings.public_url.rstrip('/')}/chart/{token}"
        message = (
            f"📊 **{interaction.guild.name} Performance Race** · {QUEUE_FILTERS[queue]['label']} · {period}\n"
            f"{url}\n*Interactive link expires in 1 hour.*"
        )
        try:
            gif_bytes = await asyncio.to_thread(render_race_gif, payload)
            if len(gif_bytes) > 8_000_000:
                raise ValueError("Generated GIF is too large to upload.")
            await interaction.followup.send(
                message,
                file=discord.File(io.BytesIO(gif_bytes), filename="performance-race.gif"),
            )
        except (OSError, ValueError) as error:
            LOGGER.warning("Race GIF generation failed: %s", error)
            await interaction.followup.send(message + "\n*GIF preview was unavailable; use the interactive link.*")

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

    @leaderboard_group.command(name="add", description="Register another member's Riot account (admin only)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def leaderboard_add(self, interaction: discord.Interaction, member: discord.Member, game_name: str, tag_line: str) -> None:
        assert interaction.guild_id is not None
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            account = await self.bot.riot_client.resolve_account(game_name, tag_line)
        except RiotAPIError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        player = RegisteredPlayer(
            discord_user_id=member.id,
            game_name=account.game_name,
            tag_line=account.tag_line,
            puuid=account.puuid,
        )
        self.bot.storage.set_registration(interaction.guild_id, player)
        await interaction.followup.send(f"Registered **{player.riot_id}** for {member.mention}.", ephemeral=True)

    @leaderboard_add.error
    async def leaderboard_add_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need Manage Server to add members.", ephemeral=True)
            return
        raise error

    @leaderboard_group.command(name="remove", description="Remove another member from the leaderboard (admin only)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def leaderboard_remove(self, interaction: discord.Interaction, member: discord.Member) -> None:
        assert interaction.guild_id is not None
        removed = self.bot.storage.remove_registration(interaction.guild_id, member.id)
        if removed:
            await interaction.response.send_message(f"Removed {member.mention} from the leaderboard.", ephemeral=True)
            return
        await interaction.response.send_message(f"{member.mention} is not registered in this server leaderboard.", ephemeral=True)

    @leaderboard_remove.error
    async def leaderboard_remove_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need Manage Server to remove members.", ephemeral=True)
            return
        raise error

    @leaderboard_group.command(name="list", description="List all registered members (admin only)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def leaderboard_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None
        players = self.bot.storage.list_registrations(interaction.guild_id)
        if not players:
            await interaction.response.send_message("No players are registered in this server leaderboard yet.", ephemeral=True)
            return
        lines = []
        for player in players:
            member = interaction.guild.get_member(player.discord_user_id) if interaction.guild else None
            mention = member.mention if member else f"<@{player.discord_user_id}>"
            lines.append(f"{mention} — **{player.riot_id}**")
        embed = discord.Embed(
            title=f"{interaction.guild.name} — Registered Players ({len(players)})",
            description="\n".join(lines),
            color=0x7B56F3,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @leaderboard_list.error
    async def leaderboard_list_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need Manage Server to list members.", ephemeral=True)
            return
        raise error

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
    chart_server = ChartServer(settings.chart_port)
    storage = Storage(settings.data_path)
    bot = OraculoBot(
        settings=settings,
        storage=storage,
        riot_client=RiotAPIClient(
            api_key=settings.riot_api_key,
            account_region=settings.riot_account_region,
            platform_region=settings.riot_platform_region,
            storage=storage,
            cache_ttl_seconds=settings.riot_cache_ttl_seconds,
        ),
        chart_server=chart_server,
    )

    async def _run_all() -> None:
        await chart_server.start()
        try:
            async with bot:
                await bot.start(settings.discord_token)
        finally:
            await chart_server.stop()

    try:
        asyncio.run(_run_all())
    except KeyboardInterrupt:
        pass
