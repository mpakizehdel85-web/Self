import asyncio
import os
import threading
from http import HTTPStatus
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs
from pathlib import Path
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

PORT = int(os.environ.get("PORT", "10000"))
SESSION_PATH = Path("userbot")

# متغیرهای نگهداری وضعیت و اطلاعات موقت در حافظه
app_state = {
    "step": "form",          # فرم شروع: form, code, password, success, error
    "message": "لطفاً اطلاعات اکانت تلگرام خود را وارد کنید.",
    "api_id": "",
    "api_hash": "",
    "phone": "",
    "code": "",
    "password": "",
    "phone_code_hash": "",
    "client": None
}

def html_template(body):
    return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>راه‌اندازی یوزربات تلگرام</title>
<style>
body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:#0f172a; color:#f8fafc; font-family:system-ui,sans-serif; }}
.card {{ width:min(90vw, 380px); padding:24px; background:#1e293b; border-radius:14px; border:1px solid #334155; box-shadow:0 10px 25px rgba(0,0,0,0.3); }}
input {{ width:100%; box-sizing:border-box; padding:12px; margin-top:6px; margin-bottom:14px; border-radius:8px; border:1px solid #475569; background:#0f172a; color:#fff; font-size:15px; }}
label {{ font-size:13px; color:#94a3b8; }}
button {{ width:100%; padding:12px; border:0; border-radius:8px; background:#3b82f6; color:white; font-size:15px; font-weight:700; cursor:pointer; margin-top:8px; }}
button:hover {{ background:#2563eb; }}
p {{ font-size:14px; color:#cbd5e1; line-height:1.5; margin-bottom:16px; }}
.success {{ color:#4ade80; font-weight:bold; }}
.error {{ color:#f87171; }}
</style>
</head>
<body>
<div class="card">
{body}
</div>
</body>
</html>"""

def get_current_form():
    st = app_state["step"]
    msg = app_state["message"]
    
    if st == "form":
        return f"""
        <h2>ورود اطلاعات</h2>
        <p>{msg}</p>
        <form method="POST" action="/submit">
            <label>API ID</label>
            <input type="text" name="api_id" required placeholder="مثلاً 123456">
            <label>API Hash</label>
            <input type="text" name="api_hash" required placeholder="متن هشتاد کارکتری">
            <label>شماره تلفن (با پیش‌شماره)</label>
            <input type="text" name="phone" required placeholder="+989123456789">
            <button type="submit">دریافت کد تایید</button>
        </form>"""
    elif st == "code":
        return f"""
        <h2>کد تایید تلگرام</h2>
        <p class="success">{msg}</p>
        <form method="POST" action="/verify_code">
            <label>کد ۵ رقمی ارسالی از تلگرام</label>
            <input type="text" name="code" required placeholder="کد را وارد کنید">
            <button type="submit">تایید و ورود</button>
        </form>"""
    elif st == "password":
        return f"""
        <h2>رمز دومرحله‌ای (2FA)</h2>
        <p>{msg}</p>
        <form method="POST" action="/verify_password">
            <label>رمز عبور اکانت</label>
            <input type="password" name="password" required placeholder="رمز اکانت خود را وارد کنید">
            <button type="submit">ثبت رمز و ورود</button>
        </form>"""
    elif st == "success":
        return f"""
        <h2 class="success">✅ اتصال موفقیت‌آمیز!</h2>
        <p>{msg}</p>"""
    else:
        return f"""
        <h2 class="error">خطا</h2>
        <p class="error">{msg}</p>
        <p><a href="/" style="color:#60a5fa;">تلاش مجدد</a></p>"""

# رویدادهای همگام‌سازی تردها
code_event = threading.Event()
pass_event = threading.Event()

class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html_template(get_current_form()).encode("utf-8"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        params = parse_qs(body)

        if self.path == "/submit":
            app_state["api_id"] = params.get("api_id", [""])[0].strip()
            app_state["api_hash"] = params.get("api_hash", [""])[0].strip()
            app_state["phone"] = params.get("phone", [""])[0].strip()
            code_event.set() # آزادسازی اسکریپت برای درخواست کد

        elif self.path == "/verify_code":
            app_state["code"] = params.get("code", [""])[0].strip()
            code_event.set()

        elif self.path == "/verify_password":
            app_state["password"] = params.get("password", [""])[0].strip()
            pass_event.set()

        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, format, *args):
        pass

def run_web_server():
    server = HTTPServer(("0.0.0.0", PORT), WebHandler)
    server.serve_forever()

async def telegram_worker():
    # روشن کردن سرور وب در پس‌زمینه
    threading.Thread(target=run_web_server, daemon=True).start()

    # منتظر ماندن تا کاربر فرم اول را پر کند
    code_event.wait()
    code_event.clear()

    app_state["step"] = "loading"
    app_state["message"] = "در حال اتصال به تلگرام..."

    try:
        client = TelegramClient(str(SESSION_PATH), int(app_state["api_id"]), app_state["api_hash"], auto_reconnect=True)
        await client.connect()
        app_state["client"] = client

        res = await client.send_code_request(app_state["phone"])
        app_state["phone_code_hash"] = res.phone_code_hash

        app_state["step"] = "code"
        app_state["message"] = "کد تایید تلگرام با موفقیت ارسال شد. لطفاً آن را وارد کنید."

        # منتظر ماندن برای دریافت کد تایید
        code_event.wait()
        code_event.clear()

        try:
            await client.sign_in(phone=app_state["phone"], code=app_state["code"], phone_code_hash=app_state["phone_code_hash"])
        except SessionPasswordNeededError:
            app_state["step"] = "password"
            app_state["message"] = "این اکانت دارای رمز دومرحله‌ای است. لطفاً رمز خود را وارد کنید."
            
            pass_event.wait()
            await client.sign_in(password=app_state["password"])

        app_state["step"] = "success"
        app_state["message"] = "یوزربات با موفقیت لاگین کرد و اکنون کاملاً فعال است و به کار خود ادامه می‌دهد!"
        
        # رویداد نمونه برای تست ربات
        @client.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
        async def ping(event):
            await event.edit("✅ Userbot is active!")

        await client.run_until_disconnected()

    except Exception as e:
        app_state["step"] = "error"
        app_state["message"] = str(e)

if __name__ == "__main__":
    asyncio.run(telegram_worker())
