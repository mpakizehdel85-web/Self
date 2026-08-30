import asyncio
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

PORT = int(os.environ.get("PORT", "8000"))
SESSION_PATH = Path("userbot")

# متغیرهای نگهداری وضعیت موقت برای لاگین
login_data = {
    "api_id": "",
    "api_hash": "",
    "phone": "",
    "code": "",
    "password": "",
    "phone_code_hash": ""
}

current_step = "form" # مراحل: form, code, password, success, error
status_message = "لطفاً اطلاعات اکانت خود را وارد کنید."

def html_page(body_content):
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telegram Userbot Login</title>
<style>
body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:#10131a; color:#fff; font-family:system-ui,sans-serif; }}
main {{ width:min(92vw,400px); padding:28px; box-sizing:border-box; border-radius:16px; background:#191e28; border:1px solid #303746; }}
input {{ width:100%; box-sizing:border-box; padding:12px; margin-top:8px; margin-bottom:14px; border-radius:9px; border:1px solid #465064; background:#0d1117; color:#fff; font-size:16px; }}
label {{ font-size:14px; color:#b8c0cf; }}
button {{ width:100%; margin-top:10px; padding:12px; border:0; border-radius:9px; background:#4f8cff; color:white; font-size:16px; font-weight:700; cursor:pointer; }}
p, .msg {{ color:#b8c0cf; line-height:1.5; font-size:14px; margin-bottom:15px; }}
.success {{ color: #3fb950; font-weight: bold; }}
.error {{ color: #f85149; }}
</style>
</head>
<body>
<main>
{body_content}
</main>
</html>"""

def get_body():
    global current_step, status_message
    if current_step == "form":
        return f"""
<h2>راه‌اندازی یوزربات</h2>
<p class="msg">{status_message}</p>
<form method="POST" action="/submit">
<label>API ID</label><input type="text" name="api_id" required>
<label>API Hash</label><input type="text" name="api_hash" required>
<label>شماره تلفن (با پیش‌شماره)</label><input type="text" name="phone" placeholder="+989..." required>
<button type="submit">ارسال و دریافت کد</button>
</form>"""
    elif current_step == "code":
        return f"""
<h2>کد تایید تلگرام</h2>
<p class="msg success">{status_message}</p>
<form method="POST" action="/verify_code">
<label>کد دریافتی از تلگرام</label><input type="text" name="code" required>
<button type="submit">تایید و ورود</button>
</form>"""
    elif current_step == "password":
        return f"""
<h2>رمز دو مرحله‌ای (2FA)</h2>
<p class="msg">{status_message}</p>
<form method="POST" action="/verify_password">
<label>رمز عبور اکانت</label><input type="password" name="password" required>
<button type="submit">تایید رمز</button>
</form>"""
    elif current_step == "success":
        return f"""
<h2 class="success">✅ ورود موفقیت‌آمیز!</h2>
<p class="msg">{status_message}</p>
"""
    else:
        return f"""
<h2 class="error">خطا</h2>
<p class="msg error">{status_message}</p>
<p><a href="/" style="color: #4f8cff;">تلاش مجدد</a></p>"""

# رویدادها برای هماهنگی بین وب‌سرور و اسکریپت اصلی پایتون
code_received_event = threading.Event()
password_received_event = threading.Event()

class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html_page(get_body()).encode("utf-8"))

    def do_POST(self):
        global current_step, status_message
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        params = parse_qs(body)

        if self.path == "/submit":
            login_data["api_id"] = params.get("api_id", [""])[0].strip()
            login_data["api_hash"] = params.get("api_hash", [""])[0].strip()
            login_data["phone"] = params.get("phone", [""])[0].strip()
            code_received_event.set()

        elif self.path == "/verify_code":
            login_data["code"] = params.get("code", [""])[0].strip()
            code_received_event.set()

        elif self.path == "/verify_password":
            login_data["password"] = params.get("password", [""])[0].strip()
            password_received_event.set()

        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, format, *args): pass

def run_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), WebHandler)
    server.serve_forever()

def register_bot_events(cli):
    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
    async def ping(event):
        await event.edit("✅ Userbot is online and working!")

async def main():
    global current_step, status_message
    
    # روشن کردن سرور در پس‌زمینه
    threading.Thread(target=run_server, daemon=True).start()

    # منتظر دریافت اطلاعات اولیه از صفحه وب
    code_received_event.wait()
    code_received_event.clear()

    current_step = "loading"
    status_message = "در حال اتصال به تلگرام و ارسال کد..."

    try:
        client = TelegramClient(str(SESSION_PATH), int(login_data["api_id"]), login_data["api_hash"], auto_reconnect=True)
        await client.connect()

        res = await client.send_code_request(login_data["phone"])
        login_data["phone_code_hash"] = res.phone_code_hash

        current_step = "code"
        status_message = "کد تایید به تلگرام شما ارسال شد. لطفاً آن را وارد کنید."

        # منتظر دریافت کد تایید از کاربر
        code_received_event.wait()
        code_received_event.clear()

        try:
            await client.sign_in(phone=login_data["phone"], code=login_data["code"], phone_code_hash=login_data["phone_code_hash"])
        except SessionPasswordNeededError:
            current_step = "password"
            status_message = "این اکانت دارای رمز دومرحله‌ای است. لطفاً رمز خود را وارد کنید."
            
            password_received_event.wait()
            await client.sign_in(password=login_data["password"])

        current_step = "success"
        status_message = "یوزربات با موفقیت لاگین کرد و اکنون کاملاً فعال است!"

        register_bot_events(client)
        print("Userbot started successfully!")
        await client.run_until_disconnected()

    except Exception as e:
        current_step = "error"
        status_message = str(e)
        print(f"[ERROR] {e}")

if __name__ == "__main__":
    asyncio.run(main())
