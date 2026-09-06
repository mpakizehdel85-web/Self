import asyncio
import html
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from telethon import TelegramClient, events, functions
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

# ============================================================
# SETTINGS
# ============================================================

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
PHONE = os.environ["TELEGRAM_PHONE"]

TELEGRAM_SESSION = os.environ.get("TELEGRAM_SESSION", "").strip()
PASSWORD_2FA = os.environ.get("TELEGRAM_2FA_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8000"))
START_TIME = time.time()

# ============================================================
# TELEGRAM CLIENT
# ============================================================

if TELEGRAM_SESSION:
    SESSION = StringSession(TELEGRAM_SESSION)
else:
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
<input name="code" type="text" inputmode="numeric" required>
<button type="submit">ورود</button>
</form>
""")
    if login_state == "password":
        return page_template("""
<h2>Two-Step Verification</h2>
<p>رمز دو مرحله‌ای تلگرام را وارد کن.</p>
<form method="post" action="/password" autocomplete="off">
<label>2FA Password</label>
<input name="password" type="password" required>
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
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        values = parse_qs(body, keep_blank_values=True)
        if self.path == "/code":
            code = values.get("code", [""])[0].strip()
            if MAIN_LOOP:
                asyncio.run_coroutine_threadsafe(code_queue.put(code), MAIN_LOOP)
            self.redirect()
            return
        if self.path == "/password":
            password = values.get("password", [""])[0]
            if MAIN_LOOP:
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

async def authenticate():
    await client.connect()
    if await client.is_user_authorized():
        set_login_state("authenticated", "Session is valid.")
        return
    await client.send_code_request(PHONE)
    set_login_state("code", "Code requested.")
    code = await code_queue.get()
    try:
        await client.sign_in(phone=PHONE, code=code)
    except SessionPasswordNeededError:
        set_login_state("password", "2FA required.")
        password = PASSWORD_2FA if PASSWORD_2FA else await password_queue.get()
        await client.sign_in(password=password)
    set_login_state("authenticated", "Done.")

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

# ============================================================
# COMMANDS: HELP & SESSION & SET & REPLY & CAT
# ============================================================

COMMAND_DESCRIPTIONS = {
    ".session": "دریافت رشته سشن",
    ".set": "زمان‌بندی ارسال پیام",
    ".reply": "تنظیم پاسخ خودکار",
    ".stopreply": "توقف پاسخ خودکار",
    ".cat": "نجات پیشی",
    ".stopcat": "توقف نجات پیشی",
    ".fish": "اتوماسیون ماهی",
    ".stopfish": "توقف اتوماسیون ماهی",
    ".automeo": "ارسال خودکار meo",
    ".stopautomeo": "توقف ارسال خودکار meo",
    ".autoreact": "ریکشن خودکار",
    ".stopautoreact": "توقف ریکشن خودکار",
    ".khofash": "شکارچی خفاش فوق‌سریع",
    ".stopkhofash": "توقف شکارچی خفاش",
    ".stopall": "توقف تمام قابلیت‌ها",
    ".status": "گزارش وضعیت"
}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.i$"))
async def short_help_list(event):
    lines = ["📋 **لیست دستورات:**\n"]
    for cmd, desc in COMMAND_DESCRIPTIONS.items():
        lines.append(f"`{cmd}` : {desc}")
    await event.edit("\n".join(lines))

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.session$"))
async def send_session(event):
    await client.send_message("me", client.session.save())
    await event.edit("✅ سشن در Saved Messages ارسال شد.")

def parse_interval(value):
    value = value.strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(s|m|h)", value)
    if match:
        num = float(match.group(1))
        unit = match.group(2)
        return num if unit == "s" else (num * 60 if unit == "m" else num * 3600)
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value) * 60
    return None

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.set(?:\s|$)?"))
async def set_scheduled_messages(event):
    match = re.fullmatch(r"\.set\s+(\d+)\s+(.+?)\s+(\d+(?:\.\d+)?[smh]?)", event.raw_text.strip(), re.IGNORECASE)
    if not match:
        await event.edit("❌ فرمت اشتباه. مثال: `.set 3 سلام 5m`")
        return
    count, text, interval = int(match.group(1)), match.group(2).strip(), parse_interval(match.group(3))
    now = datetime.now(timezone.utc)
    for i in range(1, count + 1):
        await client.send_message(event.chat_id, text, schedule=now + timedelta(seconds=interval * i))
    await event.edit(f"✅ {count} پیام زمان‌بندی شد.")

reply_rules = {}
@client.on(events.NewMessage(outgoing=True, pattern=r"^\.reply(?:\s|$)?"))
async def create_reply(event):
    match = re.fullmatch(r"\.reply\s+(.+?)\s+to\s+(.+)", event.raw_text.strip(), re.IGNORECASE)
    if not match: return
    resp, trig = match.group(1).strip(), match.group(2).strip()
    reply_rules.setdefault(event.chat_id, {})[trig.casefold()] = resp
    await event.edit(f"✅ ریپلای فعال شد: {trig} -> {resp}")

@client.on(events.NewMessage())
async def automatic_reply(event):
    if event.out or event.reply_to_msg_id: return
    resp = reply_rules.get(event.chat_id, {}).get(event.raw_text.strip().casefold())
    if resp: await event.reply(resp)

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopreply$"))
async def stop_reply(event):
    reply_rules.pop(event.chat_id, None)
    await event.edit("🛑 ریپلای متوقف شد.")

cat_chats = set()
@client.on(events.NewMessage(outgoing=True, pattern=r"^\.cat$"))
async def start_cat(event):
    cat_chats.add(event.chat_id)
    await event.delete()

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopcat$"))
async def stop_cat(event):
    cat_chats.discard(event.chat_id)
    await event.edit("🛑 گربه متوقف شد.")

@client.on(events.NewMessage())
async def cat_handler(event):
    if event.chat_id in cat_chats and event.message.buttons:
        for row in event.message.buttons:
            for btn in row:
                if "نجات پیشی" in getattr(btn, "text", ""):
                    await event.message.click(text=btn.text)
                    return
# ============================================================
# .FISH & .AUTOMEO & .AUTOREACT & .KHOFASH
# ============================================================

fish_chats = set()
automeo_chats = set()
autoreact_chats = set()
khofash_chats = set()

BAT_CODE_MAPPING = {
    "1": "✨", "2": "🧄", "3": "👀", "4": "👶", "5": "💦", "6": "👾",
    "7": "🌦️", "8": "💨", "9": "⚫️", "10": "🕷️", "11": "🧼", "12": "🐥",
    "13": "💙", "14": "💙", "15": "🙍‍♀", "16": "🧽", "17": "🌹", "18": "🤖",
    "19": "💥", "20": "🍋", "21": "🎭", "22": "🗻", "23": "🪞", "24": "🃏",
    "25": "❤️", "26": "🚒", "27": "🌕", "28": "🧛", "29": "🧊", "30": "😇",
    "31": "😈", "32": "🔥", "33": "🇫🇷", "34": "⭐️", "35": "🌧", "36": "🪙",
    "37": "⚡️", "38": "🌑"
}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.fish$"))
async def start_fish(event):
    fish_chats.add(event.chat_id)
    await event.delete()

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopfish$"))
async def stop_fish(event):
    fish_chats.discard(event.chat_id)
    await event.edit("🛑 اتوماسیون ماهی متوقف شد.")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.automeo$"))
async def start_automeo(event):
    automeo_chats.add(event.chat_id)
    await event.delete()
    async def meo_loop():
        while event.chat_id in automeo_chats:
            try:
                await client.send_message(event.chat_id, "meo")
            except Exception:
                pass
            await asyncio.sleep(300)
    asyncio.create_task(meo_loop())

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopautomeo$"))
async def stop_automeo(event):
    automeo_chats.discard(event.chat_id)
    await event.edit("🛑 ارسال خودکار میو متوقف شد.")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.autoreact$"))
async def start_autoreact(event):
    autoreact_chats.add(event.chat_id)
    await event.edit("✅ ریکشن خودکار فعال شد.")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopautoreact$"))
async def stop_autoreact(event):
    autoreact_chats.discard(event.chat_id)
    await event.edit("🛑 ریکشن خودکار متوقف شد.")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.khofash$"))
async def start_khofash(event):
    khofash_chats.add(event.chat_id)
    await event.delete()

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopkhofash$"))
async def stop_khofash(event):
    khofash_chats.discard(event.chat_id)
    await event.edit("🛑 شکارچی خفاش متوقف شد.")

@client.on(events.NewMessage())
async def general_automations(event):
    message = event.message
    chat_id = message.chat_id
    text = message.raw_text or ""

    # ماهی
    if chat_id in fish_chats and message.buttons:
        for row in message.buttons:
            for btn in row:
                if "ماهی" in getattr(btn, "text", ""):
                    try:
                        await message.click(text=btn.text)
                    except Exception:
                        pass
                    return

    # خفاش فوق‌سریع
    if chat_id in khofash_chats and message.edit_date is None:
        if "خفاش" in text and "میترسه" in text and "کد" in text:
            match = re.search(r"کد\s*:\s*(\d+)", text)
            if not match:
                match = re.search(r"\(.*?(\d+).*?\)", text)
            if match:
                code_str = match.group(1).strip()
                emoji = BAT_CODE_MAPPING.get(code_str)
                if emoji:
                    try:
                        await message.reply(emoji)
                        async def send_bg():
                            try:
                                chat = await message.get_chat()
                                uname = getattr(chat, 'username', None)
                                if uname:
                                    link = f"https://t.me/{uname}/{message.id}"
                                else:
                                    c_id = str(chat_id)
                                    clean = c_id[4:] if c_id.startswith("-100") else (c_id[1:] if c_id.startswith("-") else c_id)
                                    link = f"https://t.me/c/{clean}/{message.id}"
                                await client.send_message("me", f"🦇 شکار کد {code_str}:\n{link}", link_preview=False)
                            except Exception:
                                pass
                        asyncio.create_task(send_bg())
                    except Exception:
                        pass

# ============================================================
# UTILITIES & STATUS & MAIN
# ============================================================

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopall$"))
async def stop_all(event):
    fish_chats.clear()
    automeo_chats.clear()
    autoreact_chats.clear()
    khofash_chats.clear()
    cat_chats.clear()
    reply_rules.clear()
    await event.edit("🛑 تمام قابلیت‌های بات متوقف و پاکسازی شدند.")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.status$"))
async def status_report(event):
    report = ["📊 **وضعیت قابلیت‌ها:**"]
    report.append(f"🦇 خفاش: {'فعال' if khofash_chats else 'غیرفعال'}")
    report.append(f"🐱 گربه: {'فعال' if cat_chats else 'غیرفعال'}")
    report.append(f"🐟 ماهی: {'فعال' if fish_chats else 'غیرفعال'}")
    report.append(f"🐈 میو: {'فعال' if automeo_chats else 'غیرفعال'}")
    await event.edit("\n".join(report))

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
async def ping(event):
    await event.edit("✅ Userbot is online.")

async def main():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    start_web_server()
    await authenticate()
    print("✅ USERBOT CONNECTED SUCCESSFULLY!")
    await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped.")
