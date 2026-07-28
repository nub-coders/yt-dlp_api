"""Global cap on concurrent yt-dlp/ffmpeg subprocesses.

Each yt-dlp -g spawn pulls a JS runtime + network; unbounded fan-out under load
OOMs the box. One shared semaphore across every spawn site bounds it. Sized to
2×CPU (I/O-bound wait on network, so > core count is fine). Override via
YTDLP_MAX_PROCS. Single event loop → asyncio.Semaphore, no threading concern.
"""
import asyncio
import os

_max = int(os.getenv("YTDLP_MAX_PROCS", str((os.cpu_count() or 2) * 2)))
subprocess_slot = asyncio.Semaphore(_max)
