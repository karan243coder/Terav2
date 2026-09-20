"""
TeraBox Downloader Telegram Bot
================================
Paste any TeraBox share link -> bot downloads the ORIGINAL quality file and
sends it back in the same chat. No login, no cookies, no IDs from the user.

Features:
  - Advanced live progress display (download + upload): bar, %, size,
    speed, ETA and elapsed time — always in sync.
  - FloodWait control: every Telegram API call is retried after the
    platform's required sleep; the bot never hangs or crashes.
  - Health server on port 8080 (GET /healthz) for Koyeb / VPS monitoring.
  - Zero-cookie mode (default), self-hosted API mode (TERABOX_API_BASE),
    ndus cookie mode (TERABOX_COOKIE) and TeraBoxDL player-link turbo mode.

Run:  python bot.py     (config from .env)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.enums import ChatAction
from pyrogram.errors import (
    FloodWait,
    MessageIdInvalid,
    MessageNotModified,
    RPCError,
)
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from terabox import (
    TBFile,
    TeraBox,
    TeraBoxError,
    is_player_url,
    parse_player_url,
    parse_share_url,
    parse_size_str,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Config (.env)
# ---------------------------------------------------------------------------

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TERABOX_COOKIE = os.environ.get("TERABOX_COOKIE", "").strip()
API_BASE = os.environ.get("TERABOX_API_BASE", "").strip()
WORK_DIR = Path(os.environ.get("WORK_DIR", "downloads")).resolve()
OWNER_ID = os.environ.get("OWNER_ID", "").strip()
ALLOWED_USERS = {
    int(x) for x in re.split(r"[,\s]+", os.environ.get("ALLOWED_USERS", "")) if x.strip().isdigit()
}
MAX_FILE_BYTES = int(float(os.environ.get("MAX_FILE_GB", "2.0")) * 1024 ** 3)
RATE_LIMIT_SEC = int(os.environ.get("RATE_LIMIT_SEC", "60"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "3"))
BOT_NAME = os.environ.get("BOT_NAME", "TeraBox Downloader Bot")
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))
VERSION = "2.0"

if TERABOX_COOKIE:
    MODE = "ndus cookie (direct CDN)"
elif API_BASE:
    MODE = "self-hosted API"
else:
    MODE = "zero-cookie (provider API)"

if not (API_ID and API_HASH and BOT_TOKEN):
    raise SystemExit("Set API_ID, API_HASH and BOT_TOKEN in .env (see README).")

WORK_DIR.mkdir(parents=True, exist_ok=True)

URL_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*(?:" +
    "|".join(re.escape(d) for d in [
        "terabox.com", "1024terabox.com", "teraboxapp.com", "terabox.app",
        "nephobox.com", "4funbox.co", "4funbox.com", "mirrobox.com",
        "momerybox.com", "teraboxlink.com", "terafileshare.com",
        "freeterabox.com", "teraboxshare.com", "terasharefile.com",
        "terabox1.com", "terabox2.com", "gibibox.com", "tboxhub.com",
        "player.teraboxdl.site",
    ]) +
    r")[^\s]*",
    re.I,
)

HELP_TEXT = (
    f"👋 Hi! I am *{BOT_NAME}*.\n\n"
    "Just paste any TeraBox share link here:\n"
    "`https://www.terabox.com/s/1AbCdEfG...`\n"
    "`https://1024terabox.com/s/1AbCdEfG...`\n"
    "(all TeraBox alternative domains work)\n\n"
    "🎬 I download the file in *ORIGINAL full quality* (no re-encoding) "
    "and send it right here. You don't need any ID, login or cookies.\n\n"
    "📁 If the share contains multiple files, pick one with the buttons.\n"
    "⚡ Bonus: TeraBoxDL *player links* also work — they give a faster download.\n"
    f"⚠️ Max file size: {MAX_FILE_BYTES // 1024 ** 3} GB "
    "(bigger files are shared as a direct link).\n"
    "⏱️ Please keep a short gap between requests (rate limit)."
)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

SEM = asyncio.Semaphore(CONCURRENCY)
EDIT_LOCK = asyncio.Lock()          # serialize message edits (FloodWait safety)
_last_use: dict[int, float] = {}
_shares: dict[str, tuple[str, float, list]] = {}   # surl -> (url, ts, files)
_dl_tb = TeraBox(cookie=TERABOX_COOKIE)
STARTUP = time.time()
ACTIVE_DOWNLOADS = 0                # live counter (health server)
FLOODWAIT_SLEEPED = 0.0             # total seconds spent in FloodWait (stats)


def is_owner(user_id: int) -> bool:
    return bool(OWNER_ID) and str(user_id) == OWNER_ID


def is_allowed(user_id: int) -> bool:
    return not ALLOWED_USERS or user_id in ALLOWED_USERS or is_owner(user_id)


def rate_ok(user_id: int) -> tuple[bool, int]:
    last = _last_use.get(user_id, 0)
    wait = RATE_LIMIT_SEC - (time.time() - last)
    if wait > 0:
        return False, int(wait) + 1
    _last_use[user_id] = time.time()
    return True, 0


def sanitize(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip()
    return (name or "file")[:150]


def remember(url: str, surl: str, files: list) -> None:
    now = time.time()
    for k in [k for k, (_, ts, _) in _shares.items() if now - ts > 1800]:
        _shares.pop(k, None)
    _shares[surl] = (url, now, files)


# ---------------------------------------------------------------------------
# Formatting helpers (pure functions — easy to test)
# ---------------------------------------------------------------------------

def fmt_bytes(n: float) -> str:
    """Bytes -> human string (n is bytes)."""
    n = max(0.0, float(n))
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024:.1f} KB"


def fmt_dur(seconds: float | None) -> str:
    """Seconds -> h:mm:ss / mm:ss. None -> '—'."""
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def make_bar(pct: float, width: int = 20) -> str:
    filled = max(0, min(width, int(pct * width)))
    return "▰" * filled + "▱" * (width - filled)


class SpeedTracker:
    """Rolling speed/ETA from (time, done-byte) samples."""

    def __init__(self) -> None:
        self.t0 = time.time()
        self.samples: deque = deque()

    def tick(self, done: int) -> None:
        now = time.time()
        self.samples.append((now, done))
        while len(self.samples) > 2 and now - self.samples[0][0] > 8:
            self.samples.popleft()

    @property
    def speed(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        t0, d0 = self.samples[0]
        t1, d1 = self.samples[-1]
        dt = t1 - t0
        return max(0.0, (d1 - d0) / dt) if dt > 0 else 0.0

    @property
    def elapsed(self) -> float:
        return time.time() - self.t0

    def eta(self, total: int) -> float | None:
        s = self.speed
        if s < 1024 or total <= 0:
            return None
        remain = total - self.samples[-1][1]
        return max(0.0, remain / s) if remain > 0 else 0.0


def progress_card(
    emoji: str,
    title: str,
    name: str,
    size_str: str,
    done: int,
    total: int,
    tracker: SpeedTracker,
) -> str:
    """One consistent card for both download and upload phases."""
    lines = [f"{emoji} {title}", "━━━━━━━━━━━━━━━━━━━━", f"🎬 {name[:90]}"]
    lines.append(f"📦 {size_str or 'size unknown'}")
    lines.append("")
    if total > 0:
        pct = min(1.0, done / total)
        lines.append(f"{make_bar(pct)}  {pct * 100:.1f}%")
        lines.append(f"{fmt_bytes(done)} / {fmt_bytes(total)}")
        lines.append(f"⚡ Speed: {fmt_bytes(tracker.speed)}/s")
        lines.append(
            f"⏱️ ETA: {fmt_dur(tracker.eta(total))}  |  Elapsed: {fmt_dur(tracker.elapsed)}"
        )
    else:
        lines.append(f"⬇ {fmt_bytes(done)} so far")
        lines.append(f"⏱️ Elapsed: {fmt_dur(tracker.elapsed)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FloodWait-safe Telegram helpers
# ---------------------------------------------------------------------------

async def safe_edit(msg: Message | None, text: str, tries: int = 3) -> bool:
    """edit_text with FloodWait control + retries. Never raises."""
    if msg is None:
        return False
    for _ in range(tries):
        try:
            async with EDIT_LOCK:
                await msg.edit_text(text, disable_notification=True)
            return True
        except FloodWait as e:
            global FLOODWAIT_SLEEPED
            FLOODWAIT_SLEEPED += e.value
            await asyncio.sleep(min(e.value + 1, 60))
        except MessageNotModified:
            return True
        except (MessageIdInvalid, RPCError):
            return False
    return False


async def safe_send(chat_id: int, text: str, **kw):
    for _ in range(3):
        try:
            return await app.send_message(chat_id, text, **kw)
        except FloodWait as e:
            global FLOODWAIT_SLEEPED
            FLOODWAIT_SLEEPED += e.value
            await asyncio.sleep(min(e.value + 1, 60))
    return None


app = Client(
    "terabox_dl",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)


# ---------------------------------------------------------------------------
# Health server (port 8080 — Koyeb healthcheck + monitoring)
# ---------------------------------------------------------------------------

class _HealthHandler(BaseHTTPRequestHandler):
    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("", "/healthz", "/health"):
            self._json(200, {
                "status": "ok",
                "service": "terabox-downloader-bot",
                "version": VERSION,
                "uptime_s": int(time.time() - STARTUP),
                "active_downloads": ACTIVE_DOWNLOADS,
                "concurrency": CONCURRENCY,
                "mode": MODE,
                "health_port": HEALTH_PORT,
            })
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, *args) -> None:  # silence
        pass


def start_health_server(port: int) -> None:
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    except OSError as e:
        print(f"⚠️  Health server could not bind port {port}: {e} (bot continues)")
        return
    threading.Thread(target=srv.serve_forever, name="health", daemon=True).start()
    print(f"❤️  Health server: http://0.0.0.0:{port}/healthz")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["start", "help"]))
async def cmd_start(app_, msg: Message):
    try:
        await msg.reply_markdown(HELP_TEXT)
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
    except RPCError:
        pass


@app.on_message(filters.command("ping"))
async def cmd_ping(app_, msg: Message):
    t0 = time.time()
    try:
        m = await msg.reply_text("🏓 Pinging...")
        await m.edit_text(f"🏓 Pong! `{(time.time() - t0) * 1000:.0f} ms` ⚡")
    except (FloodWait, RPCError):
        pass


@app.on_message(filters.command("id"))
async def cmd_id(app_, msg: Message):
    try:
        await msg.reply_text(f"👤 Your Telegram ID: `{msg.from_user.id}`")
    except (FloodWait, RPCError):
        pass


@app.on_message(filters.command("stats"))
async def cmd_stats(app_, msg: Message):
    if not is_owner(msg.from_user.id):
        return
    up = time.time() - STARTUP
    try:
        await msg.reply_text(
            "📊 Bot Stats\n"
            f"⏱️ Uptime: {int(up // 3600)}h {int(up % 3600 // 60)}m\n"
            f"🔗 Cached shares: {len(_shares)}\n"
            f"⚡ Active downloads: {ACTIVE_DOWNLOADS}\n"
            f"🚀 Mode: {MODE}\n"
            f"🍪 ndus cookie: {'set' if TERABOX_COOKIE else 'not set (zero-cookie mode)'}\n"
            f"⚙️ Concurrency: {CONCURRENCY} | Rate limit: {RATE_LIMIT_SEC}s\n"
            f"💤 FloodWait sleeps (total): {fmt_dur(FLOODWAIT_SLEEPED)}\n"
            f"❤️ Health: port {HEALTH_PORT} (/healthz)",
            parse_mode="markdown",
        )
    except (FloodWait, RPCError):
        pass


# ---------------------------------------------------------------------------
# Download flow
# ---------------------------------------------------------------------------

def _dl_worker(url: str, fs_id: str, dest: str, prog):
    """Blocking: resolve download source + stream file to dest (worker thread)."""
    src = _dl_tb.resolve_download_source(url, fs_id, "")
    return _dl_tb.download(src.url, dest, headers=src.headers, progress_cb=prog)


@app.on_message(filters.private & URL_RE)
async def on_link(app_, msg: Message):
    user = msg.from_user
    if not is_allowed(user.id):
        try:
            await msg.reply_text("⛔ This bot is for invited users only.")
        except (FloodWait, RPCError):
            pass
        return
    ok, wait = rate_ok(user.id)
    if not ok:
        try:
            await msg.reply_text(f"⏳ Please wait {wait}s and try again (rate limit).")
        except (FloodWait, RPCError):
            pass
        return

    link = URL_RE.search(msg.text).group(0).replace("&amp;", "&")

    try:
        # --- TeraBoxDL player link = already-resolved FAST original link ---
        if is_player_url(link):
            d = parse_player_url(link)
            if not d.get("direct"):
                await msg.reply_text("❌ Invalid player link ('direct' URL is missing).")
                return
            pf = TBFile(
                fs_id=d.get("fs_id") or "0",
                name=d.get("filename") or "video.mp4",
                size=parse_size_str(d.get("size")),
                thumbnail=d.get("poster") or "",
            )
            status = await msg.reply_text(
                f"⚡ Player link detected — fast original download starting...\n\n"
                f"🎬 {pf.name}\n📦 {pf.size_str if pf.size else d.get('size', '?')}",
                disable_notification=True,
            )
            await _deliver(status, user, link, pf)
            return

        _, surl = parse_share_url(link)

        status = await msg.reply_text(
            f"🔍 Processing link...\n{link[:120]}\n\n⏳ Fetching file list",
            disable_notification=True,
        )

        async with SEM:
            try:
                files = await asyncio.to_thread(_dl_tb.get_share_info, link)
            except TeraBoxError as e:
                await safe_edit(status, f"❌ {e}")
                return
            except Exception as e:  # network etc.
                await safe_edit(status, f"❌ Network error: {type(e).__name__}\nPlease try again.")
                return

        non_dir = [f for f in files if not f.is_dir]
        if not non_dir:
            await safe_edit(status, "❌ This share only contains folders — no direct download possible.")
            return

        remember(link, surl, files)

        if len(non_dir) == 1:
            await _deliver(status, user, link, non_dir[0])
        else:
            rows = []
            for i, f in enumerate(non_dir[:20], 1):
                rows.append([InlineKeyboardButton(
                    f"{f.name[:40]}  •  {f.size_str}", callback_data=f"tbd:{surl}:{i}"
                )])
            kb = InlineKeyboardMarkup(rows)
            try:
                async with EDIT_LOCK:
                    await status.edit_text(
                        f"📂 This share has {len(non_dir)} files — which one do you want? (full quality)\n\n"
                        "⚠️ Buttons stay valid for 15 minutes.",
                        reply_markup=kb,
                        disable_notification=True,
                    )
            except (FloodWait, RPCError):
                pass
    except TeraBoxError as e:
        try:
            await msg.reply_text(f"❌ {e}")
        except (FloodWait, RPCError):
            pass
    except Exception as e:  # absolute safety net — bot must never crash
        print(f"❌ on_link error: {type(e).__name__}: {e}")
        try:
            await msg.reply_text(f"❌ Unexpected error: {type(e).__name__}\nPlease try again.")
        except (FloodWait, RPCError):
            pass


@app.on_callback_query(filters.regex("^tbd:"))
async def on_pick(app_, cq: CallbackQuery):
    user = cq.from_user
    try:
        if not is_allowed(user.id):
            return await cq.answer("Not allowed")
        _, surl, idx = cq.data.split(":")
        idx = int(idx)
        entry = _shares.get(surl)
        if not entry:
            return await cq.answer("Expired — please send a fresh link", show_alert=True)
        url, _, files = entry
        non_dir = [f for f in files if not f.is_dir]
        if idx < 1 or idx > len(non_dir):
            return await cq.answer("Invalid option")

        ok, wait = rate_ok(user.id)
        if not ok:
            return await cq.answer(f"Please try again in {wait}s", show_alert=True)

        f = non_dir[idx - 1]
        await cq.answer("Starting download...")
        msg = await cq.message.reply_text(
            f"📥 {f.name}\n📦 {f.size_str}\n\n⏳ Resolving + downloading...",
            disable_notification=True,
        )
        async with SEM:
            await _deliver(msg, user, url, f, from_button=True)
    except Exception as e:  # noqa: BLE001
        print(f"❌ on_pick error: {type(e).__name__}: {e}")
        try:
            await cq.answer("Error — try again", show_alert=True)
        except Exception:
            pass


async def _deliver(status: Message, user, url: str, f, from_button: bool = False):
    """Download -> upload pipeline with advanced live progress (English)."""
    global ACTIVE_DOWNLOADS
    dest = WORK_DIR / f"{user.id}_{sanitize(f.name)}"
    size_str = f.size_str if f.size else ""
    loop = asyncio.get_running_loop()

    try:
        # ---------- Phase 0: oversized files -> direct link ----------
        if f.size and f.size > MAX_FILE_BYTES:
            src = await asyncio.to_thread(_dl_tb.resolve_download_source, url, f.fs_id, "")
            await safe_edit(
                status,
                f"⚠️ {f.name} is {f.size_str} — bigger than the Telegram bot "
                f"limit ({MAX_FILE_BYTES // 1024 ** 3} GB).\n\n"
                "🔗 Direct download link (full quality — use browser / IDM):\n"
                f"{src.url}\n\n"
                "The link stays valid for about 1-2 hours.",
            )
            return

        # ---------- Phase 1: DOWNLOAD (provider -> disk) ----------
        dl_track = SpeedTracker()
        dl_state = {"t": 0.0}

        def dl_prog(done: int, total: int):
            dl_track.tick(done)
            now = time.time()
            if now - dl_state["t"] < 3.5:
                return
            dl_state["t"] = now
            if loop.is_closed():
                return
            txt = progress_card("📥", "Downloading...", f.name, size_str, done, total, dl_track)
            asyncio.run_coroutine_threadsafe(safe_edit(status, txt), loop)

        ACTIVE_DOWNLOADS += 1
        try:
            size = await asyncio.to_thread(_dl_worker, url, f.fs_id, str(dest), dl_prog)
        finally:
            ACTIVE_DOWNLOADS = max(0, ACTIVE_DOWNLOADS - 1)

        dl_time = dl_track.elapsed
        dl_speed = size / dl_time if dl_time > 0 else 0.0
        dl_stats.update(time=dl_time, speed=dl_speed)
        await safe_edit(
            status,
            f"✅ Download complete ({fmt_bytes(size)})\n"
            f"⬇️ Took {fmt_dur(dl_time)} @ {fmt_bytes(dl_speed)}/s\n\n"
            "📤 Uploading to Telegram — this shows live progress too...",
        )
        try:
            await status.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
        except RPCError:
            pass

        # ---------- Phase 2: UPLOAD (disk -> Telegram) ----------
        up_track = SpeedTracker()
        up_state = {"t": 0.0}

        def up_cb(current: int, total: int):
            up_track.tick(current)
            now = time.time()
            if now - up_state["t"] < 3.5:
                return
            up_state["t"] = now
            if loop.is_closed():
                return
            txt = progress_card("📤", "Uploading to Telegram...", f.name, size_str, current, total, up_track)
            asyncio.run_coroutine_threadsafe(safe_edit(status, txt), loop)

        caption = f"🎬 {f.name}\n📦 {fmt_bytes(size)} — original quality (sent as document)"
        sent = None
        last_err = None
        for attempt in range(2):  # FloodWait → sleep + one retry
            try:
                sent = await status.chat.send_document(
                    status.chat.id,
                    str(dest),
                    caption=caption[:1024],
                    progress=up_cb,
                    disable_notification=True,
                )
                break
            except FloodWait as e:
                global FLOODWAIT_SLEEPED
                FLOODWAIT_SLEEPED += e.value
                last_err = e
                if attempt == 0:
                    await asyncio.sleep(min(e.value + 1, 60))
                    continue
                break
            except RPCError as e:
                last_err = e
                break

        if sent is None:
            # upload failed (e.g. file too big / network) -> direct link fallback
            try:
                src = await asyncio.to_thread(_dl_tb.resolve_download_source, url, f.fs_id, "")
                await safe_edit(
                    status,
                    f"⚠️ Telegram upload failed ({type(last_err).__name__ if last_err else 'unknown'}).\n\n"
                    "🔗 Direct download link (full quality):\n"
                    f"{src.url}\n\n"
                    "The link stays valid for about 1-2 hours.",
                )
                return
            except Exception:
                await safe_edit(status, f"❌ Upload failed: {last_err}")
                return

        up_time = up_track.elapsed
        up_speed = size / up_time if up_time > 0 else 0.0
        total_time = dl_time + up_time

        # ---------- Phase 3: SUMMARY ----------
        await safe_edit(
            status,
            "✅ Done!\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🎬 {f.name[:90]}\n"
            f"📦 {fmt_bytes(size)} — original quality\n\n"
            f"⬇️ Download: {fmt_dur(dl_time)} @ {fmt_bytes(dl_speed)}/s\n"
            f"📤 Upload:   {fmt_dur(up_time)} @ {fmt_bytes(up_speed)}/s\n"
            f"⏱️  Total:   {fmt_dur(total_time)}\n\n"
            "👆 The file is above — save it now.",
        )
    except TeraBoxError as e:
        await safe_edit(status, f"❌ {e}")
    except Exception as e:  # noqa: BLE001 — never crash
        print(f"❌ _deliver error: {type(e).__name__}: {e}")
        await safe_edit(
            status,
            f"❌ Something went wrong: {type(e).__name__}: {str(e)[:200]}\nPlease try again.",
        )
    finally:
        try:
            if dest.exists():
                dest.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"🤖 {BOT_NAME} v{VERSION} starting...")
    print(f"   mode:      {MODE}")
    if API_BASE:
        print(f"   api:       {API_BASE}")
    print(f"   workdir:   {WORK_DIR} | max {MAX_FILE_BYTES // 1024 ** 3}GB | rate {RATE_LIMIT_SEC}s | concurrency {CONCURRENCY}")
    start_health_server(HEALTH_PORT)
    app.run()
