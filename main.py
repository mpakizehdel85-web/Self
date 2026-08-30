import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

PORT = int(os.environ.get("PORT", "8000"))
SESSION_PATH = Path("userbot")

client = None
MAIN_LOOP = None

# وضعیت‌های لاگین: form -> code -> password -> done
auth_step = "form"
form_data = {}
code_event = asyncio.Event()
password_event = asyncio.Event()
login_message = "لطفاً اطلاعات را وارد کنید."

def set_step(step, msg):
    global auth_step, login_message
    auth_step = step
    login_message = msg
    print(f"[AUTH] {step}: {msg}")

def page_template(content):
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telegram Setup</title>
<style>
body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:#10131a; color:#fff; font-family:system-ui,sans-serif; }}
main {{ width:min(92vw,400px); padding:28px; box-sizing:border-box; border-radius:16px; background:#191e28; border:1px solid #303746; }}
input {{ width:100%; box-sizing:border-box; padding:12px; margin-top:8px; margin-bottom:14px; border-radius:9px; border:1px solid #465064; background:#0d1117; color:#fff; font-size:16px; }}
label {{ font-size:14px; color:#b8c0cf; }}
button {{ width:100%; margin-top:10px; padding:12px; border:0; border-radius:9px; background:#4f8cff; color:white; font-size:16px; font-weight:700; cursor:pointer; }}
p {{ color:#b8c0cf; line-height:1.5; }}
</style>
</head>
<body><main>{content}</main></body>
</html>"""

def render_page():
    if auth_step == "form":
        return page_template("""
<h2>Telegram Login</h2>
<form method="post" action="/submit">
<label>API ID</label><input name="api_id" type="text" required>
<label>API Hash</label><input name="api_hash" type="text" required>
<label>Phone Number</label><input name="phone" type="text" placeholder="+989..." required>
<label>2FA Password (Optional)</label><input name="password" type="password">
<button type="submit">ارسال و دریافت کد</button>
</form>""")
    if auth_step == "code":
        return page_template("""
<h2>Enter Code</h2>
<p>کد تلگرام را وارد کنید:</p>
<form method="post" action="/code">
<label>Login Code</label><input name="code" type="text" required>
<button type="submit">تایید کد</button>
</form>""")
    if auth_step == "password":
        return page_template("""
<h2>2FA Password</h2>
<p>رمز دومرحله‌ای را وارد کنید:</p>
<form method="post" action="/password">
<label>Password</label><input name="password" type="password" required>
<button type="submit">تایید رمز</button>
</form>""")
    if auth_step == "done":
        return page_template("<h2>✅ متصل شد!</h2><p>یوزربات فعال شد و می‌توانید به تلگرام برگردید.</p>")
    return page_template(f"<h2>صبر کنید...</h2><p>{login_message}</p><meta http-equiv='refresh' content='3'>")

class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = render_page().encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
            set_step("processing", "در حال درخواست کد...")
            if MAIN_LOOP: MAIN_LOOP.call_soon_threadsafe(code_event.set)
        elif self.path == "/code":
            form_data["code"] = values.get("code", [""])[0].strip()
            set_step("processing", "بررسی کد...")
            if MAIN_LOOP: MAIN_LOOP.call_soon_threadsafe(code_event.set)
        elif self.path == "/password":
            form_data["password_entered"] = values.get("password", [""])[0].strip()
            set_step("processing", "بررسی رمز...")
            if MAIN_LOOP: MAIN_LOOP.call_soon_threadsafe(password_event.set)

        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, format, *args): pass

def start_web_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), WebHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

# دستورات بات
def register_events(cli):
    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
    async def ping(event):
        await event.edit("✅ Userbot is online.")

async def main():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    start_web_server()

    await code_event.wait()
    code_event.clear()

    client = TelegramClient(str(SESSION_PATH), int(form_data["api_id"]), form_data["api_hash"], auto_reconnect=True)
    await client.connect()

    if not await client.is_user_authorized():
        res = await client.send_code_request(form_data["phone"])
        set_step("code", "کد ارسال شد.")
        
        code_event.clear()
        await code_event.wait()
        code = form_data["code"]

        try:
            await client.sign_in(phone=form_data["phone"], code=code, phone_code_hash=res.phone_code_hash)
        except SessionPasswordNeededError:
            set_step("password", "نیازمند رمز دومرحله‌ای.")
            if form_data.get("password"):
                await client.sign_in(password=form_data["password"])
            else:
                password_event.clear()
                await password_event.wait()
                await client.sign_in(password=form_data["password_entered"])

    set_step("done", "موفقیت‌آمیز!")
    register_events(client)
    print("Userbot is running completely!")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
