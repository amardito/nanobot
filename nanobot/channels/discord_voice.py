from __future__ import annotations

import asyncio
import functools
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import discord
from loguru import logger

from nanobot.config.paths import get_media_dir

# Max audio duration to prevent abuse (e.g. 1 hour)
MAX_AUDIO_DURATION_SECONDS = 3600

# Max concurrent downloads to prevent resource exhaustion
MAX_CONCURRENT_DOWNLOADS = 3


class VoiceManager:
    """Manages Discord voice connections and audio playback."""

    def __init__(self, client: discord.Client):
        self._client = client
        self._voice_clients: dict[int, discord.VoiceClient] = {}
        self._audio_queues: dict[int, asyncio.Queue[str]] = {}
        self._playback_tasks: dict[int, asyncio.Task[None]] = {}
        self._download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        self._media_dir = get_media_dir("discord_voice")
        self._media_dir.mkdir(parents=True, exist_ok=True)

    async def join_voice_channel(
        self, channel: discord.VoiceChannel | discord.StageChannel
    ) -> discord.VoiceClient | None:
        """Joins a voice channel."""
        guild_id = channel.guild.id
        if guild_id in self._voice_clients:
            if self._voice_clients[guild_id].channel.id == channel.id:
                return self._voice_clients[guild_id]
            await self._voice_clients[guild_id].move_to(channel)
            return self._voice_clients[guild_id]

        try:
            voice_client = await channel.connect(timeout=60)
            self._voice_clients[guild_id] = voice_client
            self._audio_queues[guild_id] = asyncio.Queue()
            logger.info("Joined voice channel: {}", channel.name)
            return voice_client
        except asyncio.TimeoutError:
            logger.error("Timed out connecting to voice channel: {}", channel.name)
            return None
        except Exception:
            logger.exception("Failed to join voice channel: {}", channel.name)
            return None

    async def leave_voice_channel(self, guild_id: int) -> None:
        """Leaves a voice channel."""
        if guild_id in self._voice_clients:
            await self._voice_clients[guild_id].disconnect()
            del self._voice_clients[guild_id]
            if guild_id in self._audio_queues:
                # Clear queue and stop playback task
                while not self._audio_queues[guild_id].empty():
                    try:
                        self._audio_queues[guild_id].get_nowait()
                    except asyncio.QueueEmpty:
                        break
                if guild_id in self._playback_tasks:
                    self._playback_tasks[guild_id].cancel()
                    del self._playback_tasks[guild_id]
                del self._audio_queues[guild_id]
            logger.info("Left voice channel in guild: {}", guild_id)

    async def play_audio(self, guild_id: int, url: str) -> bool:
        """Plays audio from a URL in the specified guild's voice channel."""
        voice_client = self._voice_clients.get(guild_id)
        if voice_client is None:
            logger.warning("Not in a voice channel in guild: {}", guild_id)
            return False

        try:
            audio_path = await self._download_audio(url)
            if audio_path is None:
                return False

            await self._audio_queues[guild_id].put(audio_path)
            if guild_id not in self._playback_tasks or self._playback_tasks[guild_id].done():
                self._playback_tasks[guild_id] = asyncio.create_task(
                    self._playback_loop(guild_id)
                )
            logger.info("Queued audio for playback: {}", url)
            return True
        except Exception:
            logger.exception("Error queuing audio for playback: {}", url)
            return False

    async def stop_playback(self, guild_id: int) -> None:
        """Stops current playback and clears the queue."""
        if guild_id in self._playback_tasks:
            self._playback_tasks[guild_id].cancel()
            del self._playback_tasks[guild_id]
        if guild_id in self._audio_queues:
            while not self._audio_queues[guild_id].empty():
                try:
                    self._audio_queues[guild_id].get_nowait()
                    # Delete the file after it's removed from the queue
                    # Path(audio_path).unlink(missing_ok=True)
                except asyncio.QueueEmpty:
                    break
        if guild_id in self._voice_clients and self._voice_clients[guild_id].is_playing():
            self._voice_clients[guild_id].stop()
        logger.info("Stopped playback and cleared queue for guild: {}", guild_id)

    async def skip_audio(self, guild_id: int) -> bool:
        """Skips the current audio track."""
        if guild_id in self._voice_clients and self._voice_clients[guild_id].is_playing():
            self._voice_clients[guild_id].stop()
            logger.info("Skipped current audio for guild: {}", guild_id)
            return True
        return False

    async def _playback_loop(self, guild_id: int) -> None:
        voice_client = self._voice_clients.get(guild_id)
        audio_queue = self._audio_queues.get(guild_id)
        if voice_client is None or audio_queue is None:
            return

        while True:
            try:
                audio_path = await audio_queue.get()
                logger.info("Playing audio: {}", audio_path)
                voice_client.play(
                    discord.FFmpegPCMAudio(str(audio_path)),
                    after=lambda e: self._playback_after(guild_id, audio_path, e),
                )
                while voice_client.is_playing():
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                logger.info("Playback loop cancelled for guild: {}", guild_id)
                break
            except Exception:
                logger.exception("Error in playback loop for guild: {}", guild_id)
                await asyncio.sleep(1)  # Prevent tight loop on persistent errors

    def _playback_after(self, guild_id: int, audio_path: str, error: Exception | None) -> None:
        if error:
            logger.error("Audio playback error for {}: {}", audio_path, error)
        else:
            logger.info("Finished playing audio: {}", audio_path)
        # Clean up the downloaded file
        Path(audio_path).unlink(missing_ok=True)

    async def _download_audio(self, url: str) -> str | None:
        """Downloads audio from a URL using yt-dlp and ffmpeg."""
        async with self._download_semaphore:
            try:
                # Use yt-dlp to get the audio URL and metadata
                command = [
                    "yt-dlp",
                    "-f", "bestaudio[ext=webm]/bestaudio",  # Prefer webm for Discord
                    "--get-url",
                    "--restrict-filenames",
                    "--no-playlist",
                    "--no-warnings",
                    "--print", "filename",
                    "--output", str(self._media_dir / "%(id)s.%(ext)s"),
                    url,
                ]
                logger.info("Running yt-dlp command: {}", " ".join(command))
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate()

                if process.returncode != 0:
                    logger.error(
                        "yt-dlp failed for {}: {}", url, stderr.decode().strip()
                    )
                    return None

                # The last line of stdout is the filename
                output_lines = stdout.decode().strip().split('\n')
                downloaded_filename = output_lines[-1]
                audio_path = Path(downloaded_filename)

                if not audio_path.is_file():
                    logger.error("Downloaded file not found: {}", audio_path)
                    return None

                logger.info("Downloaded audio to: {}", audio_path)
                return str(audio_path)

            except asyncio.CancelledError:
                logger.warning("Audio download cancelled for: {}", url)
                return None
            except Exception:
                logger.exception("Error downloading audio from: {}", url)
                return None

    async def _cleanup_old_media(self) -> None:
        """Cleans up old downloaded media files."""
        # Implement a cleanup strategy (e.g., delete files older than X hours/days)
        pass

    async def _start_cleanup_task(self) -> None:
        """Starts a periodic cleanup task for old media."""
        while True:
            await asyncio.sleep(3600)  # Run cleanup every hour
            await self._cleanup_old_media()

    async def start(self) -> None:
        """Starts the voice manager and its background tasks."""
        # self._cleanup_task = asyncio.create_task(self._start_cleanup_task())
        logger.info("Discord VoiceManager started.")

    async def stop(self) -> None:
        """Stops the voice manager and disconnects all voice clients."""
        # if hasattr(self, "_cleanup_task") and self._cleanup_task:
        #     self._cleanup_task.cancel()
        #     with suppress(asyncio.CancelledError):
        #         await self._cleanup_task

        for guild_id in list(self._voice_clients.keys()):
            await self.leave_voice_channel(guild_id)
        logger.info("Discord VoiceManager stopped.")
