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
# SETTINGS
# ============================================================

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
PHONE = os.environ["TELEGRAM_PHONE"]
PASSWORD_2FA = os.environ.get("TELEGRAM_2FA_PASSWORD", "")

SESSION_DIR = Path(".telegram_sessions")
SESSION_DIR.mkdir(parents=True, exist_ok=True)
SESSION_PATH = SESSION_DIR / "userbot"

PORT = int(os.environ.get("PORT", "8000"))

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
    border-radius:9px;
    border:1px solid #465064;
    background:#0d1117;
    color:#fff;
    font-size:17px;
}}
button {{
    width:100%;
    margin-top:18px;
    padding:12px;
    border:0;
    border-radius:9px;
    background:#4f8cff;
    color:white;
    font-size:16px;
    font-weight:700;
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

    if login_state == "code":

        return page_template("""
<h2>Telegram Login</h2>
<p>کد یک‌بارمصرف تلگرام را وارد کن.</p>

<form method="post" action="/code" autocomplete="off">
<label>Login Code</label>
<input
    name="code"
    type="text"
    inputmode="numeric"
    autocomplete="one-time-code"
    required
>
<button type="submit">ورود</button>
</form>
""")

    if login_state == "password":

        return page_template("""
<h2>Two-Step Verification</h2>
<p>رمز دو مرحله‌ای تلگرام را وارد کن.</p>

<form method="post" action="/password" autocomplete="off">
<label>2FA Password</label>
<input
    name="password"
    type="password"
    autocomplete="current-password"
    required
>
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
        self.send_header(
            "Content-Type",
            "text/html; charset=utf-8"
        )
        self.send_header(
            "Content-Length",
            str(len(body))
        )
        self.send_header(
            "Cache-Control",
            "no-store"
        )
        self.end_headers()

        self.wfile.write(body)

    def do_POST(self):

        length = int(
            self.headers.get(
                "Content-Length",
                "0"
            )
        )

        body = self.rfile.read(length).decode("utf-8")

        values = parse_qs(
            body,
            keep_blank_values=True
        )

        if self.path == "/code":

            code = values.get(
                "code",
                [""]
            )[0].strip()

            if not code.isdigit():

                self.send_error(
                    HTTPStatus.BAD_REQUEST,
                    "Invalid code"
                )
                return

            if MAIN_LOOP:

                asyncio.run_coroutine_threadsafe(
                    code_queue.put(code),
                    MAIN_LOOP
                )

            self.redirect()

            return

        if self.path == "/password":

            password = values.get(
                "password",
                [""]
            )[0]

            if not password:

                self.send_error(
                    HTTPStatus.BAD_REQUEST,
                    "Password required"
                )
                return

            if MAIN_LOOP:

                asyncio.run_coroutine_threadsafe(
                    password_queue.put(password),
                    MAIN_LOOP
                )

            self.redirect()

            return

        self.send_error(
            HTTPStatus.NOT_FOUND
        )

    def redirect(self):

        self.send_response(
            HTTPStatus.SEE_OTHER
        )

        self.send_header(
            "Location",
            "/"
        )

        self.end_headers()

    def log_message(self, format, *args):
        return


def start_web_server():

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        LoginHandler
    )

    threading.Thread(
        target=server.serve_forever,
        daemon=True
    ).start()

    print(
        f"[WEB] Login page: port {PORT}"
    )

    return server


# ============================================================
# AUTHENTICATION
# ============================================================

async def authenticate():

    await client.connect()

    if await client.is_user_authorized():

        set_login_state(
            "authenticated",
            "Existing Telegram session is valid."
        )

        print(
            "[LOGIN] Existing session reused."
        )

        return

    print(
        "[LOGIN] Session is not authorized."
    )

    set_login_state(
        "starting",
        "Requesting a new Telegram login code..."
    )

    await client.send_code_request(
        PHONE
    )

    set_login_state(
        "code",
        "Telegram login code requested."
    )

    print(
        "[LOGIN] Waiting for code..."
    )

    code = await code_queue.get()

    try:

        await client.sign_in(
            phone=PHONE,
            code=code
        )

    except SessionPasswordNeededError:

        set_login_state(
            "password",
            "Telegram requires your 2FA password."
        )

        if PASSWORD_2FA:

            password = PASSWORD_2FA

        else:

            password = await password_queue.get()

        await client.sign_in(
            password=password
        )

    set_login_state(
        "authenticated",
        "Authentication successful."
    )

    print(
        "[LOGIN] Authentication successful."
    )


# ============================================================
# TIME PARSER
# ============================================================

def parse_interval(value):

    value = value.strip().lower()

    match = re.fullmatch(
        r"(\d+(?:\.\d+)?)(s|m|h)",
        value
    )

    if match:

        number = float(
            match.group(1)
        )

        unit = match.group(2)

        if unit == "s":
            return number

        if unit == "m":
            return number * 60

        if unit == "h":
            return number * 3600

    if re.fullmatch(
        r"\d+(?:\.\d+)?",
        value
    ):

        return float(value) * 60

    return None


# ============================================================
# .SET
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.set(?:\s|$)"
    )
)
async def set_scheduled_messages(event):

    match = re.fullmatch(
        r"\.set\s+(\d+)\s+(.+?)\s+(\d+(?:\.\d+)?[smh]?)",
        event.raw_text.strip(),
        re.IGNORECASE
    )

    if not match:

        await event.edit(
            "❌ فرمت اشتباه.\n\n"
            "مثال:\n"
            ".set 3 سلام 5m\n\n"
            "یا:\n"
            ".set 10 hi 2m"
        )

        return

    count = int(
        match.group(1)
    )

    message_text = match.group(2).strip()

    interval_text = match.group(3)

    interval = parse_interval(
        interval_text
    )

    if count <= 0:

        await event.edit(
            "❌ تعداد باید بیشتر از صفر باشد."
        )

        return

    if interval is None or interval <= 0:

        await event.edit(
            "❌ فاصله زمانی نامعتبر است."
        )

        return

    now = datetime.now(
        timezone.utc
    )

    scheduled = 0

    try:

        for index in range(
            1,
            count + 1
        ):

            schedule_time = (
                now
                + timedelta(
                    seconds=interval * index
                )
            )

            await client.send_message(
                event.chat_id,
                message_text,
                schedule=schedule_time
            )

            scheduled += 1

        await event.edit(
            "✅ پیام‌های زمان‌بندی‌شده ساخته شدند.\n\n"
            f"تعداد: {scheduled}\n"
            f"متن: {message_text}\n"
            f"فاصله: {interval_text}\n\n"
            "📅 پیام‌ها در Scheduled Messages تلگرام قرار گرفتند."
        )

        print(
            f"[SET] Scheduled {scheduled} messages "
            f"in chat {event.chat_id}"
        )

    except Exception as error:

        print(
            "[SET ERROR]",
            type(error).__name__,
            str(error)
        )

        await event.edit(
            "❌ هنگام ساخت پیام‌های زمان‌بندی‌شده خطا رخ داد.\n\n"
            f"تعداد موفق: {scheduled}\n"
            f"خطا: {type(error).__name__}: {error}"
        )


# ============================================================
# .REPLY
# ============================================================

reply_rules = {}


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.reply(?:\s|$)"
    )
)
async def create_reply(event):

    match = re.fullmatch(
        r"\.reply\s+(.+?)\s+to\s+(.+)",
        event.raw_text.strip(),
        re.IGNORECASE
    )

    if not match:

        await event.edit(
            "❌ فرمت:\n\n"
            ".reply جواب to متن\n\n"
            "مثال:\n"
            ".reply چطوری to سلام"
        )

        return

    response = match.group(1).strip()
    trigger = match.group(2).strip()

    if event.chat_id not in reply_rules:

        reply_rules[event.chat_id] = {}

    reply_rules[event.chat_id][
        trigger.casefold()
    ] = response

    await event.edit(
        "✅ ریپلای فعال شد\n\n"
        f"متن هدف: {trigger}\n"
        f"پاسخ: {response}"
    )


@client.on(
    events.NewMessage()
)
async def automatic_reply(event):

    if event.out:
        return

    if event.reply_to_msg_id:
        return

    chat_id = event.chat_id

    if chat_id not in reply_rules:
        return

    incoming = event.raw_text.strip()

    response = reply_rules[
        chat_id
    ].get(
        incoming.casefold()
    )

    if response is None:
        return

    try:

        await event.reply(
            response
        )

    except Exception as error:

        print(
            "[REPLY ERROR]",
            type(error).__name__,
            str(error)
        )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.stopreply$"
    )
)
async def stop_reply(event):

    reply_rules.pop(
        event.chat_id,
        None
    )

    await event.edit(
        "🛑 ریپلای خودکار متوقف شد."
    )


# ============================================================
# .CAT
# ============================================================

cat_chats = set()


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.cat$"
    )
)
async def start_cat(event):

    cat_chats.add(
        event.chat_id
    )

    await event.edit(
        "🐱 حالت نجات پیشی فعال شد.\n"
        "منتظر پیام‌های جدید می‌مانم."
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.stopcat$"
    )
)
async def stop_cat(event):

    cat_chats.discard(
        event.chat_id
    )

    await event.edit(
        "🛑 حالت نجات پیشی متوقف شد."
    )


async def check_cat_message(message):

    if message.chat_id not in cat_chats:
        return

    if not message.buttons:
        return

    for row in message.buttons:

        for button in row:

            text = getattr(
                button,
                "text",
                ""
            )

            if (
                text
                and
                "نجات پیشی خیابونی"
                in text
            ):

                try:

                    await message.click(
                        text=text
                    )

                    print(
                        "[CAT] Clicked:",
                        text
                    )

                except Exception as error:

                    print(
                        "[CAT ERROR]",
                        type(error).__name__,
                        str(error)
                    )

                return


@client.on(
    events.NewMessage()
)
async def cat_new_message(event):

    await check_cat_message(
        event.message
    )


@client.on(
    events.MessageEdited()
)
async def cat_edited_message(event):

    await check_cat_message(
        event.message
    )


# ============================================================
# .PING
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.ping$"
    )
)
async def ping(event):

    await event.edit(
        "✅ Userbot is online."
    )


# ============================================================
# .WHOAMI
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.whoami$"
    )
)
async def whoami(event):

    me = await client.get_me()

    username = (
        f"@{me.username}"
        if me.username
        else "No username"
    )

    await event.edit(
        f"Name: {me.first_name or ''}\n"
        f"Username: {username}\n"
        f"ID: {me.id}"
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    global MAIN_LOOP

    MAIN_LOOP = asyncio.get_running_loop()

    start_web_server()

    print(
        "======================================"
    )

    print(
        "Telegram Userbot starting..."
    )

    print(
        "======================================"
    )

    await authenticate()

    me = await client.get_me()

    print(
        "======================================"
    )

    print(
        "✅ USERBOT CONNECTED"
    )

    print(
        f"Name: {me.first_name or ''}"
    )

    print(
        f"Username: @{me.username or 'none'}"
    )

    print(
        "======================================"
    )

    print(
        "Commands:"
    )

    print(
        ".set"
    )

    print(
        ".reply"
    )

    print(
        ".cat"
    )

    print(
        ".ping"
    )

    print(
        ".whoami"
    )

    print(
        "======================================"
    )

    await client.run_until_disconnected()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "Userbot stopped."
        )

    except Exception as error:

        print(
            "======================================"
        )

        print(
            "USERBOT ERROR"
        )

        print(
            type(error).__name__,
            str(error)
        )

        print(
            "======================================"
        )

        raise
