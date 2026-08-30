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
from telethon.sessions import StringSession

SESSION_PATH = Path("userbot")
PORT = int(os.environ.get("PORT", "8000"))

client = None
MAIN_LOOP = None

auth_step = "form"
form_data = {}
code_event = asyncio.Event()
password_event = asyncio.Event()
login_message = "لطفاً اطلاعات اکانت تلگرام خود را وارد کنید."


def set_step(step, msg):
    global auth_step, login_message
    auth_step = step
    login_message = msg
    print(f"[AUTH STEP] {step}: {msg}")


def page_template(content):
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telegram Userbot Setup</title>
<style>
body {{
    margin:0; min-height:100vh; display:grid; place-items:center;
    background:#10131a; color:#fff; font-family:system-ui,sans-serif;
}}
main {{
    width:min(92vw,400px); padding:28px; box-sizing:border-box;
    border-radius:16px; background:#191e28; border:1px solid #303746;
}}
input {{
    width:100%; box-sizing:border-box; padding:12px;
    margin-top:8px; margin-bottom:14px; border-radius:9px;
    border:1px solid #465064; background:#0d1117; color:#fff; font-size:16px;
}}
label {{ font-size:14px; color:#b8c0cf; }}
button {{
    width:100%; margin-top:10px; padding:12px; border:0;
    border-radius:9px; background:#4f8cff; color:white;
    font-size:16px; font-weight:700; cursor:pointer;
}}
p {{ color:#b8c0cf; line-height:1.5; }}
</style>
</head>
<body>
<main>{content}</main>
</body>
</html>"""


def render_page():
    if auth_step == "form":
        return page_template("""
<h2>Telegram Credentials</h2>
<p>اطلاعات اکانت تلگرام خود را وارد کنید.</p>
<form method="post" action="/submit">
<label>API ID</label>
<input name="api_id" type="text" required>
<label>API Hash</label>
<input name="api_hash" type="text" required>
<label>Phone Number (with +)</label>
<input name="phone" type="text" placeholder="+989..." required>
<label>2FA Password (optional)</label>
<input name="password" type="password">
<button type="submit">ذخیره و ادامه</button>
</form>
""")

    if auth_step == "code":
        return page_template("""
<h2>Telegram Login Code</h2>
<p>کد تایید ارسال‌شده از تلگرام را وارد کنید:</p>
<form method="post" action="/code">
<label>Login Code</label>
<input name="code" type="text" inputmode="numeric" required>
<button type="submit">تایید کد</button>
</form>
""")

    if auth_step == "password":
        return page_template("""
<h2>Two-Step Verification</h2>
<p>رمز عبور دو مرحله‌ای (2FA) خود را وارد کنید:</p>
<form method="post" action="/password">
<label>2FA Password</label>
<input name="password" type="password" required>
<button type="submit">تایید رمز</button>
</form>
""")

    if auth_step == "done":
        return page_template("""
<h2>✅ متصل شد</h2>
<p>یوزربات با موفقیت به تلگرام متصل گردید و آماده به کار است.</p>
""")

    return page_template(f"""
<h2>Status</h2>
<p>{html.escape(login_message)}</p>
<meta http-equiv="refresh" content="3">
""")


class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = render_page().encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        global form_data
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        values = parse_qs(body, keep_blank_values=True)

        if self.path == "/submit":
            form_data["api_id"] = values.get("api_id", [""])[0].strip()
            form_data["api_hash"] = values.get("api_hash", [""])[0].strip()
            form_data["phone"] = values.get("phone", [""])[0].strip()
            form_data["password"] = values.get("password", [""])[0].strip()
            set_step("processing", "در حال ارتباط با تلگرام...")
            if MAIN_LOOP:
                MAIN_LOOP.call_soon_threadsafe(code_event.set)

        elif self.path == "/code":
            form_data["code"] = values.get("code", [""])[0].strip()
            set_step("processing", "در حال بررسی کد تایید...")
            if MAIN_LOOP:
                MAIN_LOOP.call_soon_threadsafe(code_event.set)

        elif self.path == "/password":
            form_data["password_entered"] = values.get("password", [""])[0].strip()
            set_step("processing", "در حال بررسی رمز عبور...")
            if MAIN_LOOP:
                MAIN_LOOP.call_soon_threadsafe(password_event.set)

        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_web_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), WebHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[WEB] Server running on port {PORT}")


async def authenticate():
    global client
    await code_event.wait()
    code_event.clear()

    api_id = int(form_data["api_id"])
    api_hash = form_data["api_hash"]
    phone = form_data["phone"]
    pass_2fa = form_data.get("password", "")

    client = TelegramClient(str(SESSION_PATH), api_id, api_hash, auto_reconnect=True)
    await client.connect()

    if await client.is_user_authorized():
        set_step("done", "سشن از قبل معتبر بود.")
        return

    await client.send_code_request(phone)
    set_step("code", "کد تایید ارسال شد.")

    code_event.clear()
    await code_event.wait()
    code = form_data["code"]

    try:
        await client.sign_in(phone=phone, code=code)
    except SessionPasswordNeededError:
        set_step("password", "نیازمند رمز دومرحله‌ای است.")
        if pass_2fa:
            password = pass_2fa
        else:
            password_event.clear()
            await password_event.wait()
            password = form_data["password_entered"]
        await client.sign_in(password=password)

    set_step("done", "ورود موفقیت‌آمیز بود.")
    try:
        print("======================================")
        print("SESSION_STRING:", client.session.save())
        print("======================================")
    except:
        pass


# ============================================================
# EVENT HANDLERS & COMMANDS
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


def register_events(cli):
    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
    async def ping(event):
        await event.edit("✅ Userbot is online.")

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.whoami$"))
    async def whoami(event):
        me = await cli.get_me()
        username = f"@{me.username}" if me.username else "No username"
        await event.edit(f"Name: {me.first_name or ''}\nUsername: {username}\nID: {me.id}")

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


# ============================================================
# .FISH AUTOMATION
# ============================================================

fish_tasks = {}

def register_fish(cli):
    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.fish$"))
    async def start_fish_loop(event):
        chat_id = event.chat_id
        if chat_id in fish_tasks:
            await event.edit("⚠️ اتومیشن ماهی برای این چت قبلاً روشن شده است.")
            return

        await event.edit("🐟 اتومیشن ماهی هر ۳۱ دقیقه فعال شد.")
        
        async def fish_worker():
            try:
                while chat_id in fish_tasks:
                    await client.send_message(chat_id, "ماهی")
                    await asyncio.sleep(3)
                    
                    async for message in client.iter_messages(chat_id, limit=2):
                        if message.buttons:
                            for row in message.buttons:
                                for btn in row:
                                    if "بندازش تو یخچال" in getattr(btn, "text", ""):
                                        await btn.click()
                                        break
                    
                    await asyncio.sleep(4)
                    await client.send_message(chat_id, "یخچال میویی")
                    await asyncio.sleep(4)
                    
                    async for message in client.iter_messages(chat_id, limit=2):
                        if message.buttons:
                            for row in message.buttons:
                                for btn in row:
                                    btn_text = getattr(btn, "text", "")
                                    if "خام" in btn_text or "بپوخش" in btn_text:
                                        await btn.click()
                                        await asyncio.sleep(1.5)
                    
                    await asyncio.sleep(2)
                    async for message in client.iter_messages(chat_id, limit=2):
                        if message.buttons:
                            for row in message.buttons:
                                for btn in row:
                                    btn_text = getattr(btn, "text", "")
                                    if "✅" in btn_text or "تایید" in btn_text or "تيك" in btn_text:
                                        await btn.click()
                                        break

                    for _ in range(31 * 60):
                        if chat_id not in fish_tasks:
                            break
                        await asyncio.sleep(1)
            except Exception as error:
                print("[FISH ERROR]", type(error).__name__, str(error))
                fish_tasks.pop(chat_id, None)

        task = asyncio.create_task(fish_worker())
        fish_tasks[chat_id] = task

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.stopfish$"))
    async def stop_fish_loop(event):
        chat_id = event.chat_id
        task = fish_tasks.pop(chat_id, None)
        if task:
            task.cancel()
            await event.edit("🛑 اتومیشن ماهی متوقف شد.")
        else:
            await event.edit("⚠️ اتومیشن ماهی در این چت فعال نیست.")

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.status$"))
    async def check_all_bot_activities(event):
        chat_id = event.chat_id
        status_lines = ["<b>🤖 گزارش جامع فعالیت‌های ربات:</b>\n"]
        if fish_tasks:
            status_lines.append("<b>🐟 اتومیشن ماهی (.fish):</b> فعال ✅")
        else:
            status_lines.append("<b>🐟 اتومیشن ماهی (.fish):</b> غیرفعال ❌")
        await event.edit("\n".join(status_lines), parse_mode='html')


# ============================================================
# MAIN
# ============================================================

async def main():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    start_web_server()

    print("Waiting for credentials via web page...")
    await authenticate()

    register_events(client)
    register_fish(client)
    
    print("Userbot running and all commands registered.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
