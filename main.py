import asyncio
import html
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError

# ============================================================
# SETTINGS & DYNAMIC CONFIG
# ============================================================

CONFIG_FILE = Path(".bot_config.json")
SESSION_PATH = Path("userbot")

PORT = int(os.environ.get("PORT", "8000"))

# متغیرهای سراسری برای نگهداری اطلاعات در حال دریافت
bot_config = {
    "api_id": "",
    "api_hash": "",
    "phone": "",
    "password_2fa": ""
}

client = None
MAIN_LOOP = None
code_queue = asyncio.Queue()
password_queue = asyncio.Queue()
config_queue = asyncio.Queue()

login_state = "config" # شروع با وضعیت دریافت تنظیمات اولیه
login_message = "لطفاً مشخصات تلگرام خود را وارد کنید."


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
<title>Telegram Userbot Setup</title>
<style>
body {{
    margin:0;
    min-height:100vh;
    display:grid;
    place-items:center;
    background:#10131a;
    color:#fff;
    font-family:system-ui,sans-serif;
}}
main {{
    width:min(92vw,400px);
    padding:28px;
    box-sizing:border-box;
    border-radius:16px;
    background:#191e28;
    border:1px solid #303746;
}}
input {{
    width:100%;
    box-sizing:border-box;
    padding:12px;
    margin-top:8px;
    margin-bottom:14px;
    border-radius:9px;
    border:1px solid #465064;
    background:#0d1117;
    color:#fff;
    font-size:16px;
}}
label {{ font-size:14px; color:#b8c0cf; }}
button {{
    width:100%;
    margin-top:10px;
    padding:12px;
    border:0;
    border-radius:9px;
    background:#4f8cff;
    color:white;
    font-size:16px;
    font-weight:700;
    cursor:pointer;
}}
p {{ color:#b8c0cf; line-height:1.5; }}
</style>
</head>
<body>
<main>
{content}
</main>
</body>
</html>"""


def login_page():
    if login_state == "config":
        return page_template("""
<h2>Telegram Credentials</h2>
<p>اطلاعات اکانت تلگرام خود را وارد کنید.</p>
<form method="post" action="/config" autocomplete="off">
<label>API ID</label>
<input name="api_id" type="text" required>
<label>API Hash</label>
<input name="api_hash" type="text" required>
<label>Phone Number (with +)</label>
<input name="phone" type="text" placeholder="+989..." required>
<label>2FA Password (optional)</label>
<input name="password_2fa" type="password">
<button type="submit">ذخیره و ادامه</button>
</form>
""")

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

        if self.path == "/config":
            api_id = values.get("api_id", [""])[0].strip()
            api_hash = values.get("api_hash", [""])[0].strip()
            phone = values.get("phone", [""])[0].strip()
            pwd = values.get("password_2fa", [""])[0]

            if not api_id or not api_hash or not phone:
                self.send_error(HTTPStatus.BAD_REQUEST, "Missing fields")
                return

            cfg = {
                "api_id": api_id,
                "api_hash": api_hash,
                "phone": phone,
                "password_2fa": pwd
            }
            if MAIN_LOOP:
                asyncio.run_coroutine_threadsafe(config_queue.put(cfg), MAIN_LOOP)
            self.redirect()
            return

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
    print(f"[WEB] Login page: port {PORT}")
    return server


# ============================================================
# AUTHENTICATION
# ============================================================

async def authenticate():
    global client, bot_config

    # بررسی اینکه آیا قبلاً اطلاعات ذخیره شده است یا خیر
    if CONFIG_FILE.exists():
        import json
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                bot_config = json.load(f)
        except Exception:
            pass

    # اگر اطلاعات کامل نبود، منتظر ماندن برای ورود از طریق فرم وب
    while not bot_config.get("api_id") or not bot_config.get("api_hash") or not bot_config.get("phone"):
        set_login_state("config", "لطفاً مشخصات را از طریق صفحه وب وارد کنید.")
        print("[LOGIN] Waiting for credentials via web page...")
        bot_config = await config_queue.get()
        import json
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(bot_config, f)

    API_ID = int(bot_config["api_id"])
    API_HASH = bot_config["api_hash"]
    PHONE = bot_config["phone"]
    PASSWORD_2FA = bot_config.get("password_2fa", "")

    SESSION_DIR.mkdir(exist_ok=True)
    client = TelegramClient(
        str(SESSION_PATH),
        API_ID,
        API_HASH,
        auto_reconnect=True,
        connection_retries=None,
        request_retries=5,
        retry_delay=5,
        flood_sleep_threshold=60,
    )

    # ثبت ایونت‌ها روی کلاینت جدید
    register_events(client)

    await client.connect()

    if await client.is_user_authorized():
        set_login_state("authenticated", "Existing Telegram session is valid.")
        print("[LOGIN] Existing session reused.")
        return

    print("[LOGIN] Session is not authorized.")
    set_login_state("starting", "Requesting a new Telegram login code...")
    await client.send_code_request(PHONE)
    set_login_state("code", "Telegram login code requested.")
    print("[LOGIN] Waiting for code...")

    code = await code_queue.get()

    try:
        await client.sign_in(phone=PHONE, code=code)
    except SessionPasswordNeededError:
        set_login_state("password", "Telegram requires your 2FA password.")
        if PASSWORD_2FA:
            password = PASSWORD_2FA
        else:
            password = await password_queue.get()
        await client.sign_in(password=password)

    set_login_state("authenticated", "Authentication successful.")
    print("[LOGIN] Authentication successful.")


# ============================================================
# TIME PARSER
# ============================================================

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


# ============================================================
# EVENT HANDLERS REGISTRATION (دستورات و پاسخ‌های شما دست‌نخورده)
# ============================================================

def register_events(cli):

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.set(?:\s|$)"))
    async def set_scheduled_messages(event):
        match = re.fullmatch(r"\.set\s+(\d+)\s+(.+?)\s+(\d+(?:\.\d+)?[smh]?)", event.raw_text.strip(), re.IGNORECASE)
        if not match:
            await event.edit("❌ فرمت اشتباه.\n\nمثال:\n.set 3 سلام 5m")
            return
        count = int(match.group(1))
        message_text = match.group(2).strip()
        interval_text = match.group(3)
        interval = parse_interval(interval_text)
        if count <= 0 or interval is None or interval <= 0:
            await event.edit("❌ مقدار یا فاصله زمانی نامعتبر است.")
            return

        now = datetime.now(timezone.utc)
        scheduled = 0
        try:
            for index in range(1, count + 1):
                schedule_time = now + timedelta(seconds=interval * index)
                await cli.send_message(event.chat_id, message_text, schedule=schedule_time)
                scheduled += 1
            await event.edit(f"✅ پیام‌های زمان‌بندی‌شده ساخته شدند.\nتعداد: {scheduled}")
        except Exception as error:
            await event.edit(f"❌ خطا: {type(error).__name__}: {error}")


    reply_rules = {}

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.reply(?:\s|$)"))
    async def create_reply(event):
        match = re.fullmatch(r"\.reply\s+(.+?)\s+to\s+(.+)", event.raw_text.strip(), re.IGNORECASE)
        if not match:
            await event.edit("❌ فرمت:\n.reply جواب to متن")
            return
        response = match.group(1).strip()
        trigger = match.group(2).strip()
        if event.chat_id not in reply_rules:
            reply_rules[event.chat_id] = {}
        reply_rules[event.chat_id][trigger.casefold()] = response
        await event.edit(f"✅ ریپلای فعال شد\nهدف: {trigger}\nپاسخ: {response}")

    @cli.on(events.NewMessage())
    async def automatic_reply(event):
        if event.out: return
        chat_id = event.chat_id
        if chat_id not in reply_rules: return
        response = reply_rules[chat_id].get(event.raw_text.strip().casefold())
        if response:
            await event.reply(response)

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.stopreply$"))
    async def stop_reply(event):
        reply_rules.pop(event.chat_id, None)
        await event.edit("🛑 ریپلای خودکار متوقف شد.")


    cat_chats = set()

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.cat$"))
    async def start_cat(event):
        cat_chats.add(event.chat_id)
        await event.edit("🐱 حالت نجات پیشی فعال شد.")

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.stopcat$"))
    async def stop_cat(event):
        cat_chats.discard(event.chat_id)
        await event.edit("🛑 حالت نجات پیشی متوقف شد.")

    async def check_cat_message(message):
        if message.chat_id not in cat_chats or not message.buttons: return
        for row in message.buttons:
            for button in row:
                text = getattr(button, "text", "")
                if text and "نجات پیشی خیابونی" in text:
                    try:
                        await message.click(text=text)
                    except Exception:
                        pass
                    return

    @cli.on(events.NewMessage())
    async def cat_new_message(event):
        await check_cat_message(event.message)

    @cli.on(events.MessageEdited())
    async def cat_edited_message(event):
        await check_cat_message(event.message)


    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
    async def ping(event):
        await event.edit("✅ Userbot is online.")


    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.whoami$"))
    async def whoami(event):
        me = await cli.get_me()
        username = f"@{me.username}" if me.username else "No username"
        await event.edit(f"Name: {me.first_name or ''}\nUsername: {username}\nID: {me.id}")


# ============================================================
# MAIN
# ============================================================

async def main():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()

    start_web_server()

    print("======================================")
    print("Telegram Userbot starting...")
    print("======================================")

    await authenticate()

    me = await client.get_me()
    print("======================================")
    print("✅ USERBOT CONNECTED")
    print(f"Name: {me.first_name or ''}")
    print(f"Username: @{me.username or 'none'}")
    print("======================================")

    await client.run_until_disconnected()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Userbot stopped.")
    except Exception as error:
        print("======================================")
        print("USERBOT ERROR")
        print(type(error).__name__, str(error))
        print("======================================")
        raise
        # ============================================================
# .FISH (Fish Automation Loop)
# ============================================================

fish_tasks = {}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.fish$"))
async def start_fish_loop(event):
    chat_id = event.chat_id
    
    if chat_id in fish_tasks:
        await event.edit("⚠️ اتومیشن ماهی برای این چت قبلاً روشن شده است.")
        return

    await event.edit("🐟 اتومیشن ماهی هر ۳۱ دقیقه فعال شد.")
    
    async def fish_worker():
        try:
            while chat_id in fish_tasks:
                # ۱. ارسال کلمه ماهی
                await client.send_message(chat_id, "ماهی")
                await asyncio.sleep(3)
                
                # ۲. پیدا کردن و کلیک روی دکمه "بندازش تو یخچال"
                async for message in client.iter_messages(chat_id, limit=2):
                    if message.buttons:
                        for row in message.buttons:
                            for btn in row:
                                if "بندازش تو یخچال" in getattr(btn, "text", ""):
                                    await btn.click()
                                    print("[FISH] Clicked send to fridge")
                                    break
                
                # مکث چند ثانیه‌ای قبل از نوشتن "یخچال میویی"
                await asyncio.sleep(4)
                
                # ۳. ارسال دستور "یخچال میویی" برای باز کردن یخچال
                await client.send_message(chat_id, "یخچال میویی")
                await asyncio.sleep(4)
                
                # ۴. انتخاب ماهی‌های خام در یخچال و کلیک روی دکمه "بپوخش"
                async for message in client.iter_messages(chat_id, limit=2):
                    if message.buttons:
                        for row in message.buttons:
                            for btn in row:
                                btn_text = getattr(btn, "text", "")
                                # انتخاب گزینه‌های ماهی خام یا دکمه پختن
                                if "خام" in btn_text or "بپوخش" in btn_text:
                                    await btn.click()
                                    await asyncio.sleep(1.5)
                
                await asyncio.sleep(2)

                # ۵. تایید نهایی در صفحه جدید (کلیک روی دکمه تیک ✅)
                async for message in client.iter_messages(chat_id, limit=2):
                    if message.buttons:
                        for row in message.buttons:
                            for btn in row:
                                btn_text = getattr(btn, "text", "")
                                # بررسی ایموجی تیک یا کلمات تایید
                                if "✅" in btn_text or "تایید" in btn_text or "تيك" in btn_text:
                                    await btn.click()
                                    print(f"[FISH] Clicked confirmation: {btn_text}")
                                    break

                # ۶. صبر کردن برای ۳۱ دقیقه بعدی (31 * 60 ثانیه)
                for _ in range(31 * 60):
                    if chat_id not in fish_tasks:
                        break
                    await asyncio.sleep(1)
                    
        except Exception as error:
            print("[FISH ERROR]", type(error).__name__, str(error))
            fish_tasks.pop(chat_id, None)

    task = asyncio.create_task(fish_worker())
    fish_tasks[chat_id] = task


@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopfish$"))
async def stop_fish_loop(event):
    chat_id = event.chat_id
    task = fish_tasks.pop(chat_id, None)
    
    if task:
        task.cancel()
        await event.edit("🛑 اتومیشن ماهی متوقف شد.")
    else:
        await event.edit("⚠️ اتومیشن ماهی در این چت فعال نیست.")

