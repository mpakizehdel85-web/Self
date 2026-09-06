import asyncio
import html
import os
import re
import threading
import time
import json
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from telethon import TelegramClient, events, functions
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.types import MessageEntityCustomEmoji

# ============================================================
# SETTINGS & CACHE
# ============================================================

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
PHONE = os.environ["TELEGRAM_PHONE"]

TELEGRAM_SESSION = os.environ.get("TELEGRAM_SESSION", "").strip()
PASSWORD_2FA = os.environ.get("TELEGRAM_2FA_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8000"))
START_TIME = time.time()

CACHE_FILE = "bat_cache.json"

if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            bat_cache = json.load(f)
    except Exception:
        bat_cache = {}
else:
    bat_cache = {}

# ============================================================
# TELEGRAM CLIENT
# ============================================================

if TELEGRAM_SESSION:
    print("[SESSION] TELEGRAM_SESSION found.")
    SESSION = StringSession(TELEGRAM_SESSION)
else:
    print("[SESSION] No TELEGRAM_SESSION found.")
    SESSION = StringSession()

client = TelegramClient(
    SESSION,
    API_ID,
    API_HASH,
    auto_reconnect=True,
    connection_retries=None,
    request_retries=5,
    retry_delay=5,
    flood_sleep_threshold=60,
)

# ============================================================
# LOGIN WEB PAGE
# ============================================================

login_state = "starting"
login_message = "Connecting to Telegram..."
MAIN_LOOP = None

code_queue = asyncio.Queue()
password_queue = asyncio.Queue()

def set_login_state(state, message):
    global login_state, login_message
    login_state = state
    login_message = message
    print("[LOGIN]", message)

def page_template(content):
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telegram Userbot</title>
<style>
body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:#10131a; color:#fff; font-family:system-ui,sans-serif; }}
main {{ width:min(92vw,400px); padding:28px; box-sizing:border-box; border-radius:16px; background:#191e28; border:1px solid #303746; }}
input {{ width:100%; box-sizing:border-box; padding:12px; margin-top:8px; border-radius:9px; border:1px solid #465064; background:#0d1117; color:#fff; font-size:17px; }}
button {{ width:100%; margin-top:18px; padding:12px; border:0; border-radius:9px; background:#4f8cff; color:white; font-size:16px; font-weight:700; }}
p {{ color:#b8c0cf; line-height:1.5; }}
</style>
</head>
<body>
<main>{content}</main>
</body>
</html>"""

def login_page():
    if login_state == "code":
        return page_template("""
<h2>Telegram Login</h2>
<p>کد یک‌بارمصرف تلگرام را وارد کن.</p>
<form method="post" action="/code" autocomplete="off">
<label>Login Code</label>
<input name="code" type="text" inputmode="numeric" autocomplete="one-time-code" required>
<button type="submit">ورود</button>
</form>
""")
    if login_state == "password":
        return page_template("""
<h2>Two-Step Verification</h2>
<p>رمز دو مرحله‌ای تلگرام را وارد کن.</p>
<form method="post" action="/password" autocomplete="off">
<label>2FA Password</label>
<input name="password" type="password" autocomplete="current-password" required>
<button type="submit">ادامه</button>
</form>
""")
    if login_state == "authenticated":
        return page_template("""
<h2>✅ Telegram Connected</h2>
<p>Userbot با موفقیت متصل شده است.</p>
""")
    return page_template(f"""
<h2>Telegram Userbot</h2>
<p>{html.escape(login_message)}</p>
<meta http-equiv="refresh" content="2">
""")

class LoginHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = login_page().encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        values = parse_qs(body, keep_blank_values=True)

        if self.path == "/code":
            code = values.get("code", [""])[0].strip()
            if code.isdigit() and MAIN_LOOP:
                asyncio.run_coroutine_threadsafe(code_queue.put(code), MAIN_LOOP)
            self.redirect()
            return

        if self.path == "/password":
            password = values.get("password", [""])[0]
            if password and MAIN_LOOP:
                asyncio.run_coroutine_threadsafe(password_queue.put(password), MAIN_LOOP)
            self.redirect()
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def redirect(self):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, format, *args):
        return

def start_web_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), LoginHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[WEB] Login page running on port {PORT}")
    return server

# ============================================================
# AUTHENTICATION
# ============================================================

async def authenticate():
    await client.connect()
    if await client.is_user_authorized():
        set_login_state("authenticated", "Existing Telegram session is valid.")
        return

    set_login_state("starting", "Requesting a new Telegram login code...")
    await client.send_code_request(PHONE)
    set_login_state("code", "Telegram login code requested.")
    code = await code_queue.get()

    try:
        await client.sign_in(phone=PHONE, code=code)
    except SessionPasswordNeededError:
        set_login_state("password", "Telegram requires your 2FA password.")
        password = PASSWORD_2FA if PASSWORD_2FA else await password_queue.get()
        await client.sign_in(password=password)

    set_login_state("authenticated", "Authentication successful.")

# ============================================================
# HELPERS & COMMAND REGISTRY
# ============================================================

async def get_chat_display_info(chat_id):
    try:
        chat = await client.get_entity(chat_id)
        name = getattr(chat, 'title', None) or getattr(chat, 'first_name', 'Unknown')
        if getattr(chat, 'username', None):
            return f"[{name}](https://t.me/{chat.username})"
        else:
            return f"{name} (`{chat_id}`)"
    except Exception:
        return f"Chat ID: `{chat_id}`"

COMMAND_DESCRIPTIONS = {
    ".session": "دریافت رشته سشن",
    ".set": "زمان‌بندی ارسال پیام",
    ".reply": "تنظیم پاسخ خودکار",
    ".stopreply": "توقف پاسخ خودکار",
    ".cat": "حالت نجات پیشی (مخفی)",
    ".stopcat": "توقف نجات پیشی",
    ".khofash": "شکارچی خودکار خفاش (بهینه‌شده)",
    ".stopkhofash": "توقف شکارچی خفاش",
    ".delete": "پاکسازی پیام‌ها",
    ".save": "ذخیره پیام در سیو مسیج",
    ".uptime": "آب‌تایم بات",
    ".fish": "اتوماسیون ماهی خودکار",
    ".stopfish": "توقف اتوماسیون ماهی",
    ".automeo": "ارسال خودکار meo",
    ".stopautomeo": "توقف ارسال خودکار meo",
    ".autoreact": "تنظیم ریکشن خودکار",
    ".stopautoreact": "توقف ریکشن خودکار",
    ".readmentions": "سین کردن منشن‌ها",
    ".userinfo": "اطلاعات حساب کاربر",
    ".tag": "تگ کردن هوشمند کاربران",
    ".kazino": "اتوماسیون کازینو",
    ".stopkazino": "توقف اتوماسیون کازینو",
    ".stopall": "توقف تمام قابلیت‌ها",
    ".status": "گزارش وضعیت بات",
    ".i": "فهرست دستورات",
    ".ping": "بررسی آنلاین بودن",
    ".whoami": "اطلاعات حساب کاربری"
}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.i$"))
async def short_help_list(event):
    lines = ["📋 **لیست خلاصه دستورات:**\n"]
    for cmd, desc in COMMAND_DESCRIPTIONS.items():
        lines.append(f"`{cmd}` : {desc}")
    await event.edit("\n".join(lines))

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.session$"))
async def send_session(event):
    try:
        session_string = client.session.save()
        if not session_string:
            await event.edit("❌ Session هنوز آماده نیست.")
            return
        await client.send_message("me", session_string)
        await event.edit("✅ TELEGRAM_SESSION در Saved Messages ارسال شد.")
    except Exception as error:
        await event.edit(f"❌ خطا:\n{error}")

def parse_interval(value):
    value = value.strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(s|m|h)", value)
    if match:
        number = float(match.group(1))
        unit = match.group(2)
        if unit == "s": return number
        if unit == "m": return number * 60
        if unit == "h": return number * 3600
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value) * 60
    return None

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.set(?:\s|$)?"))
async def set_scheduled_messages(event):
    match = re.fullmatch(r"\.set\s+(\d+)\s+(.+?)\s+(\d+(?:\.\d+)?[smh]?)", event.raw_text.strip(), re.IGNORECASE)
    if not match:
        await event.edit("❌ فرمت اشتباه.\nمثال:\n`.set 3 سلام 5m`")
        return
    count = int(match.group(1))
    message_text = match.group(2).strip()
    interval = parse_interval(match.group(3))
    if count <= 0 or interval is None or interval <= 0:
        await event.edit("❌ مقادیر نامعتبر است.")
        return
    now = datetime.now(timezone.utc)
    try:
        for index in range(1, count + 1):
            schedule_time = now + timedelta(seconds=interval * index)
            await client.send_message(event.chat_id, message_text, schedule=schedule_time)
        await event.edit(f"✅ {count} پیام زمان‌بندی شد.")
    except Exception as error:
        await event.edit(f"❌ خطا: {error}")

reply_rules = {}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.reply(?:\s|$)?"))
async def create_reply(event):
    match = re.fullmatch(r"\.reply\s+(.+?)\s+to\s+(.+)", event.raw_text.strip(), re.IGNORECASE)
    if not match:
        await event.edit("❌ فرمت:\n`.reply جواب to متن`")
        return
    response = match.group(1).strip()
    trigger = match.group(2).strip()
    if event.chat_id not in reply_rules:
        reply_rules[event.chat_id] = {}
    reply_rules[event.chat_id][trigger.casefold()] = response
    await event.edit(f"✅ ریپلای فعال شد\nهدف: {trigger}\nپاسخ: {response}")

@client.on(events.NewMessage())
async def automatic_reply(event):
    if event.out or event.reply_to_msg_id:
        return
    chat_id = event.chat_id
    if chat_id not in reply_rules:
        return
    incoming = event.raw_text.strip()
    response = reply_rules[chat_id].get(incoming.casefold())
    if response:
        try:
            await event.reply(response)
        except Exception:
            pass

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopreply$"))
async def stop_reply(event):
    reply_rules.pop(event.chat_id, None)
    await event.edit("🛑 ریپلای خودکار متوقف شد.")

cat_chats = set()

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.cat$"))
async def start_cat(event):
    cat_chats.add(event.chat_id)
    try:
        await event.delete()
    except Exception:
        pass

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopcat$"))
async def stop_cat(event):
    cat_chats.discard(event.chat_id)
    await event.edit("🛑 حالت نجات پیشی متوقف شد.")

async def check_cat_message(message):
    if message.chat_id not in cat_chats or not message.buttons:
        return
    for row in message.buttons:
        for button in row:
            text = getattr(button, "text", "")
            if text and "نجات پیشی خیابونی" in text:
                try:
                    await message.click(text=text)
                except Exception:
                    pass
                return

@client.on(events.NewMessage())
async def cat_new_message(event):
    await check_cat_message(event.message)

@client.on(events.MessageEdited())
async def cat_edited_message(event):
    await check_cat_message(event.message)

fish_task_running = None

async def run_fish_workflow(client, chat_id):
    try:
        await client.send_message(chat_id, "ماهی")
        await asyncio.sleep(4)
        async for message in client.iter_messages(chat_id, limit=3):
            if message.text and message.buttons:
                target_text = "بندازش تو یخچال" if ("افسانه‌ای" in message.text or "افسانه ای" in message.text) else "فروش ماهی"
                clicked = False
                for row in message.buttons:
                    for button in row:
                        if target_text in getattr(button, "text", ""):
                            await button.click()
                            clicked = True
                            break
                    if clicked:
                        break
                break
    except Exception as error:
        print("[FISH ERROR]", error)

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.fish(?:\s|$)?"))
async def start_fish_loop(event):
    global fish_task_running
    cmd_text = event.raw_text.strip()
    match = re.search(r"^\.fish\s+(.+)$", cmd_text, re.IGNORECASE)
    if not match:
        await event.edit("❌ فرمت زمان اشتباه است.\nمثال: `.fish 11m`")
        return
    interval_str = match.group(1).strip()
    interval_seconds = parse_interval(interval_str)
    if interval_seconds is None or interval_seconds <= 0:
        await event.edit("❌ فرمت زمان نامعتبر است.")
        return
    chat_id = event.chat_id
    await event.edit(f"🎣 اتوماسیون ماهی فعال شد (هر {interval_str}).")

    async def loop_job():
        while True:
            await run_fish_workflow(client, chat_id)
            await asyncio.sleep(interval_seconds)

    if fish_task_running:
        fish_task_running.cancel()
    fish_task_running = asyncio.create_task(loop_job())

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopfish$"))
async def stop_fish_loop(event):
    global fish_task_running
    if fish_task_running:
        fish_task_running.cancel()
        fish_task_running = None
        await event.edit("🛑 اتوماسیون ماهی متوقف شد.")
    else:
        await event.edit("❌ هیچ اتوماسیونی فعالی وجود ندارد.")
# ============================================================
# .KHOFASH (ULTRA-FAST PRE-CACHED & AUTO-LEARNING BAT HUNTER)
# ============================================================

khofash_chats = set()
pending_learning = {}

BAT_CODE_MAPPING = {
    "1": "✨", "2": "🧄", "3": "👀", "4": "👶", "5": "💦", "6": "👾",
    "7": "🌦️", "8": "💨", "9": "⚫️", "10": "🕷️", "11": "🧼", "12": "🐥",
    "13": "💙", "14": "💙", "15": "🙍‍♀", "16": "🧽", "17": "🌹", "18": "🤖",
    "19": "💥", "20": "🍋", "21": "🎭", "22": "🗻", "23": "🪞", "24": "🃏",
    "25": "❤️", "26": "🚒", "27": "🌕", "28": "🧛", "29": "🧊", "30": "😇",
    "31": "😈", "32": "🔥", "33": "🇫🇷", "34": "⭐️", "35": "🌧", "36": "🪙",
    "37": "⚡️", "38": "🌑"
}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.khofash$"))
async def start_khofash(event):
    khofash_chats.add(event.chat_id)
    try:
        await event.delete()
    except Exception:
        pass

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopkhofash$"))
async def stop_khofash(event):
    khofash_chats.discard(event.chat_id)
    await event.edit("🛑 شکارچی خفاش متوقف شد.")

@client.on(events.NewMessage())
async def instant_khofash_hunter(event):
    if event.chat_id not in khofash_chats:
        return
    
    msg = event.message
    custom_emoji_id = None

    if msg.entities:
        for entity in msg.entities:
            if isinstance(entity, MessageEntityCustomEmoji):
                custom_emoji_id = str(entity.document_id)
                break

    if custom_emoji_id:
        if custom_emoji_id in bat_cache:
            code = bat_cache[custom_emoji_id]
            emoji_response = BAT_CODE_MAPPING.get(str(code))
            if emoji_response:
                try:
                    await event.reply(emoji_response)
                except Exception:
                    pass
            return
        
        pending_learning[event.sender_id] = {
            "emoji_id": custom_emoji_id,
            "msg_id": msg.id
        }
        return

    if event.sender_id in pending_learning:
        text = event.raw_text or ""
        if "کد" in text or "عدد" in text:
            match = re.search(r"کد\s*:\s*(\d+)", text) or re.search(r"(\d+)", text)
            if match:
                data = pending_learning.pop(event.sender_id)
                code = match.group(1)
                bat_cache[data["emoji_id"]] = code
                try:
                    with open(CACHE_FILE, "w", encoding="utf-8") as f:
                        json.dump(bat_cache, f, ensure_ascii=False)
                except Exception as err:
                    print("[CACHE SAVE ERROR]", err)

# ============================================================
# .DELETE & .SAVE
# ============================================================

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.delete(?:\s+(\d+))?$"))
async def delete_messages(event):
    match = event.pattern_match
    count = int(match.group(1)) if match.group(1) else 10
    deleted = 0
    async for message in client.iter_messages(event.chat_id, limit=count, from_user="me"):
        try:
            await message.delete()
            deleted += 1
        except Exception:
            pass

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.save$"))
async def save_message(event):
    if not event.is_reply:
        await event.edit("❌ لطفا روی پیام مورد نظر ریپلای بزنید.")
        return
    reply_msg = await event.get_reply_message()
    await client.forward_messages("me", reply_msg)
    await event.edit("✅ در Saved Messages ذخیره شد.")

# ============================================================
# .UPTIME, .AUTOMEO, .AUTOREACT, .READMENTIONS, .USERINFO
# ============================================================

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.uptime$"))
async def uptime_bot(event):
    uptime_seconds = int(time.time() - START_TIME)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    await event.edit(f"⏱ **آب‌تایم بات:** {hours} ساعت و {minutes} دقیقه و {seconds} ثانیه")

automeo_tasks = {}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.automeo$"))
async def start_automeo(event):
    chat_id = event.chat_id
    if chat_id in automeo_tasks:
        await event.edit("⚠️ ارسال خودکار meo از قبل فعال است.")
        return
    await event.edit("🐱 **ارسال خودکار meo فعال شد.**")

    async def meo_loop():
        while True:
            try:
                await client.send_message(chat_id, "meo")
            except Exception:
                pass
            await asyncio.sleep(300)

    automeo_tasks[chat_id] = asyncio.create_task(meo_loop())

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopautomeo$"))
async def stop_automeo(event):
    task = automeo_tasks.pop(event.chat_id, None)
    if task:
        task.cancel()
        await event.edit("🛑 ارسال خودکار متوقف شد.")
    else:
        await event.edit("❌ غیرفعال است.")

autoreact_rules = {}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.autoreact(?:\s|$)"))
async def set_autoreact(event):
    cmd_text = event.raw_text.strip()
    match = re.match(r"^\.autoreact\s+(.+?)\s+([^\s]+)$", cmd_text)
    target, emoji = (match.group(1).strip(), match.group(2).strip()) if match else (None, None)
    
    if not target and event.is_reply:
        parts = cmd_text.split()
        if len(parts) == 2:
            emoji = parts[1].strip()
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.sender_id:
                target = str(reply_msg.sender_id)

    if not target or not emoji:
        await event.edit("❌ فرمت اشتباه.")
        return

    if event.chat_id not in autoreact_rules:
        autoreact_rules[event.chat_id] = {}
    autoreact_rules[event.chat_id][target.casefold()] = emoji
    await event.edit("✅ ریکشن خودکار فعال شد.")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopautoreact$"))
async def stop_autoreact(event):
    autoreact_rules.pop(event.chat_id, None)
    await event.edit("🛑 ریکشن خودکار متوقف شد.")

@client.on(events.NewMessage())
async def handle_autoreact(event):
    chat_id = event.chat_id
    if chat_id not in autoreact_rules:
        return
    sender = await event.get_sender()
    sender_id_str = str(sender.id) if sender else ""
    sender_username = f"@{sender.username}".casefold() if sender and getattr(sender, 'username', None) else ""
    msg_text = event.raw_text or ""

    for target, emoji in autoreact_rules[chat_id].items():
        t_clean = target.casefold()
        matched = (t_clean == sender_id_str or t_clean == sender_username) if (t_clean.startswith("@") or t_clean.isdigit()) else (t_clean in msg_text.casefold())
        if matched:
            try:
                from telethon.tl.functions.messages import SendReactionRequest
                from telethon.tl.types import ReactionEmoji
                await client(SendReactionRequest(peer=event.chat_id, msg_id=event.id, reaction=[ReactionEmoji(emoticon=emoji)]))
            except Exception:
                pass
            break

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.readmentions$"))
async def read_mentions(event):
    try:
        await client(functions.messages.ReadMentionsRequest(peer=event.chat_id))
        await event.edit("✅ منشن‌ها سین شدند.")
    except Exception as error:
        await event.edit(f"❌ خطا: {error}")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.userinfo(?:\s|$)?"))
async def user_info(event):
    try:
        match = re.match(r"^\.userinfo\s+(.+)$", event.raw_text.strip())
        target = await client.get_entity(match.group(1).strip()) if match else (await (await event.get_reply_message()).get_sender() if event.is_reply else await event.get_sender())
        if not target:
            await event.edit("❌ یافت نشد.")
            return
        name = f"{target.first_name or ''} {target.last_name or ''}".strip()
        await event.edit(f"👤 **مشخصات:**\n• نام: `{name}`\n• آیدی: `{target.id}`\n• یوزرنیم: @{target.username or 'ندارد'}")
    except Exception as error:
        await event.edit(f"❌ خطا: {error}")

# ============================================================
# .TAG, .KAZINO, .STOPALL, .STATUS, .PING, .WHOAMI & MAIN
# ============================================================

recent_tagged = {}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.tag(?:\s+(\d+))?$"))
async def tag_users(event):
    if not event.is_group and not event.is_channel:
        await event.edit("❌ فقط در گروه‌ها.")
        return
    count = min(int(event.pattern_match.group(1) or 10), 100)
    chat_id = event.chat_id
    if chat_id not in recent_tagged:
        recent_tagged[chat_id] = []
    
    seen, pool, me_id = set(), [], (await client.get_me()).id
    try:
        async for msg in client.iter_messages(chat_id, limit=200):
            if not msg.sender_id or msg.sender_id in seen or msg.sender_id == me_id or msg.sender_id in recent_tagged[chat_id]:
                continue
            user = msg.sender
            if not user or user.bot or user.deleted:
                continue
            seen.add(msg.sender_id)
            mention = f"@{user.username}" if user.username else f"[{user.first_name or 'دوست'}](tg://user?id={user.id})"
            pool.append((msg.sender_id, mention))

        import random
        random.shuffle(pool)
        selected = pool[:count]
        if not selected:
            recent_tagged[chat_id].clear()
            await event.edit("🔄 لیست خالی شد، مجدد تلاش کنید.")
            return

        for uid, _ in selected:
            recent_tagged[chat_id].append(uid)
        
        users_list = [item[1] for item in selected]
        for i in range(0, len(users_list), 5):
            await client.send_message(chat_id, "👥 **دوستان:** " + " ".join(users_list[i:i+5]))
            await asyncio.sleep(1.5)
        await event.delete()
    except Exception as error:
        await event.edit(f"❌ خطا: {error}")

kazino_active_chats = set()

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.kazino(?:\s+(.+))?$"))
async def start_kazino(event):
    emoji = event.pattern_match.group(1).strip() if event.pattern_match.group(1) else "🎰"
    target_vals = {"🎰": 64, "🎲": 6, "🎯": 6, "🎳": 6, "🏀": 5, "⚽": 5}
    winning = target_vals.get(emoji, 6)
    if not event.is_reply:
        try: await event.delete()
        except Exception: pass
        return
    reply_msg = await event.get_reply_message()
    chat_id = event.chat_id
    kazino_active_chats.add(chat_id)
    try: await event.delete()
    except Exception: pass

    try:
        from telethon.tl.types import InputMediaDice
        while chat_id in kazino_active_chats:
            sent = await client.send_message(chat_id, file=InputMediaDice(emoticon=emoji), reply_to=reply_msg.id)
            if sent.media and getattr(sent.media, 'value', None) == winning:
                kazino_active_chats.discard(chat_id)
                break
            try: await sent.delete()
            except Exception: pass
            await asyncio.sleep(0.02)
    except Exception:
        kazino_active_chats.discard(chat_id)

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopkazino$"))
async def stop_kazino(event):
    kazino_active_chats.discard(event.chat_id)
    try: await event.delete()
    except Exception: pass

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopall$"))
async def stop_all_features(event):
    global fish_task_running
    if fish_task_running:
        fish_task_running.cancel()
        fish_task_running = None
    for task in automeo_tasks.values(): task.cancel()
    automeo_tasks.clear()
    reply_rules.clear()
    cat_chats.clear()
    autoreact_rules.clear()
    kazino_active_chats.clear()
    khofash_chats.clear()
    await event.edit("🛑 تمام قابلیت‌ها متوقف و پاکسازی شدند.")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.status$"))
async def bot_status_report(event):
    await event.edit(f"📊 **وضعیت بات:**\n• شکارچی خفاش: {'فعال' if khofash_chats else 'غیرفعال'}\n• ماهی: {'فعال' if fish_task_running else 'غیرفعال'}\n• پیشی: {'فعال' if cat_chats else 'غیرفعال'}")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
async def ping(event):
    await event.edit("✅ Userbot is online.")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.whoami$"))
async def whoami(event):
    me = await client.get_me()
    await event.edit(f"Name: {me.first_name or ''}\nUsername: @{me.username or 'none'}\nID: {me.id}")

async def main():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    start_web_server()
    print("Telegram Userbot starting...")
    await authenticate()
    me = await client.get_me()
    print(f"✅ USERBOT CONNECTED: {me.first_name}")
    await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Userbot stopped.")
    except Exception as error:
        print("USERBOT ERROR:", error)
        raise
