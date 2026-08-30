import asyncio
import json
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

PORT = int(os.environ.get("PORT", "8000"))
SESSION_PATH = Path("userbot")

# وضعیت‌ها: 0: فرم اولیه, 1: منتظر کد, 2: منتظر پسورد, 3: متصل شده
app_state = {
    "step": 0,
    "message": "لطفاً اطلاعات اکانت خود را وارد کنید.",
    "api_id": "",
    "api_hash": "",
    "phone": "",
    "password_2fa": "",
    "code": "",
    "password_entered": ""
}

code_event = asyncio.Event()
password_event = asyncio.Event()
MAIN_LOOP = None

def page_template():
    return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telegram Setup</title>
<style>
body { margin:0; min-height:100vh; display:grid; place-items:center; background:#10131a; color:#fff; font-family:system-ui,sans-serif; }
main { width:min(92vw,400px); padding:28px; box-sizing:border-box; border-radius:16px; background:#191e28; border:1px solid #303746; }
input { width:100%; box-sizing:border-box; padding:12px; margin-top:8px; margin-bottom:14px; border-radius:9px; border:1px solid #465064; background:#0d1117; color:#fff; font-size:16px; }
label { font-size:14px; color:#b8c0cf; }
button { width:100%; margin-top:10px; padding:12px; border:0; border-radius:9px; background:#4f8cff; color:white; font-size:16px; font-weight:700; cursor:pointer; }
button:disabled { background:#2a3b5c; color:#7a8a9e; cursor:not-allowed; }
p, #status { color:#b8c0cf; line-height:1.5; font-size:14px; margin-top: 10px; }
.hidden { display: none !important; }
</style>
</head>
<body>
<main>
    <h2 id="title">Telegram Login</h2>
    <div id="status">در حال بارگذاری...</div>
    <form id="setupForm" onsubmit="handleSubmit(event)">
        <div id="step0">
            <label>API ID</label><input id="api_id" type="text" required>
            <label>API Hash</label><input id="api_hash" type="text" required>
            <label>Phone Number</label><input id="phone" type="text" placeholder="+989..." required>
            <label>2FA Password (Optional)</label><input id="password" type="password">
            <button type="submit" id="btn0">ارسال و دریافت کد</button>
        </div>
        <div id="step1" class="hidden">
            <label>Login Code</label><input id="code" type="text" required>
            <button type="submit" id="btn1">تایید کد</button>
        </div>
        <div id="step2" class="hidden">
            <label>2FA Password</label><input id="pass_input" type="password" required>
            <button type="submit" id="btn2">تایید رمز</button>
        </div>
    </form>
</main>
<script>
async function fetchState() {
    try {
        let res = await fetch('/status');
        let data = await res.json();
        document.getElementById('status').innerText = data.message;
        
        if (data.step === 0) {
            document.getElementById('step0').classList.remove('hidden');
            document.getElementById('step1').classList.add('hidden');
            document.getElementById('step2').classList.add('hidden');
        } else if (data.step === 1) {
            document.getElementById('step0').classList.add('hidden');
            document.getElementById('step1').classList.remove('hidden');
            document.getElementById('step2').classList.add('hidden');
        } else if (data.step === 2) {
            document.getElementById('step0').classList.add('hidden');
            document.getElementById('step1').classList.add('hidden');
            document.getElementById('step2').classList.remove('hidden');
        } else if (data.step === 3) {
            document.getElementById('setupForm').classList.add('hidden');
            document.getElementById('title').innerText = "✅ متصل شد!";
        }
    } catch(e) {}
}

async function handleSubmit(e) {
    e.preventDefault();
    let payload = {};
    if (app_state_step === 0 || document.getElementById('step0').classList.contains('hidden') === false) {
        payload.action = 'submit';
        payload.api_id = document.getElementById('api_id').value;
        payload.api_hash = document.getElementById('api_hash').value;
        payload.phone = document.getElementById('phone').value;
        payload.password = document.getElementById('password').value;
    } else if (document.getElementById('step1').classList.contains('hidden') === false) {
        payload.action = 'code';
        payload.code = document.getElementById('code').value;
    } else if (document.getElementById('step2').classList.contains('hidden') === false) {
        payload.action = 'password';
        payload.password = document.getElementById('pass_input').value;
    }
    
    document.getElementById('status').innerText = "در حال ارسال اطلاعات...";
    let res = await fetch('/api', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    });
    let data = await res.json();
    fetchState();
}

let app_state_step = 0;
setInterval(async () => {
    let res = await fetch('/status');
    let data = await res.json();
    app_state_step = data.step;
    document.getElementById('status').innerText = data.message;
    if(data.step === 3) {
        document.getElementById('setupForm').style.display = 'none';
        document.getElementById('title').innerText = "✅ ربات با موفقیت روشن شد!";
    }
}, 2000);

fetchState();
</script>
</body>
</html>"""

class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body = page_template().encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/status":
            data = json.dumps({"step": app_state["step"], "message": app_state["message"]}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if self.path == "/api":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            try:
                data = json.loads(body)
            except:
                data = {}

            action = data.get("action")
            if action == "submit":
                app_state["api_id"] = data.get("api_id", "").strip()
                app_state["api_hash"] = data.get("api_hash", "").strip()
                app_state["phone"] = data.get("phone", "").strip()
                app_state["password_2fa"] = data.get("password", "").strip()
                app_state["step"] = 5 # در حال پردازش
                app_state["message"] = "در حال اتصال به تلگرام و ارسال کد..."
                if MAIN_LOOP: MAIN_LOOP.call_soon_threadsafe(code_event.set)
            elif action == "code":
                app_state["code"] = data.get("code", "").strip()
                app_state["step"] = 5
                app_state["message"] = "در حال بررسی کد..."
                if MAIN_LOOP: MAIN_LOOP.call_soon_threadsafe(code_event.set)
            elif action == "password":
                app_state["password_entered"] = data.get("password", "").strip()
                app_state["step"] = 5
                app_state["message"] = "در حال بررسی رمز دومرحله‌ای..."
                if MAIN_LOOP: MAIN_LOOP.call_soon_threadsafe(password_event.set)

            response = json.dumps({"status": "ok"}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format, *args): pass

def start_web_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), WebHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

def register_events(cli):
    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
    async def ping(event):
        await event.edit("✅ Userbot is online.")

async def main():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    start_web_server()

    # منتظر ماندن برای دریافت اطلاعات از فرم وب
    await code_event.wait()
    code_event.clear()

    client = TelegramClient(str(SESSION_PATH), int(app_state["api_id"]), app_state["api_hash"], auto_reconnect=True)
    await client.connect()

    if not await client.is_user_authorized():
        try:
            res = await client.send_code_request(app_state["phone"])
            app_state["step"] = 1
            app_state["message"] = "کد تایید به تلگرام شما ارسال شد. لطفاً آن را وارد کنید."
        except Exception as e:
            app_state["step"] = 0
            app_state["message"] = f"خطا در ارسال کد: {e}"
            return

        code_event.clear()
        await code_event.wait()
        code = app_state["code"]

        try:
            await client.sign_in(phone=app_state["phone"], code=code, phone_code_hash=res.phone_code_hash)
        except SessionPasswordNeededError:
            app_state["step"] = 2
            app_state["message"] = "این اکانت دارای رمز دومرحله‌ای است. لطفاً رمز را وارد کنید."
            if app_state["password_2fa"]:
                await client.sign_in(password=app_state["password_2fa"])
            else:
                password_event.clear()
                await password_event.wait()
                await client.sign_in(password=app_state["password_entered"])

    app_state["step"] = 3
    app_state["message"] = "ربات با موفقیت لاگین کرد و فعال است!"
    
    register_events(client)
    print("Userbot is running completely!")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
