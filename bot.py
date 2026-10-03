import re
import json
import os
import logging
from urllib.parse import parse_qs, unquote, urljoin, urlparse
import requests
import urllib3
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============ CONFIG ============
BOT_TOKEN = "8803100493:AAGnwuVy_0dUGz4b3mPeWSderOfqDqCSpdg"
ADMIN_ID = 8865925148
DATA_FILE = "bot_data.json"

HELP_URL = "https://t.me/Princescripterbot"

MAX_HOPS = 10
TIMEOUT = 10
CHECK_KEYS = ['url', 'redirect', 'link', 'target', 'dest', 'destination', 'r']

WAIT_BAN_ID, WAIT_UNBAN_ID, WAIT_BROADCAST_MSG = range(3)

HEADERS_REQUEST = {
    'sec-ch-ua': '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
    'sec-ch-ua-mobile': '?1',
    'sec-ch-ua-platform': '"Android"',
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'sec-fetch-site': 'none',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-user': '?1',
    'sec-fetch-dest': 'document',
    'accept-encoding': 'gzip, deflate',
    'accept-language': 'en-US,en;q=0.9,hi;q=0.8,fr;q=0.7',
    'priority': 'u=0, i',
}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ============ DATA STORE ============
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                d.setdefault("bot_on", True)
                d.setdefault("users", [])
                d.setdefault("banned", [])
                return d
        except Exception:
            pass
    return {"bot_on": True, "users": [], "banned": []}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def register_user(user_id):
    data = load_data()
    if user_id not in data["users"]:
        data["users"].append(user_id)
        save_data(data)


def is_banned(user_id):
    return user_id in load_data()["banned"]


def esc(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ============ HELPERS ============
def is_valid_url(url):
    if not url:
        return False
    p = urlparse(url)
    return p.scheme in ('http', 'https') and bool(p.netloc)


def clean_response(resp):
    raw = resp.text.strip()
    if not raw:
        return f"Status: {resp.status_code}"
    clean_text = " ".join(re.sub(r'<[^>]+>', ' ', raw).split())
    if clean_text:
        return clean_text[:200] + ("..." if len(clean_text) > 200 else "")
    return f"Status: {resp.status_code}"


def extract_embedded_link(url):
    parsed = urlparse(url)
    if parsed.query:
        params = parse_qs(parsed.query)
        for key in CHECK_KEYS:
            for k in params:
                if k.lower() == key:
                    raw_val = params[k][0].strip()
                    val = unquote(unquote(raw_val))
                    p = urlparse(val)
                    if p.scheme in ('http', 'https') and p.netloc:
                        return val
    return url


def extract_client_redirect(html, current_url):
    meta_match = re.search(
        r'<meta[^>]*?content=[\'"][^;]*?;\s*url=([^>\'"]+)',
        html, re.IGNORECASE
    )
    if meta_match:
        target = meta_match.group(1).strip().strip("'\"")
        return urljoin(current_url, target)

    js_patterns = [
        r'(?:window\.)?location(?:\.href|\.replace)?\s*[=(]\s*[\'"]([^\'"]+)[\'"]',
        r'location\.assign\([\'"]([^\'"]+)[\'"]\)',
    ]
    for pattern in js_patterns:
        js_match = re.search(pattern, html, re.IGNORECASE)
        if js_match:
            target = js_match.group(1).strip()
            return urljoin(current_url, target)

    return None


# ============ TRACE ENGINE ============
def trace_url(start_url, redirect_custom=None):
    current_url = start_url.strip()
    if not current_url.startswith(("http://", "https://")):
        current_url = "https://" + current_url

    hop = 1
    max_steps = redirect_custom if redirect_custom else MAX_HOPS
    session = requests.Session()
    session.headers.update(HEADERS_REQUEST)

    step_urls = []
    last_response_text = None
    error_text = None
    visited_urls = set()

    while current_url and hop <= max_steps:
        unpacked_url = extract_embedded_link(current_url)
        if unpacked_url != current_url:
            current_url = unpacked_url

        if not is_valid_url(current_url):
            break

        if current_url in visited_urls:
            break
        visited_urls.add(current_url)

        step_urls.append(current_url)

        try:
            resp = session.get(
                current_url, allow_redirects=False,
                verify=False, timeout=TIMEOUT
            )
            last_response_text = clean_response(resp)
        except requests.exceptions.RequestException as e:
            error_text = str(e)
            break

        if redirect_custom and hop >= redirect_custom:
            break

        next_url = resp.headers.get("Location")
        if next_url:
            next_candidate = urljoin(current_url, next_url.strip())
            if not is_valid_url(next_candidate):
                break
            current_url = next_candidate
            hop += 1
            continue

        client_target = extract_client_redirect(resp.text, current_url)
        if client_target and client_target != current_url:
            if not is_valid_url(client_target):
                break
            current_url = client_target
            hop += 1
            continue

        break

    lines = []
    lines.append("<b>╔════════════════════════╗</b>")
    lines.append("<b>║   PRINCE SCRIPTER &amp; REDIRECTING   ║</b>")
    lines.append("<b>╚════════════════════════╝</b>")
    lines.append("")

    for idx, url in enumerate(step_urls, start=1):
        lines.append(f"<b>⦿ Redirect {idx} Step ➪</b> <code>{esc(url)}</code>")
        lines.append("")

    if not step_urls:
        lines.append("<b>⦿ Redirect Step ➪</b> <code>None</code>")
        lines.append("")

    lines.append("<b>≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈</b>")
    lines.append("")

    if last_response_text:
        lines.append(f"<b>⦿ Redirect Response ➪</b> <code>{esc(last_response_text)}</code>")
    else:
        lines.append("<b>⦿ Redirect Response ➪</b> <code>None</code>")

    lines.append("")

    if error_text:
        lines.append(f"<b>⦿ Error Response ➪</b> <code>{esc(error_text)}</code>")
    else:
        lines.append("<b>⦿ Error Response ➪</b> <code>None</code>")

    lines.append("")
    lines.append("<b>≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈</b>")
    lines.append("")
    lines.append("<b>🌹 Powered BY ➪ @Princescripter</b>")

    return "\n".join(lines)


# ============ KEYBOARDS ============
def user_keyboard():
    btn = InlineKeyboardButton("🔗 Insert Link", callback_data="user_insert_link")
    btn_help = InlineKeyboardButton("ℹ️ Help", url=HELP_URL)
    return InlineKeyboardMarkup([[btn], [btn_help]])


def admin_start_keyboard():
    btn_panel = InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_open_panel")
    btn_link = InlineKeyboardButton("🔗 Insert Link", callback_data="user_insert_link")
    btn_help = InlineKeyboardButton("ℹ️ Help", url=HELP_URL)
    return InlineKeyboardMarkup([
        [btn_panel],
        [btn_link],
        [btn_help],
    ])


def admin_menu_keyboard():
    data = load_data()
    bot_on = data["bot_on"]
    toggle_text = "🛑 Turn OFF Bot" if bot_on else "🟢 Turn ON Bot"

    btn_toggle = InlineKeyboardButton(toggle_text, callback_data="admin_toggle_bot")
    btn_users = InlineKeyboardButton("👥 User List", callback_data="admin_users")
    btn_banned = InlineKeyboardButton("🚫 Banned List", callback_data="admin_banned")
    btn_ban = InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban_prompt")
    btn_unban = InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_prompt")
    btn_status = InlineKeyboardButton("📊 Bot Status", callback_data="admin_status")
    btn_broadcast = InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast_prompt")
    btn_help = InlineKeyboardButton("ℹ️ Help", url=HELP_URL)
    btn_back = InlineKeyboardButton("⬅️ Back", callback_data="admin_back_to_start")

    return InlineKeyboardMarkup([
        [btn_toggle],
        [btn_users, btn_banned],
        [btn_ban, btn_unban],
        [btn_status, btn_broadcast],
        [btn_help],
        [btn_back],
    ])


def user_list_keyboard(data):
    rows = []
    users = data["users"]
    banned = data["banned"]
    for uid in users:
        if uid in banned:
            btn = InlineKeyboardButton(f"✅ Unban {uid}", callback_data=f"unban_{uid}")
        else:
            btn = InlineKeyboardButton(f"🚫 Ban {uid}", callback_data=f"ban_{uid}")
        rows.append([btn])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_open_panel")])
    return InlineKeyboardMarkup(rows)


def banned_list_keyboard(data):
    rows = []
    for uid in data["banned"]:
        btn = InlineKeyboardButton(f"✅ Unban {uid}", callback_data=f"unban_{uid}")
        rows.append([btn])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_open_panel")])
    return InlineKeyboardMarkup(rows)


def broadcast_cancel_keyboard():
    btn_cancel = InlineKeyboardButton("❌ Cancel Broadcast", callback_data="admin_broadcast_cancel")
    return InlineKeyboardMarkup([[btn_cancel]])


# ============ TEXTS ============
def user_welcome_text(first_name: str = "User"):
    safe_name = esc(first_name)
    return (
        "<b>╔════════════════════════╗</b>\n"
        "<b>║   🌹  PRINCE SCRIPTER URL BOT         ║</b>\n"
        "<b>╚════════════════════════╝</b>\n"
        "\n"
        f"<b>👋 Hello, {safe_name}!</b>\n"
        "\n"
        "<b>✅ Welcome to the Ultimate URL Redirect Tracer Bot!</b>\n"
        "\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "<b>🔍  WHAT THIS BOT CAN DO</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "\n"
        "<b>• Trace full redirect chains of any URL</b>\n"
        "<b>• Detect hidden redirects &amp; meta refresh</b>\n"
        "<b>• Bypass shorteners (bit.ly, tinyurl, etc.)</b>\n"
        "<b>• Show HTTP response + final destination</b>\n"
        "<b>• Fast, accurate &amp; easy to use</b>\n"
        "\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "<b>🚀  HOW TO USE</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "\n"
        "<b>1️⃣  Just send me any URL</b>\n"
        "<b>2️⃣  Or tap 🔗 Insert Link below</b>\n"
        "<b>3️⃣  I'll trace and show you the full chain</b>\n"
        "\n"
        "<b>📌 Example:</b>\n"
        "<code>http://example.com/trace?offerid=123456</code>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "\n"
        "<b>💡 Tip: You can also use the /start command!</b>\n"
        "\n"
        "<b>🌹 Powered BY ➪ @Princescripter</b>"
    )


def admin_start_text(first_name: str = "Admin"):
    safe_name = esc(first_name)
    return (
        "<b>╔════════════════════════╗</b>\n"
        "<b>║  👑 PRINCE ADMIN CONTROL PANEL ║</b>\n"
        "<b>╚════════════════════════╝</b>\n"
        "\n"
        f"<b>👋 Welcome back, {safe_name}!</b>\n"
        "\n"
        "<b>✅ You have full administrative access to this bot.</b>\n"
        "\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "<b>⚙️  ADMIN CONTROLS</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "\n"
        "<b>• Turn the bot ON / OFF</b>\n"
        "<b>• View all registered users</b>\n"
        "<b>• Ban or unban users by ID</b>\n"
        "<b>• Broadcast messages to everyone</b>\n"
        "<b>• Monitor bot statistics</b>\n"
        "\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "\n"
        "<b>👉 Tap ⚙️ Admin Panel below to manage the bot.</b>\n"
        "<b>👉 Tap 🔗 Insert Link to trace a URL.</b>\n"
        "\n"
        "<b>🌹 Powered BY ➪ @Princescripter</b>"
    )


def admin_menu_text():
    data = load_data()
    status = "🟢 ON" if data["bot_on"] else "🔴 OFF"
    total_users = len(data["users"])
    total_banned = len(data["banned"])

    return (
        "<b>╔════════════════════════╗</b>\n"
        "<b>║  ⚙️ PRINCE ADMIN CONTROL MENU  ║</b>\n"
        "<b>╚════════════════════════╝</b>\n"
        "\n"
        f"<b>⦿ Bot Status ➪ {status}</b>\n"
        f"<b>⦿ Total Users ➪ {total_users}</b>\n"
        f"<b>⦿ Banned Users ➪ {total_banned}</b>\n"
        "\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "<b>🛠️  MANAGE OPTIONS</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "\n"
        "<b>🛑 Turn ON/OFF  — Toggle bot status</b>\n"
        "<b>👥 User List     — View all users</b>\n"
        "<b>🚫 Banned List   — View banned users</b>\n"
        "<b>🚫 Ban User      — Ban by user ID</b>\n"
        "<b>✅ Unban User    — Unban by user ID</b>\n"
        "<b>📊 Bot Status    — Show statistics</b>\n"
        "<b>📢 Broadcast     — Send message to all</b>\n"
        "<b>ℹ️ Help          — Help menu</b>\n"
        "\n"
        "<b>🌹 Powered BY ➪ @Princescripter</b>"
    )


def help_text():
    return (
        "<b>╔════════════════════════╗</b>\n"
        "<b>║        HELP MENU       ║</b>\n"
        "<b>╚════════════════════════╝</b>\n"
        "\n"
        "<b>📖  HOW TO USE</b>\n"
        "\n"
        "<b>• Send any URL to trace it</b>\n"
        "<b>• Or tap 🔗 Insert Link button</b>\n"
        "\n"
        "<b>🌹 Powered BY ➪ @Princescripter</b>\n"
        "<b>🤖 Bot ➪ @Princescripterbot</b>"
    )


# ============ AUTH ============
def is_admin(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == ADMIN_ID


# ============ COMMAND HANDLERS ============
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name or "User"

    if is_admin(update):
        register_user(user_id)
        await update.message.reply_text(
            admin_start_text(first_name),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_start_keyboard()
        )
        return

    if is_banned(user_id):
        await update.message.reply_text("<b>🚫 You are banned from using this bot.</b>", parse_mode=ParseMode.HTML)
        return

    register_user(user_id)
    await update.message.reply_text(
        user_welcome_text(first_name),
        parse_mode=ParseMode.HTML,
        reply_markup=user_keyboard()
    )


# ============ MESSAGE HANDLERS ============
async def url_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id

    if is_banned(user_id):
        await update.message.reply_text("<b>🚫 You are banned from using this bot.</b>", parse_mode=ParseMode.HTML)
        return

    data = load_data()
    if not data["bot_on"] and not is_admin(update):
        await update.message.reply_text("<b>🛑 Bot is currently OFF. Please try again later.</b>", parse_mode=ParseMode.HTML)
        return

    register_user(user_id)

    text = update.message.text.strip()

    is_url = (
        text.startswith("http://") or text.startswith("https://") or
        re.match(r'^[\w.-]+\.[a-z]{2,}(/\S*)?$', text, re.IGNORECASE)
    )

    if not is_url:
        await update.message.reply_text(
            "<b>❗ Please send a valid URL.</b>\n"
            "<b>Example:</b> <code>https://example.com</code>",
            parse_mode=ParseMode.HTML
        )
        return

    wait_msg = await update.message.reply_text("<b>⏳ PROCESSING...PLEASE WAITING.</b>", parse_mode=ParseMode.HTML)
    try:
        result = trace_url(text, None)
    except Exception as e:
        result = f"<b>❌ Unexpected error:</b> <code>{esc(str(e))}</code>"

    try:
        await wait_msg.edit_text(result, parse_mode=ParseMode.HTML)
    except Exception:
        await wait_msg.edit_text(result)


# ============ BAN/UNBAN CONVERSATION ============
async def ban_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Admin only.", show_alert=True)
        return
    await query.answer()
    await query.message.reply_text(
        "<b>🚫 Send the user ID you want to BAN.</b>\n"
        "<b>Example:</b> <code>123456789</code>\n\n"
        "<b>Send /cancel to cancel.</b>",
        parse_mode=ParseMode.HTML
    )
    return WAIT_BAN_ID


async def receive_ban_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == "/cancel":
        await update.message.reply_text("<b>❌ Cancelled.</b>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    if not text.lstrip("-").isdigit():
        await update.message.reply_text("<b>❗ Invalid ID. Send a numeric user ID or /cancel.</b>", parse_mode=ParseMode.HTML)
        return WAIT_BAN_ID

    uid = int(text)
    data = load_data()
    if uid not in data["banned"]:
        data["banned"].append(uid)
        save_data(data)
    await update.message.reply_text(f"<b>🚫 User {uid} has been banned.</b>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END


async def unban_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Admin only.", show_alert=True)
        return
    await query.answer()
    await query.message.reply_text(
        "<b>✅ Send the user ID you want to UNBAN.</b>\n"
        "<b>Example:</b> <code>123456789</code>\n\n"
        "<b>Send /cancel to cancel.</b>",
        parse_mode=ParseMode.HTML
    )
    return WAIT_UNBAN_ID


async def receive_unban_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == "/cancel":
        await update.message.reply_text("<b>❌ Cancelled.</b>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    if not text.lstrip("-").isdigit():
        await update.message.reply_text("<b>❗ Invalid ID. Send a numeric user ID or /cancel.</b>", parse_mode=ParseMode.HTML)
        return WAIT_UNBAN_ID

    uid = int(text)
    data = load_data()
    if uid in data["banned"]:
        data["banned"].remove(uid)
        save_data(data)
    await update.message.reply_text(f"<b>✅ User {uid} has been unbanned.</b>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END


# ============ BROADCAST CONVERSATION ============
async def broadcast_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Admin only.", show_alert=True)
        return
    await query.answer()
    await query.message.reply_text(
        "<b>📢 Broadcast System</b>\n\n"
        "<b>Send the message you want to broadcast to all users.</b>\n\n"
        "<b>✅ Text / Photo / Video / GIF / Document / Audio / Voice → Caption</b>\n"
        "<b>✅ Sticker / Location / Contact / Poll → As-is</b>\n\n"
        "<b>Tap ❌ Cancel Broadcast to cancel.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=broadcast_cancel_keyboard()
    )
    return WAIT_BROADCAST_MSG


async def receive_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return ConversationHandler.END

    msg = update.message

    if msg and msg.text and msg.text.strip() == "/cancel":
        await msg.reply_text("<b>❌ Broadcast cancelled.</b>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    data = load_data()
    users = data["users"]
    if not users:
        await msg.reply_text("<b>📭 No users to broadcast.</b>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    wait_msg = await msg.reply_text(
        "<b>⏳ Sending broadcast... please wait.</b>",
        parse_mode=ParseMode.HTML
    )

    raw_caption = msg.caption if msg.caption else None
    bold_caption = f"<b>{esc(raw_caption)}</b>" if raw_caption else None
    parse_caption = ParseMode.HTML if bold_caption else None

    success = 0
    failed = 0

    for uid in users:
        try:
            if msg.text:
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"<b>{esc(msg.text)}</b>",
                    parse_mode=ParseMode.HTML
                )
            elif msg.photo:
                await context.bot.send_photo(
                    chat_id=uid,
                    photo=msg.photo[-1].file_id,
                    caption=bold_caption,
                    parse_mode=parse_caption
                )
            elif msg.video:
                await context.bot.send_video(
                    chat_id=uid,
                    video=msg.video.file_id,
                    caption=bold_caption,
                    parse_mode=parse_caption
                )
            elif msg.animation:
                await context.bot.send_animation(
                    chat_id=uid,
                    animation=msg.animation.file_id,
                    caption=bold_caption,
                    parse_mode=parse_caption
                )
            elif msg.document:
                await context.bot.send_document(
                    chat_id=uid,
                    document=msg.document.file_id,
                    caption=bold_caption,
                    parse_mode=parse_caption
                )
            elif msg.audio:
                await context.bot.send_audio(
                    chat_id=uid,
                    audio=msg.audio.file_id,
                    caption=bold_caption,
                    parse_mode=parse_caption
                )
            elif msg.voice:
                await context.bot.send_voice(
                    chat_id=uid,
                    voice=msg.voice.file_id,
                    caption=bold_caption,
                    parse_mode=parse_caption
                )
            elif msg.video_note:
                await context.bot.send_video_note(
                    chat_id=uid,
                    video_note=msg.video_note.file_id
                )
            elif msg.sticker:
                await context.bot.send_sticker(
                    chat_id=uid,
                    sticker=msg.sticker.file_id
                )
            elif msg.location:
                await context.bot.send_location(
                    chat_id=uid,
                    latitude=msg.location.latitude,
                    longitude=msg.location.longitude
                )
            elif msg.contact:
                await context.bot.send_contact(
                    chat_id=uid,
                    phone_number=msg.contact.phone_number,
                    first_name=msg.contact.first_name,
                    last_name=msg.contact.last_name
                )
            elif msg.poll:
                await context.bot.send_poll(
                    chat_id=uid,
                    question=msg.poll.question,
                    options=[o.text for o in msg.poll.options],
                    is_anonymous=msg.poll.is_anonymous,
                    allows_multiple_answers=msg.poll.allows_multiple_answers
                )
            else:
                await context.bot.copy_message(
                    chat_id=uid,
                    from_chat_id=update.effective_chat.id,
                    message_id=msg.message_id
                )
            success += 1
        except Exception:
            failed += 1

    try:
        await wait_msg.edit_text(
            f"<b>📢 Broadcast done.</b>\n<b>✅ Sent: {success}</b>\n<b>❌ Failed: {failed}</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        await msg.reply_text(
            f"<b>📢 Broadcast done.</b>\n<b>✅ Sent: {success}</b>\n<b>❌ Failed: {failed}</b>",
            parse_mode=ParseMode.HTML
        )

    return ConversationHandler.END


async def broadcast_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Admin only.", show_alert=True)
        return
    await query.answer("Broadcast cancelled.")
    try:
        await query.message.edit_text("<b>❌ Broadcast cancelled.</b>", parse_mode=ParseMode.HTML)
    except Exception:
        await query.message.reply_text("<b>❌ Broadcast cancelled.</b>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("<b>❌ Cancelled.</b>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END


# ============ CALLBACKS ============
async def user_insert_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "<b>🔗 Please send the link you want to trace.</b>",
        parse_mode=ParseMode.HTML
    )


async def admin_open_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Admin only.", show_alert=True)
        return
    await query.answer()
    try:
        await query.message.edit_text(
            admin_menu_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu_keyboard()
        )
    except Exception:
        await query.message.reply_text(
            admin_menu_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu_keyboard()
        )


async def admin_back_to_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Admin only.", show_alert=True)
        return
    await query.answer()
    first_name = query.from_user.first_name or "Admin"
    try:
        await query.message.edit_text(
            admin_start_text(first_name),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_start_keyboard()
        )
    except Exception:
        await query.message.reply_text(
            admin_start_text(first_name),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_start_keyboard()
        )


async def admin_toggle_bot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Admin only.", show_alert=True)
        return

    data = load_data()
    data["bot_on"] = not data["bot_on"]
    save_data(data)
    status = "🟢 ON" if data["bot_on"] else "🔴 OFF"
    await query.answer(f"Bot is now {status}")

    try:
        await query.message.edit_text(
            admin_menu_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu_keyboard()
        )
    except Exception:
        pass


async def admin_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Admin only.", show_alert=True)
        return
    await query.answer()

    data = load_data()
    users = data["users"]
    if not users:
        await query.message.reply_text("<b>📭 No users yet.</b>", parse_mode=ParseMode.HTML)
        return

    await query.message.reply_text(
        f"<b>👥 Total Users: {len(users)}</b>\n\n<b>Tap a button to Ban/Unban:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=user_list_keyboard(data)
    )


async def admin_banned_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Admin only.", show_alert=True)
        return
    await query.answer()

    data = load_data()
    banned = data["banned"]
    if not banned:
        await query.message.reply_text("<b>📭 No banned users.</b>", parse_mode=ParseMode.HTML)
        return

    await query.message.reply_text(
        f"<b>🚫 Banned Users: {len(banned)}</b>\n\n<b>Tap to Unban:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=banned_list_keyboard(data)
    )


async def admin_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Admin only.", show_alert=True)
        return
    await query.answer()

    data = load_data()
    status = "🟢 ON" if data["bot_on"] else "🔴 OFF"
    text = (
        f"<b>⦿ Bot Status ➪ {status}</b>\n"
        f"<b>⦿ Total Users ➪ {len(data['users'])}</b>\n"
        f"<b>⦿ Banned Users ➪ {len(data['banned'])}</b>"
    )
    await query.message.reply_text(text, parse_mode=ParseMode.HTML)


async def admin_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Admin only.", show_alert=True)
        return
    await query.answer()
    await query.message.reply_text(help_text(), parse_mode=ParseMode.HTML)


async def quick_ban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Admin only.", show_alert=True)
        return

    uid = int(query.data.split("_", 1)[1])
    data = load_data()
    if uid not in data["banned"]:
        data["banned"].append(uid)
        save_data(data)
        await query.answer(f"🚫 Banned {uid}")
    else:
        await query.answer("Already banned.")

    users = data["users"]
    try:
        await query.message.edit_text(
            f"<b>👥 Total Users: {len(users)}</b>\n\n<b>Tap a button to Ban/Unban:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=user_list_keyboard(data)
        )
    except Exception:
        pass


async def quick_unban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Admin only.", show_alert=True)
        return

    uid = int(query.data.split("_", 1)[1])
    data = load_data()
    if uid in data["banned"]:
        data["banned"].remove(uid)
        save_data(data)
        await query.answer(f"✅ Unbanned {uid}")
    else:
        await query.answer("Not banned.")

    try:
        await query.message.edit_text(
            f"<b>👥 Total Users: {len(data['users'])}</b>\n\n<b>Tap a button to Ban/Unban:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=user_list_keyboard(data)
        )
    except Exception:
        try:
            if data["banned"]:
                await query.message.edit_text(
                    f"<b>🚫 Banned Users: {len(data['banned'])}</b>\n\n<b>Tap to Unban:</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=banned_list_keyboard(data)
                )
            else:
                await query.message.reply_text("<b>📭 No banned users.</b>", parse_mode=ParseMode.HTML)
        except Exception:
            pass


# ============ ERROR HANDLER ============
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "<b>⚠️ Something went wrong. Please try again.</b>",
                parse_mode=ParseMode.HTML
            )
    except Exception:
        pass


# ============ MAIN ============
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    ban_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(ban_prompt_callback, pattern="^admin_ban_prompt$"),
            CallbackQueryHandler(unban_prompt_callback, pattern="^admin_unban_prompt$"),
        ],
        states={
            WAIT_BAN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ban_id)],
            WAIT_UNBAN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_unban_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
    )

    broadcast_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(broadcast_prompt_callback, pattern="^admin_broadcast_prompt$"),
        ],
        states={
            WAIT_BROADCAST_MSG: [
                MessageHandler(filters.ALL & ~filters.COMMAND, receive_broadcast_msg),
                CallbackQueryHandler(broadcast_cancel_callback, pattern="^admin_broadcast_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
    )

    # ---- Only /start command ----
    app.add_handler(CommandHandler("start", start_cmd))

    # ---- Conversations ----
    app.add_handler(ban_conv)
    app.add_handler(broadcast_conv)

    # ---- Callbacks ----
    app.add_handler(CallbackQueryHandler(user_insert_link_callback, pattern="^user_insert_link$"))
    app.add_handler(CallbackQueryHandler(admin_open_panel_callback, pattern="^admin_open_panel$"))
    app.add_handler(CallbackQueryHandler(admin_back_to_start_callback, pattern="^admin_back_to_start$"))
    app.add_handler(CallbackQueryHandler(admin_toggle_bot_callback, pattern="^admin_toggle_bot$"))
    app.add_handler(CallbackQueryHandler(admin_users_callback, pattern="^admin_users$"))
    app.add_handler(CallbackQueryHandler(admin_banned_callback, pattern="^admin_banned$"))
    app.add_handler(CallbackQueryHandler(admin_status_callback, pattern="^admin_status$"))
    app.add_handler(CallbackQueryHandler(admin_help_callback, pattern="^admin_help$"))
    app.add_handler(CallbackQueryHandler(quick_ban_callback, pattern=r"^ban_\d+$"))
    app.add_handler(CallbackQueryHandler(quick_unban_callback, pattern=r"^unban_\d+$"))

    # ---- Plain text (URL) ----
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, url_message_handler))

    app.add_error_handler(error_handler)

    logger.info("Bot started. Admin ID: %s", ADMIN_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()