#!/usr/bin/env python3
"""
Leaderboard command for the MeshCore Bot
Shows the farthest path-command distance for the current week and of all time.
A weekly Sunday-midnight job announces the weekly winner before the new week begins.
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional

from ..models import MeshMessage
from .base_command import BaseCommand


class LeaderboardCommand(BaseCommand):
    """Shows path distance leaderboard (weekly + all-time)."""

    name = "leaderboard"
    keywords = ["leaderboard", "lb"]
    description = "Show the farthest path-command distances for this week and all time"
    category = "info"

    short_description = "Path distance leaderboard"
    usage = "leaderboard"
    examples = ["leaderboard", "lb"]

    def __init__(self, bot):
        super().__init__(bot)
        self.lb_enabled = self.get_config_value(
            'Leaderboard_Command', 'enabled', fallback=True, value_type='bool'
        )
        self._register_weekly_job()

    def can_execute(self, message: MeshMessage, skip_channel_check: bool = False) -> bool:
        if not self.lb_enabled:
            return False
        return super().can_execute(message, skip_channel_check)

    def get_help_text(self) -> str:
        return self.translate('commands.leaderboard.description')

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _last_sunday_midnight(self) -> int:
        """Return Unix timestamp of the most recent Sunday 00:00:00 local time."""
        now = datetime.now()
        days_since_sunday = (now.weekday() + 1) % 7
        last_sunday = now - timedelta(days=days_since_sunday)
        midnight = last_sunday.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(midnight.timestamp())

    def _query_weekly_best(self) -> Optional[dict]:
        rows = self.bot.db_manager.execute_query(
            """
            SELECT sender_name, distance_km
            FROM path_distance_records
            WHERE recorded_at >= ?
            ORDER BY distance_km DESC
            LIMIT 1
            """,
            (self._last_sunday_midnight(),),
        )
        return rows[0] if rows else None

    def _query_alltime_best(self) -> Optional[dict]:
        rows = self.bot.db_manager.execute_query(
            """
            SELECT sender_name, distance_km
            FROM path_distance_records
            ORDER BY distance_km DESC
            LIMIT 1
            """
        )
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Response formatting
    # ------------------------------------------------------------------

    def _format_response(self, weekly: Optional[dict], alltime: Optional[dict]) -> str:
        lines = []

        if weekly:
            name = weekly['sender_name'] or '?'
            km = weekly['distance_km']
            lines.append(self.translate('commands.leaderboard.weekly_best', km=km, name=name))
        else:
            lines.append(self.translate('commands.leaderboard.no_weekly_records'))

        if alltime:
            name = alltime['sender_name'] or '?'
            km = alltime['distance_km']
            lines.append(self.translate('commands.leaderboard.alltime_best', km=km, name=name))
        else:
            lines.append(self.translate('commands.leaderboard.no_records'))

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def execute(self, message: MeshMessage) -> bool:
        weekly = self._query_weekly_best()
        alltime = self._query_alltime_best()
        response = self._format_response(weekly, alltime)
        await self.send_response(message, response)
        return True

    # ------------------------------------------------------------------
    # Weekly announcement
    # ------------------------------------------------------------------

    async def _announce_weekly_result(self) -> None:
        """Send the weekly summary to all monitored channels."""
        weekly = self._query_weekly_best()
        alltime = self._query_alltime_best()
        if not weekly and not alltime:
            return

        message = self._format_response(weekly, alltime)

        raw = self.bot.config.get('Channels', 'monitor_channels', fallback='')
        channels = [c.strip() for c in raw.split(',') if c.strip()]
        for channel in channels:
            try:
                await self.bot.command_manager.send_channel_message(
                    channel, message, skip_user_rate_limit=True
                )
            except Exception as e:
                self.logger.error(f"Leaderboard weekly announcement failed for {channel}: {e}")

    def _run_weekly_announcement(self) -> None:
        """Sync wrapper called by APScheduler thread."""
        if hasattr(self.bot, 'main_event_loop') and self.bot.main_event_loop and self.bot.main_event_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._announce_weekly_result(), self.bot.main_event_loop
            )
            try:
                future.result(timeout=60)
            except Exception as e:
                self.logger.error(f"Leaderboard weekly announcement error: {e}")
        else:
            self.logger.warning("Leaderboard weekly announcement skipped — no running event loop")

    def _register_weekly_job(self) -> None:
        """Register a Sunday midnight APScheduler job for the weekly announcement."""
        try:
            scheduler = getattr(self.bot, 'message_scheduler', None)
            if scheduler is None:
                return
            apscheduler = getattr(scheduler, '_apscheduler', None)
            if apscheduler is None:
                return

            from apscheduler.triggers.cron import CronTrigger

            apscheduler.add_job(
                self._run_weekly_announcement,
                CronTrigger(day_of_week='sun', hour=0, minute=0),
                id='leaderboard_weekly_announcement',
                replace_existing=True,
            )
            self.logger.info("Leaderboard: weekly Sunday midnight announcement job registered")
        except Exception as e:
            self.logger.warning(f"Leaderboard: could not register weekly job: {e}")
