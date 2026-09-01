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
<p>برای دریافت Session در Saved Messages، دستور <b>.session</b> را ارسال کن.</p>
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
            if not code.isdigit():
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid code")
                return
            if MAIN_LOOP:
                asyncio.run_coroutine_threadsafe(code_queue.put(code), MAIN_LOOP)
            self.redirect()
            return

        if self.path == "/password":
            password = values.get("password", [""])[0]
            if not password:
                self.send_error(HTTPStatus.BAD_REQUEST, "Password required")
                return
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
# COMMAND DESCRIPTIONS
# ============================================================

COMMAND_DESCRIPTIONS = {
    ".session": "دریافت رشته سشن",
    ".set": "زمان‌بندی ارسال پیام",
    ".reply": "تنظیم پاسخ خودکار",
    ".stopreply": "توقف پاسخ خودکار",
    ".cat": "حالت نجات پیشی (مخفی)",
    ".stopcat": "توقف نجات پیشی",
    ".delete": "پاکسازی پیام‌ها",
    ".save": "ذخیره پیام در سیو مسیج",
    ".uptime": "آب‌تایم بات",
    ".fish": "اتوماسیون ماهی خودکار",
    ".stopfish": "توقف اتوماسیون ماهی",
    ".autoreact": "تنظیم ریکشن خودکار",
    ".stopautoreact": "توقف ریکشن خودکار",
    ".readmentions": "سین کردن منشن‌های این چت",
    ".userinfo": "اطلاعات حساب کاربر با ریپلای",
    ".tag": "تگ کردن هوشمند کاربران",
    ".kazino": "اتوماسیون کازینو (سرعتی و الگوگرا)",
    ".stopkazino": "توقف اتوماسیون کازینو",
    ".stopall": "توقف تمام قابلیت‌های فعال",
    ".status": "گزارش کامل وضعیت بات",
    ".i": "فهرست خلاصه دستورات",
    ".ping": "بررسی آنلاین بودن",
    ".whoami": "اطلاعات حساب کاربری"
}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.i$"))
async def short_help_list(event):
    lines = ["📋 **لیست خلاصه دستورات:**\n"]
    for cmd, desc in COMMAND_DESCRIPTIONS.items():
        lines.append(f"`{cmd}` : {desc}")
    await event.edit("\n".join(lines))

# ============================================================
# OTHER COMMANDS (SESSION, SET, REPLY, CAT, DELETE, SAVE, ETC.)
# ============================================================

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

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.delete(?:\s+(\d+))?$"))
async def delete_messages(event):
    match = event.pattern_match
    count = int(match.group(1)) if match.group(1) else 10
    for message in client.iter_messages(event.chat_id, limit=count, from_user="me"):
        try:
            await message.delete()
        except Exception:
            pass

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.save$"))
async def save_message(event):
    if not event.is_reply:
        await event.edit("❌ لطفا روی پیامی که می‌خواهید ذخیره کنید ریپلای بزنید.")
        return
    reply_msg = await event.get_reply_message()
    await client.forward_messages("me", reply_msg)
    await event.edit("✅ پیام در Saved Messages ذخیره شد.")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.uptime$"))
async def uptime_bot(event):
    uptime_seconds = int(time.time() - START_TIME)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    await event.edit(f"⏱ **آب‌تایم بات:** {hours} ساعت و {minutes} دقیقه و {seconds} ثانیه")

# ============================================================
# .KAZINO (ULTRA-FAST & PATTERN LEARNER MODES)
# ============================================================

kazino_active_chats = set()

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.kazino(?:\s+(.+))?$"))
async def start_kazino(event):
    match = event.pattern_match
    arg = match.group(1).strip() if match.group(1) else "🎰"

    chat_id = event.chat_id
    kazino_active_chats.add(chat_id)
    
    try:
        await event.delete()
    except Exception:
        pass

    target_values = {
        "🎰": 64,
        "🎲": 6,
        "🎯": 6,
        "🎳": 6,
        "🏀": 5,
        "⚽": 5
    }
    winning_value = target_values.get(arg, 64)

    # تشخیص حالت: اگر کاربر کلمه algo یا یک عدد خاص را وارد کرده باشد، حالت الگوریتم فعال می‌شود
    is_algo_mode = False
    if "algo" in arg.lower():
        is_algo_mode = True
    elif arg.isdigit():
        winning_value = int(arg)
        is_algo_mode = True

    try:
        from telethon.tl.types import InputMediaDice
        
        history = []
        pattern_map = {}
        
        while chat_id in kazino_active_chats:
            sent_msg = await client.send_message(
                chat_id, 
                file=InputMediaDice(emoticon="🎰" if not arg.startswith("🎲") and not arg.startswith("🎯") and not arg.startswith("🎳") and not arg.startswith("🏀") and not arg.startswith("⚽") else arg.split()[0])
            )
            
            dice_value = None
            if sent_msg.media and hasattr(sent_msg.media, 'value'):
                dice_value = sent_msg.media.value
            
            if dice_value:
                if is_algo_mode:
                    if len(history) >= 3:
                        key = tuple(history[-3:])
                        if key not in pattern_map:
                            pattern_map[key] = []
                        pattern_map[key].append(dice_value)
                    history.append(dice_value)
                
                # حالت عادی: شکار مستقیم عدد هدف
                if not is_algo_mode and dice_value == winning_value:
                    kazino_active_chats.discard(chat_id)
                    await client.send_message(chat_id, f"🎉 **هدف ({winning_value}) با موفقیت شکار شد!**")
                    break
                
                # حالت الگوریتم: پیش‌بینی بر اساس تکرار توالی قبلی
                if is_algo_mode and len(history) >= 3:
                    current_key = tuple(history[-3:])
                    if current_key in pattern_map:
                        possible_nexts = pattern_map[current_key]
                        if possible_nexts:
                            predicted_next = max(set(possible_nexts), key=possible_nexts.count)
                            if predicted_next == winning_value:
                                kazino_active_chats.discard(chat_id)
                                await client.send_message(
                                    chat_id, 
                                    f"🎯 **الگو کشف و پیش‌بینی شد!**\n"
                                    f"• توالی اخیر: `{history[-3:]}`\n"
                                    f"• پیش‌بینی عدد بعدی: **{winning_value}**\n"
                                    f"حالا خودت دستی پرتاب کن!"
                                )
                                break

            try:
                await sent_msg.delete()
            except Exception:
                pass
                
            await asyncio.sleep(0.04)
                
    except Exception as error:
        kazino_active_chats.discard(chat_id)
        print("[KAZINO ERROR]", error)

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopkazino$"))
async def stop_kazino(event):
    kazino_active_chats.discard(event.chat_id)
    try:
        await event.delete()
    except Exception:
        pass

# ============================================================
# .STOPALL & STATUS & PING & WHOAMI
# ============================================================

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopall$"))
async def stop_all_features(event):
    global kazino_active_chats
    kazino_active_chats.clear()
    await event.delete()

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
async def ping(event):
    await event.edit("✅ Userbot is online.")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.whoami$"))
async def whoami(event):
    me = await client.get_me()
    username = f"@{me.username}" if me.username else "No username"
    await event.edit(f"Name: {me.first_name or ''}\nUsername: {username}\nID: {me.id}")

# ============================================================
# MAIN
# ============================================================

async def main():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    start_web_server()

    await authenticate()
    me = await client.get_me()
    print(f"✅ USERBOT CONNECTED: {me.first_name or ''} (@{me.username or 'none'})")

    await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Userbot stopped.")
