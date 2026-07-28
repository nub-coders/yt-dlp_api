# yt-dlp_api

![CI](https://github.com/nub-coders/yt-dlp_api/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/python-3.13-blue.svg)

A high-performance, asynchronous web API and Telegram bot ecosystem for YouTube search, metadata extraction, playlist parsing, and audio/video stream URL resolution. 

This repository provides a unified FastAPI backend and a Pyrogram-based Telegram bot. It features native integration with Redis for caching and rate limiting, and uses Deno for solving YouTube EJS signature challenges.

---

## Features

- ⚡ **High Performance & Async**: Fully asynchronous API built on FastAPI, Uvicorn, and Pyrogram.
- 🚀 **Advanced Stream Extraction**: Resolves direct video and audio streams via `yt-dlp` using optimized player client parameters.
- 🇩 **EJS Challenge Solver**: Integrated Deno JS runtime inside the container to solve complex YouTube EJS signature challenges dynamically.
- 🗄️ **Redis Caching**: Caches stream metadata, resolving configurations, and rates to minimize latency and avoid YouTube rate limits.
- 🔒 **Token-Based Rate Limiting**: Automatic user rate limiting managed via a Redis-backed token system.
- 🤖 **Telegram Bot Management**: Simple Telegram bot for users to generate tokens, check limits, and for admins to manage users, customize rate limits, broadcast announcements, and view real-time API logs.
- 🐳 **Docker-Compose Ready**: Container-based deployment via Docker Compose (behind Traefik, with an external Redis).

---

## Architecture Overview

```text
               +-----------------------+
               |  FastAPI Web Service  | <--- REST API Clients
               +-----------------------+
                            |
                            v
 +--------------+     +-----------+     +-------------------+
 | Telegram Bot | --> |   Redis   | <-- |    utils Module   |
 +--------------+     +-----------+     +-------------------+
  (User/Token          (Rate Limits          (Search, Stream
   Management)          & Caching)            & Playlist Parsing)
                                              |
                                              v
                                        +-------------+
                                        | Deno / Yt   |
                                        +-------------+
```

---

## Quick Start (Docker Deployment)

The Docker Compose stack runs the FastAPI backend and Telegram bot as **two separate services** sharing Redis. **Redis and a Traefik reverse proxy are external dependencies you must provide** — the compose file does not start them (see Prerequisites).

### 1. Prerequisites
Ensure you have [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) installed, plus:
- A reachable **Redis** instance (self-hosted or managed, e.g. Upstash) — set its host/credentials in `.env`.
- An external Docker network named `web` attached to a Traefik reverse proxy (`docker network create web`). The compose file publishes the API through Traefik labels, not a raw port mapping.

### 2. Configure Environment Variables
Create a `.env` file in the root directory and configure your credentials:

```ini
# Telegram Bot API Credentials
API_ID=
API_HASH=
BOT_TOKEN=your_bot_token

# Space-separated list of Telegram user IDs with administrative rights
ADMIN_IDS="6076474757 2128132096"

# Redis Configuration (external instance — host, port and credentials)
REDIS_HOST=your-redis-host
REDIS_PORT=6379

# API Rate Limits
DAILY_LIMIT=1000
ADMIN_LIMIT=10000

# Deployment Host
API_HOST=api.nubcoders.com

# Public API Base URL
BASE_URL=https://api.nubcoders.com
```

### 3. Run the Services
Start the application stack:

```bash
docker-compose up --build -d
```

This will spin up two containers:
- **`ytmusic-api`** — FastAPI Web API on port `8000` (routed via Traefik). Stateless: job state lives in Redis, so you can scale it with `docker compose up -d --scale ytmusic-api=4`.
- **`ytmusic-bot`** — Telegram bot runner. Single long-poll consumer — never scale past 1.

Redis is connected to as an external service using the credentials in `.env`.

---

## Manual Installation (Local Development)

If you prefer to run the services locally without Docker:

### 1. Install System Dependencies
- Install **FFmpeg** (required by `yt-dlp` for media processing).
- Install **Deno** (required for solving signature challenges):
  ```bash
  curl -fsSL https://deno.land/install.sh | sh
  ```

### 2. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 3. Run the Services
Run the Redis server locally, and then start the API and Bot:

```bash
# Start the API and Bot services concurrently
python3 main.py
```

Or you can run the bot separately:
```bash
python3 bot.py
```

---

## Configuration Reference

All secrets and configurations can be customized via environment variables:

| Environment Variable | Description | Default |
|----------------------|-------------|---------|
| `API_ID` | Telegram API ID (from my.telegram.org) | `2040` |
| `API_HASH` | Telegram API Hash (from my.telegram.org) | `b18441a1ff...` |
| `BOT_TOKEN` | Token for the Pyrogram bot | `None` |
| `TG_GROUP` | Telegram support/discussion group username | `nub_coder_s` |
| `TG_CHANNEL` | Telegram news/update channel username | `nub_coders` |
| `ADMIN_IDS` | Space-separated list of admin Telegram IDs | `""` |
| `REDIS_HOST` | Hostname of the Redis server | `localhost` |
| `REDIS_PORT` | Port of the Redis server | `15440` |
| `REDIS_USERNAME`| Username for Redis authentication | `default` |
| `REDIS_PASSWORD`| Password for Redis authentication | `None` |
| `API_HOST` | Traefik host used by Docker Compose | `api.nubcoders.com` |
| `BASE_URL` | Public base URL used in bot/docs | `https://api.nubcoders.com` |
| `DAILY_LIMIT` | Default daily requests limit for free tier users | `1000` |
| `ADMIN_LIMIT` | Default daily requests limit for administrators | `10000` |
| `COOKIES_FILE` | Path to a Netscape-format YouTube cookies file | `cookies.txt` |
| `COOKIES_BROWSER` | Browser profile to export cookies from (local dev only) | `firefox` |
| `COOKIES_REFRESH_HOURS` | Re-export cookies from the browser every N hours (`0` disables) | `6` |
| `YTDLP_MAX_PROCS` | Max concurrent yt-dlp/ffmpeg subprocesses (backpressure cap) | `2 × CPU count` |
| `WEB_CONCURRENCY` | Uvicorn worker processes per API container | `1` |

### Cookies & Security

yt-dlp uses a YouTube cookie jar to avoid bot-detection on some videos. In the Docker setup, cookies are exported automatically at startup from a **Firefox profile mounted into the container** (`${HOME}/.mozilla/firefox` → `/root/.mozilla/firefox`, see `docker-compose.yml`) and re-exported every `COOKIES_REFRESH_HOURS`.

**A logged-in browser profile is a bearer credential for that Google/YouTube account** — anyone who can read the profile or the exported `cookies.txt` can act as that account. Treat it accordingly:

- **Use a throwaway YouTube account, never a personal/primary one.** If the container or host is compromised, the session is fully exposed.
- The container runs as root specifically so it can read the mounted profile at `/root/.mozilla`. If you don't need browser auto-export, mount a pre-exported `cookies.txt` at `/app/cookies.txt` and set `COOKIES_REFRESH_HOURS=0` instead.
- To export a cookies file by hand on a trusted machine:
  ```bash
  yt-dlp --cookies-from-browser firefox --cookies cookies.txt --skip-download "https://www.youtube.com/watch?v=BaW_jenozKc"
  ```

---

## Web API Endpoints

The web API is served at `http://localhost:8000`. Public endpoints are free, while data-heavy parsing endpoints require an API token passed as a query parameter (e.g., `?token=YOUR_TOKEN`).

### Public Endpoints (Free)

#### `GET /health`
Returns service health status.
- **Response**: `{"status": "ok"}`

#### `GET /`
Returns service metadata and an endpoint directory.

#### `GET /metrics`
Prometheus metrics (request counts + latency summary) in text exposition format. Per-process — scrape each API replica as its own target.

#### `GET /search`
Perform queries on YouTube or YouTube Music.
- **Parameters**:
  - `q` (string, required): Query term.
  - `limit` (integer, optional): Maximum results. Default `5`.
  - `method` (string, optional): `'scrape'` (free crawler, default) or `'api'` (official YouTube Data API).
- **Response**: Search result list with titles, URLs, duration, and channel metadata.

#### `GET /trending`
Get trending music tracks.
- **Parameters**:
  - `limit` (integer, optional): Maximum results. Default `10`.

#### `GET /suggest`
Get search completion suggestions for a partial query.
- **Parameters**:
  - `q` (string, required): Partial query.
  - `limit` (integer, optional): Maximum suggestions. Default `5`.

---

### Authenticated Endpoints
*Authenticate with a header: `Authorization: Bearer YOUR_API_TOKEN`.*
*The `?token=YOUR_API_TOKEN` query parameter is still accepted but **deprecated** — it leaks into proxy/access logs and browser history. Prefer the header. (The `/stream/redirect` and `/video-stream/redirect` endpoints still require `?token=` so it can be carried through the 307 redirect.)*

#### `GET /rate-limit-status`
Check your current token request usage and remaining quota.

#### `GET /info`
Get parsed metadata and direct streaming links for a video.
- **Parameters**:
  - `q` (string, required): YouTube video URL or search query.
  - `max_results` (integer, optional): Max search results if `q` is a query. Default `1`.
  - `mode` (string, optional): `'audio'` (default) or `'video'`.
  - `redirect` (boolean, optional): Return a temporary redirect URL or wait for extraction. Default `True`.

#### `GET /stream`
Resolve and return direct audio/video stream URLs.
- **Parameters**:
  - `q` (string, required): YouTube video URL or ID.
  - `mode` (string, optional): `'audio'` (default) or `'video'`.
  - `redirect` (boolean, optional): Return a temporary redirect URL. Default `False`.

#### `GET /stream/redirect`
Get an instant redirect URL for audio streaming (ideal for `pytgcall` integrations).
- **Parameters**:
  - `q` (string, required): YouTube video URL or ID.
  - `mode` (string, optional): `'audio'` (default) or `'video'`.

#### `GET /video-stream`
Resolve and return separate high-quality video and audio URLs.
- **Parameters**:
  - `q` (string, required): YouTube video URL or ID.
  - `redirect` (boolean, optional): Return a temporary redirect URL. Default `False`.

#### `GET /video-stream/redirect`
Get an instant redirect URL for video streaming.
- **Parameters**:
  - `q` (string, required): YouTube video URL or ID.
  - `type` (string, optional): `'audio'` (default) or `'video'`.

#### `GET /playlist`
Parse all video tracks in a playlist.
- **Parameters**:
  - `url` (string, required): YouTube Playlist URL or ID.

---

## Telegram Bot Interface

Users can interact with the Telegram bot (e.g. `@ytdlp_nub_bot`) to obtain an API token and monitor their request counts.

### User Commands
- `/start` - Authenticates user, creates account, and issues an API token.
- `/menu` - Interactive inline button interface for checking rates and profile.
- `/token` - Displays the user's active API token.
- `/status` - Checks current rate limits, tier, and requested endpoint counts.
- `/ping` - Latency ping check with bot uptime statistics.
- `/revoke` - Revokes your current token and generates a new one.
- `/help` - Show help and API documentation.

### Administrative Commands (Admins only)
- `/stats` - Comprehensive API performance dashboard (requests, uptime, user tiers, active logs).
- `/user <tg_id>` - Inspect usage statistics and rate limit status of a specific user.
- `/grant <tg_id> <limit>` - Set custom daily rate limit for a specific user.
- `/revoke <tg_id>` - Reset custom rate limit for a user back to default.
- `/listusers` - List all registered user IDs.
- `/errors` - Display recent API 4xx/5xx failure logs with details.
- `/broadcast <msg>` - Send an announcement message to all registered bot users.
- `/adminhelp` - Show helper card for all administrative commands.

---

## Programmatic Usage

If you prefer to import the modules programmatically inside other Python scripts, use the `utils` package:

```python
import asyncio
from utils import Search, get_stream

async def main():
    # 1. Search song
    results = await Search("Kesariya", limit=1)
    if results and results.get("main_results"):
        song = results["main_results"][0]
        print(f"Found: {song['title']} by {song['channel']}")
        
        # 2. Extract direct stream URL
        stream_url = await get_stream(song['url'])
        print(f"Direct stream URL: {stream_url}")

asyncio.run(main())
```

---

## Project Structure

```text
yt-dlp_api/
│
├── config.py              # Centralised environment variable loader
├── main.py                # FastAPI endpoints and middleware / bot runner
├── bot.py                 # Bot startup script
├── tools.py               # Redis client connection and token handlers
│
├── utils/                 # Shared utilities and parsers
│   ├── __init__.py        # Package exports
│   ├── cache_manager.py   # Stream cache resolver (Redis & Local)
│   ├── cli_tool.py        # Command-line utility interface
│   ├── formatters.py      # Video output list formatter
│   ├── helpers.py         # Utility calculation functions
│   ├── media_extractor.py # Audio/video link resolving logic (yt-dlp)
│   ├── playlist_parser.py # Playlist item parser
│   ├── search_service.py  # Scraping-based search crawler
│   └── youtube_api.py     # Fallback YouTube Data API crawler
│
└── plugins/               # Telegram bot pyrogram plugins
    ├── admin.py           # Administrative stats, limits, and error logs
    ├── broadcast.py       # Global bot broadcast dispatcher
    ├── commands.py        # Menu, ping, start commands
    └── status.py          # User profile status and tokens
```

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
