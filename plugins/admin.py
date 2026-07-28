from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import time
import datetime
import statistics

from tools import (
    redis_client, scan_keys, is_admin, get_user_token, revoke_user_token,
    get_user_request_count, set_user_request_count,
    get_failed_request_count, get_recent_errors
)

ADMIN_BOT_START_TIME = time.time()


def _get_readable_uptime(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    parts = []
    if d > 0:
        parts.append(f"{d}d")
    if h > 0:
        parts.append(f"{h}h")
    if m > 0:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


async def _resolve_username(client: Client, uid: str) -> str:
    """Try to resolve a Telegram user ID to a display name."""
    try:
        user = await client.get_users(int(uid))
        name = user.first_name or ""
        if user.last_name:
            name += f" {user.last_name}"
        return name.strip() or f"User {uid}"
    except Exception:
        return f"User {uid}"


async def _build_stats(client: Client, progress_callback=None):
    """Collect all stats data and return (message1, message2) tuple."""
    now = datetime.datetime.now(datetime.timezone.utc)

    if progress_callback:
        await progress_callback("🔌 Connecting to Redis cache database...")

    # ── User data ──────────────────────────────────────────
    user_keys = scan_keys("user_token:*")
    total_users = len(user_keys)

    if progress_callback:
        await progress_callback(f"📊 Registered users loaded ({total_users:,} found). Scanning active sessions...")

    request_keys = scan_keys("user_requests:*")
    active_users = len(request_keys)

    total_requests_today = 0
    user_request_data = []
    request_counts = []

    for key in request_keys:
        try:
            count = int(redis_client.get(key) or 0)
            total_requests_today += count
            user_id_from_key = key.split(":")[1]
            user_request_data.append((user_id_from_key, count))
            request_counts.append(count)
        except Exception:
            continue

    user_request_data.sort(key=lambda x: x[1], reverse=True)

    # ── Admin / regular breakdown ──────────────────────────
    admin_count = 0
    admin_requests = 0
    regular_requests = 0
    admin_active = 0
    regular_active = 0

    for user_id_str, count in user_request_data:
        if is_admin(int(user_id_str)):
            admin_count += 1
            admin_requests += count
            admin_active += 1
        else:
            regular_requests += count
            regular_active += 1

    # Total admins (including inactive ones)
    total_admin_count = sum(1 for k in user_keys if is_admin(int(k.split(":")[1])))

    # ── Request analytics ──────────────────────────────────
    avg_requests = round(total_requests_today / max(active_users, 1), 2)
    median_requests = int(statistics.median(request_counts)) if request_counts else 0
    peak_requests = max(request_counts) if request_counts else 0
    min_requests = min(request_counts) if request_counts else 0

    # ── User activity tiers ────────────────────────────────
    heavy_users = sum(1 for c in request_counts if c >= 500)
    moderate_users = sum(1 for c in request_counts if 100 <= c < 500)
    light_users = sum(1 for c in request_counts if 10 <= c < 100)
    idle_users = sum(1 for c in request_counts if c < 10)
    inactive_users = total_users - active_users

    # ── New vs returning (users with token but 0 requests) ─
    new_today = 0
    for key in user_keys:
        uid = key.split(":")[1]
        req_count = redis_client.get(f"user_requests:{uid}")
        if req_count is None:
            new_today += 1

    # ── Uptime ─────────────────────────────────────────────
    bot_uptime = _get_readable_uptime(int(time.time() - ADMIN_BOT_START_TIME))

    # ── Redis info ─────────────────────────────────────────
    redis_info = redis_client.info()
    redis_memory = redis_info.get('used_memory_human', 'N/A')
    redis_peak_memory = redis_info.get('used_memory_peak_human', 'N/A')
    redis_uptime = redis_info.get('uptime_in_seconds', 0)
    redis_uptime_str = _get_readable_uptime(redis_uptime)
    connected_clients = redis_info.get('connected_clients', 'N/A')
    ops_per_sec = redis_info.get('instantaneous_ops_per_sec', 'N/A')
    total_commands = redis_info.get('total_commands_processed', 'N/A')

    # Keyspace hit rate
    hits = redis_info.get('keyspace_hits', 0)
    misses = redis_info.get('keyspace_misses', 0)
    total_lookups = hits + misses
    hit_rate = round((hits / max(total_lookups, 1)) * 100, 1)

    total_keys = len(scan_keys('*'))

    # ── Usage percentages ──────────────────────────────────
    user_utilization = round((active_users / max(total_users, 1)) * 100, 1)

    # Capacity usage (total requests vs theoretical max)
    theoretical_max = (regular_active * 1000) + (admin_active * 10000) if active_users > 0 else 1
    capacity_usage = round((total_requests_today / theoretical_max) * 100, 1)

    # ── System load indicator ──────────────────────────────
    if total_requests_today < 10000:
        load_indicator = "🟢 Low"
    elif total_requests_today < 50000:
        load_indicator = "🟡 Moderate"
    elif total_requests_today < 100000:
        load_indicator = "🟠 High"
    else:
        load_indicator = "🔴 Critical"

    # ── Failure analytics ──────────────────────────────────
    global_failed = int(redis_client.get("global_failed_total") or 0)

    # Per-status breakdown
    status_keys = scan_keys("failed_by_status:*")
    status_breakdown = []
    for sk in status_keys:
        code = sk.split(":")[1]
        cnt = int(redis_client.get(sk) or 0)
        status_breakdown.append((code, cnt))
    status_breakdown.sort(key=lambda x: x[1], reverse=True)

    # Per-path breakdown (top 5 failing endpoints)
    path_keys = scan_keys("failed_by_path:*")
    path_breakdown = []
    for pk in path_keys:
        path = pk.split(":", 1)[1]
        cnt = int(redis_client.get(pk) or 0)
        path_breakdown.append((path, cnt))
    path_breakdown.sort(key=lambda x: x[1], reverse=True)
    top_fail_paths = path_breakdown[:5]

    # Per-user failure leaderboard (top 5)
    fail_user_keys = scan_keys("user_failed:*")
    user_fail_data = []
    for fk in fail_user_keys:
        uid = fk.split(":")[1]
        cnt = int(redis_client.get(fk) or 0)
        user_fail_data.append((uid, cnt))
    user_fail_data.sort(key=lambda x: x[1], reverse=True)
    top_fail_users = user_fail_data[:5]

    # Success rate
    total_all = total_requests_today + global_failed
    success_rate = round(((total_all - global_failed) / max(total_all, 1)) * 100, 1)

    # Build failure text sections
    status_lines = ""
    for code, cnt in status_breakdown[:6]:
        status_lines += f"  `{code}` → **{cnt:,}**\n"
    if not status_lines:
        status_lines = "  None today ✨\n"

    path_lines = ""
    for path, cnt in top_fail_paths:
        path_lines += f"  `{path}` → **{cnt:,}**\n"
    if not path_lines:
        path_lines = "  None today ✨\n"

    fail_user_lines = ""
    for idx, (uid, cnt) in enumerate(top_fail_users, 1):
        if progress_callback:
            await progress_callback(f"⚠️ Resolving failed user identity {idx}/{len(top_fail_users)}...")
        name = await _resolve_username(client, uid)
        fail_user_lines += f"  ⚠️ **{name}** (`{uid}`) → **{cnt:,}** failures\n"
    if not fail_user_lines:
        fail_user_lines = "  None today ✨\n"

    # ── Top 10 users with names ────────────────────────────
    top_users = user_request_data[:10]
    top_users_lines = []
    for i, (uid, count) in enumerate(top_users, 1):
        admin_badge = "👑" if is_admin(int(uid)) else "👤"
        if progress_callback:
            await progress_callback(f"👤 Resolving identity for active user {i}/{len(top_users)}...")
        name = await _resolve_username(client, uid)
        limit = 10000 if is_admin(int(uid)) else 1000
        pct = round((count / limit) * 100, 1)
        bar_fill = min(int(pct / 10), 10)
        bar = "█" * bar_fill + "░" * (10 - bar_fill)
        top_users_lines.append(
            f"  {i}. {admin_badge} **{name}** (`{uid}`)\n"
            f"     {bar} {count:,} / {limit:,} ({pct}%)"
        )

    top_users_text = "\n".join(top_users_lines) if top_users_lines else "  No active users today"

    # ── Build message 1: Overview ──────────────────────────
    msg1 = (
        f"📊 **Bot Statistics Dashboard**\n"
        f"🕐 {now.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        f"⏱ Bot uptime: `{bot_uptime}`\n"
        f"{'━' * 28}\n\n"

        f"👥 **User Metrics**\n"
        f"├ Total registered: **{total_users:,}**\n"
        f"├ Active today: **{active_users:,}** ({user_utilization}%)\n"
        f"├ Inactive today: **{inactive_users:,}**\n"
        f"├ New (no requests yet): **{new_today:,}**\n"
        f"├ Admin users: **{total_admin_count}** ({admin_active} active)\n"
        f"└ Regular users: **{total_users - total_admin_count:,}** ({regular_active} active)\n\n"

        f"📈 **Request Analytics**\n"
        f"├ Total today: **{total_requests_today:,}**\n"
        f"├ Admin requests: **{admin_requests:,}**\n"
        f"├ Regular requests: **{regular_requests:,}**\n"
        f"├ Avg / active user: **{avg_requests}**\n"
        f"├ Median / active user: **{median_requests}**\n"
        f"├ Peak (single user): **{peak_requests:,}**\n"
        f"├ Min (single user): **{min_requests}**\n"
        f"└ Capacity used: **{capacity_usage}%**\n\n"

        f"📶 **User Activity Tiers**\n"
        f"├ 🔴 Heavy (500+): **{heavy_users}**\n"
        f"├ 🟠 Moderate (100-499): **{moderate_users}**\n"
        f"├ 🟢 Light (10-99): **{light_users}**\n"
        f"└ ⚪ Idle (<10): **{idle_users}**\n\n"

        f"❌ **Failure Analytics**\n"
        f"├ Total failed today: **{global_failed:,}**\n"
        f"├ Success rate: **{success_rate}%**\n"
        f"├ By status code:\n{status_lines}"
        f"├ Top failing endpoints:\n{path_lines}"
        f"└ Top users with failures:\n{fail_user_lines}"
    )

    # ── Build message 2: Top Users + System ────────────────
    cmd_line = f"├ Total commands: **{total_commands:,}**" if isinstance(total_commands, int) else f"├ Total commands: **{total_commands}**"

    msg2 = (
        f"🔥 **Top 10 Users Today**\n\n"
        f"{top_users_text}\n\n"
        f"{'━' * 28}\n\n"

        f"🗃️ **Redis Status**\n"
        f"├ Memory: **{redis_memory}** (peak: {redis_peak_memory})\n"
        f"├ Uptime: **{redis_uptime_str}**\n"
        f"├ Connected clients: **{connected_clients}**\n"
        f"├ Ops/sec: **{ops_per_sec}**\n"
        f"{cmd_line}\n"
        f"├ Keyspace hit rate: **{hit_rate}%** ({hits:,} hits / {misses:,} misses)\n"
        f"└ Total keys: **{total_keys:,}**\n\n"

        f"📋 **Rate Limits**\n"
        f"├ Regular: **1,000** req/day\n"
        f"├ Admin: **10,000** req/day\n"
        f"└ Search/Suggest/Trending: **Unlimited**\n\n"

        f"⚡ **Health**\n"
        f"├ System load: {load_indicator}\n"
        f"├ Active ratio: **{user_utilization}%**\n"
        f"└ Cache: **Operational**"
    )

    return msg1, msg2


@Client.on_message(filters.command("stats") & filters.private)
async def bot_stats(client: Client, message: Message):
    user_id = message.from_user.id

    if not is_admin(user_id):
        await message.reply_text("❌ You don't have admin privileges.")
        return

    chat_id = message.chat.id
    draft_id = client.rnd_id()

    try:
        # Define progress callback using native send_message_draft
        async def progress(text):
            await client.send_message_draft(
                chat_id=chat_id,
                draft_id=draft_id,
                text=f"⚡ **Stats Compilation Progress**\n\n{text}"
            )

        # Start loading progress stream
        await progress("⏳ Scanning Redis database structure...")

        msg1, msg2 = await _build_stats(client, progress_callback=progress)

        # Finalize and send completed statistics overview
        await message.reply_text(msg1)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh Stats", callback_data="admin_refresh_stats")],
        ])
        await message.reply_text(msg2, reply_markup=keyboard)

    except Exception as e:
        await message.reply_text(f"❌ Error retrieving stats: {str(e)}")

@Client.on_message(filters.regex(r"^/user \d+") & filters.private)
async def user_info(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ You don't have admin privileges.")
        return
    
    try:
        target_user_id = message.text.split()[1]
        token = await get_user_token(target_user_id)
        request_count = await get_user_request_count(target_user_id)
        failed_count = await get_failed_request_count(target_user_id)
        
        if token:
            total = request_count + failed_count
            success_rate = round((request_count / max(total, 1)) * 100, 1)
            limit = 10000 if is_admin(int(target_user_id)) else 1000
            remaining = max(0, limit - request_count)

            await message.reply_text(
                f"👤 **User Information**\n\n"
                f"🆔 User ID: `{target_user_id}`\n"
                f"🔑 Token: `{token}`\n"
                f"👑 Admin: {'Yes' if is_admin(int(target_user_id)) else 'No'}\n\n"
                f"📊 **Usage Today**\n"
                f"├ Successful: **{request_count:,}** / {limit:,}\n"
                f"├ Failed: **{failed_count:,}**\n"
                f"├ Total: **{total:,}**\n"
                f"├ Success rate: **{success_rate}%**\n"
                f"└ Remaining: **{remaining:,}**"
            )
        else:
            await message.reply_text(f"❌ User {target_user_id} not found.")
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@Client.on_message(filters.regex(r"^/grant \d+ \d+") & filters.private)
async def grant_requests(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ You don't have admin privileges.")
        return
    
    try:
        _, target_user_id, amount = message.text.split()
        target_user_id = int(target_user_id)
        amount = int(amount)
        
        current_count = await get_user_request_count(target_user_id)
        new_count = max(0, current_count - amount)  # Reduce count to grant more requests
        await set_user_request_count(target_user_id, new_count)
        
        await message.reply_text(
            f"✅ **Request Granted!**\n\n"
            f"👤 User: {target_user_id}\n"
            f"🎁 Amount: {amount} extra requests\n"
            f"📊 Previous: {current_count}\n"
            f"📈 New: {new_count}"
        )
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@Client.on_message(filters.regex(r"^/revoke \d+") & filters.private)
async def revoke_token(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ You don't have admin privileges.")
        return
    
    try:
        target_user_id = int(message.text.split()[1])
        await revoke_user_token(target_user_id)
        await message.reply_text(f"✅ Token revoked for user {target_user_id}")
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@Client.on_message(filters.command("listusers") & filters.private)
async def list_users(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ You don't have admin privileges.")
        return
    
    user_keys = scan_keys("user_token:*")
    recent_users = []
    
    for key in user_keys[:20]:  # Show last 20 users
        user_id_key = key.split(":")[1]
        token = redis_client.get(key)
        request_count = redis_client.get(f"user_requests:{user_id_key}") or "0"
        recent_users.append(f"👤 {user_id_key}: {request_count} requests")
    
    users_text = "\n".join(recent_users) if recent_users else "No users found"
    
    await message.reply_text(f"📋 **Recent Users**\n\n{users_text}")

@Client.on_message(filters.command("adminhelp") & filters.private)
async def admin_help(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ You don't have admin privileges.")
        return
    
    await message.reply_text(
        "👑 **Admin Commands**\n\n"
        "📊 `/stats` - View bot statistics\n"
        "❌ `/errors` - View recent error log\n"
        "👤 `/user <user_id>` - Get user information\n"
        "🎁 `/grant <user_id> <amount>` - Grant extra requests\n"
        "🚫 `/revoke <user_id>` - Revoke user token\n"
        "📋 `/listusers` - List recent users\n"
        "❓ `/adminhelp` - Show this help message\n\n"
        "**Examples:**\n"
        "• `/user 123456789`\n"
        "• `/grant 123456789 500`\n"
        "• `/revoke 123456789`\n"
        "• `/errors 30` (show last 30 errors)"
    )


@Client.on_message(filters.command("errors") & filters.private)
async def errors_command(client: Client, message: Message):
    """Show recent API error log with full error messages."""
    user_id = message.from_user.id

    if not is_admin(user_id):
        await message.reply_text("❌ You don't have admin privileges.")
        return

    # Parse optional count argument: /errors 30
    parts = message.text.split()
    try:
        count = min(int(parts[1]), 50) if len(parts) > 1 else 15
    except ValueError:
        count = 15

    try:
        errors = await get_recent_errors(count)

        if not errors:
            await message.reply_text("✅ **No errors logged yet!** Everything is running clean.")
            return

        # Build error log messages (split into chunks to avoid Telegram length limit)
        header = f"❌ **Recent Error Log** (last {len(errors)})\n{'━' * 28}\n\n"
        lines = []

        for i, entry in enumerate(errors, 1):
            ts = entry.get("ts", "?")
            uid = entry.get("user", "?")
            status = entry.get("status", "?")
            path = entry.get("path", "?")
            error = entry.get("error", "No message")

            # Truncate long error messages for readability
            if len(error) > 150:
                error = error[:147] + "..."

            lines.append(
                f"**{i}.** `{ts}`\n"
                f"   👤 `{uid}` → `{status}` on `{path}`\n"
                f"   💬 {error}\n"
            )

        # Telegram max message length is ~4096. Split if needed.
        messages = []
        current = header
        for line in lines:
            if len(current) + len(line) > 3900:
                messages.append(current)
                current = ""
            current += line
        if current:
            messages.append(current)

        for msg in messages:
            await message.reply_text(msg)

    except Exception as e:
        await message.reply_text(f"❌ Error fetching logs: {str(e)}")
