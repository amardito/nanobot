from __future__ import annotations

from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.schema import StringSchema, tool_parameters_schema

# Global reference to the voice manager, set by DiscordChannel on startup
_voice_manager: Any = None


def set_voice_manager(manager: Any) -> None:
    global _voice_manager
    _voice_manager = manager


def get_voice_manager() -> Any:
    return _voice_manager


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "Action to perform: 'play' to play a YouTube URL, 'stop' to stop playback, 'skip' to skip current track.",
            enum=["play", "stop", "skip"],
        ),
        url=StringSchema(
            "YouTube URL to play audio from. Required when action is 'play'.",
        ),
    )
)
class NanoPlayTool(Tool):
    """Tool to play YouTube audio in Discord voice channels."""

    name = "nanoplay"
    description = (
        "Play YouTube audio in the voice channel you're currently in. "
        "Use action='play' with a YouTube URL to play audio. "
        "Use action='stop' to stop playback and clear the queue. "
        "Use action='skip' to skip the current track."
    )

    def __init__(self, guild_id: str | None = None):
        self._guild_id = guild_id

    @classmethod
    def create(cls, ctx: Any = None) -> "NanoPlayTool":
        guild_id = getattr(ctx, "metadata", {}).get("guild_id") if ctx else None
        return cls(guild_id=guild_id)

    async def execute(self, action: str, url: str = "") -> ToolResult:
        manager = get_voice_manager()
        if manager is None:
            return ToolResult.error("Voice manager not initialized.")

        if self._guild_id is None:
            return ToolResult.error(
                "Cannot determine which voice channel you are in. "
                "Please send this command from a Discord channel where you are in a voice chat."
            )

        guild_id = int(self._guild_id)

        if action == "play":
            if not url:
                return ToolResult.error("URL is required for 'play' action.")
            success = await manager.play_audio(guild_id, url)
            if success:
                return ToolResult("Queued for playback.")
            else:
                return ToolResult.error("Failed to queue audio for playback.")

        elif action == "stop":
            await manager.stop_playback(guild_id)
            return ToolResult("Playback stopped and queue cleared.")

        elif action == "skip":
            skipped = await manager.skip_audio(guild_id)
            if skipped:
                return ToolResult("Skipped current track.")
            else:
                return ToolResult("No audio track currently playing to skip.")

        else:
            return ToolResult.error(f"Invalid action: {action}. Use 'play', 'stop', or 'skip'.")
