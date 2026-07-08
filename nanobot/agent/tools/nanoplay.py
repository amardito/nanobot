from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ContextAware, RequestContext, current_request_context
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
class NanoPlayTool(Tool, ContextAware):
    """Tool to play YouTube audio in Discord voice channels."""

    name = "nanoplay"
    description = (
        "Play YouTube audio in the voice channel you're currently in. "
        "Use action='play' with a YouTube URL to play audio. "
        "Use action='stop' to stop playback and clear the queue. "
        "Use action='skip' to skip the current track."
    )

    def __init__(self):
        self._request_ctx: ContextVar[RequestContext | None] = ContextVar("nanoplay_ctx", default=None)

    def set_context(self, ctx: RequestContext) -> None:
        self._request_ctx.set(ctx)

    @classmethod
    def create(cls, ctx: Any = None) -> "NanoPlayTool":
        return cls()

    async def execute(self, action: str, url: str = "") -> ToolResult:
        manager = get_voice_manager()
        if manager is None:
            return ToolResult.error("Voice manager not initialized.")

        # Get per-message context to find guild_id and voice_channel_id
        rctx = self._request_ctx.get()
        if rctx is None:
            rctx = current_request_context()

        if rctx is None or rctx.metadata.get("guild_id") is None:
            return ToolResult.error(
                "Cannot determine which server you are in. "
                "Please send this command from a Discord channel."
            )

        guild_id = int(rctx.metadata["guild_id"])
        voice_channel_id = rctx.metadata.get("voice_channel_id")

        # Auto-join the user's voice channel if not already connected
        if guild_id not in manager._voice_clients:
            if not voice_channel_id:
                return ToolResult.error(
                    "You must be in a voice channel to use this command. "
                    "Join a voice channel first, then try again."
                )
            voice_channel = manager._client.get_channel(int(voice_channel_id))
            if voice_channel is None:
                try:
                    voice_channel = await manager._client.fetch_channel(int(voice_channel_id))
                except Exception:
                    return ToolResult.error("Could not find the voice channel.")
            await manager.join_voice_channel(voice_channel)
            if guild_id not in manager._voice_clients:
                return ToolResult.error("Failed to join the voice channel.")

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
