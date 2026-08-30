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

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession


# ============================================================
# SETTINGS
# ============================================================

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
PHONE = os.environ["TELEGRAM_PHONE"]

# اگر TELEGRAM_SESSION وجود داشته باشد،
# مستقیماً با همان سشن وارد می‌شویم.
TELEGRAM_SESSION = os.environ.get("TELEGRAM_SESSION", "").strip()

# رمز دوم فقط برای زمانی است که TELEGRAM_SESSION نداشته باشیم
PASSWORD_2FA = os.environ.get("TELEGRAM_2FA_PASSWORD", "")

PORT = int(os.environ.get("PORT", "8000"))

START_TIME = time.time()


# ============================================================
# TELEGRAM CLIENT
# ============================================================

if TELEGRAM_SESSION:
    print("[SESSION] TELEGRAM_SESSION found.")
    print("[SESSION] Starting with existing StringSession.")
    SESSION = StringSession(TELEGRAM_SESSION)
else:
    print("[SESSION] No TELEGRAM_SESSION found.")
    print("[SESSION] Starting with a new temporary StringSession.")
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

p {{
    color:#b8c0cf;
    line-height:1.5;
}}
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

<button type="submit">
ورود
</button>

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

<button type="submit">
ادامه
</button>

</form>
""")

    if login_state == "authenticated":
        return page_template("""
<h2>✅ Telegram Connected</h2>

<p>
Userbot با موفقیت متصل شده است.
</p>

<p>
برای دریافت Session در Saved Messages،
دستور <b>.session</b> را ارسال کن.
</p>
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

        body = self.rfile.read(length).decode(
            "utf-8"
        )

        values = parse_qs(
            body,
            keep_blank_values=True
        )


        # ====================================================
        # LOGIN CODE
        # ====================================================

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


        # ====================================================
        # 2FA PASSWORD
        # ====================================================

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
        f"[WEB] Login page running on port {PORT}"
    )

    return server


# ============================================================
# AUTHENTICATION
# ============================================================

async def authenticate():

    await client.connect()


    # --------------------------------------------------------
    # EXISTING SESSION
    # --------------------------------------------------------

    if await client.is_user_authorized():

        set_login_state(
            "authenticated",
            "Existing Telegram session is valid."
        )

        print(
            "[LOGIN] Existing Telegram session reused."
        )

        return


    # --------------------------------------------------------
    # NO VALID SESSION
    # --------------------------------------------------------

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
        "[LOGIN] Waiting for login code..."
    )


    code = await code_queue.get()


    # --------------------------------------------------------
    # SIGN IN WITH CODE
    # --------------------------------------------------------

    try:

        await client.sign_in(
            phone=PHONE,
            code=code
        )

    except SessionPasswordNeededError:

        print(
            "[LOGIN] Telegram requires 2FA password."
        )

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


    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    set_login_state(
        "authenticated",
        "Authentication successful."
    )

    print(
        "[LOGIN] Authentication successful."
    )

    print(
        "[SESSION] Login completed."
    )

    print(
        "[SESSION] Use .session in Saved Messages to export your StringSession."
    )


# ============================================================
# .SESSION
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.session$"
    )
)
async def send_session(event):

    try:

        # گرفتن StringSession فعلی
        session_string = client.session.save()


        if not session_string:

            await event.edit(
                "❌ Session هنوز آماده نیست."
            )

            return


        # ارسال Session فقط به Saved Messages
        await client.send_message(
            "me",
            session_string
        )


        await event.edit(
            "✅ TELEGRAM_SESSION در Saved Messages ارسال شد.\n\n"
            "🔐 این متن را مثل رمز اکانت نگه دار."
        )


        print(
            "[SESSION] TELEGRAM_SESSION sent to Saved Messages."
        )


    except Exception as error:

        print(
            "[SESSION ERROR]",
            error
        )

        await event.edit(
            f"❌ خطا در دریافت Session:\n{error}"
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
        pattern=r"^\.set(?:\s|$)?"
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
            ".set 3 سلام 5m"
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


    if (
        count <= 0
        or interval is None
        or interval <= 0
    ):

        await event.edit(
            "❌ مقادیر وارد شده نامعتبر است."
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
            f"✅ {scheduled} پیام زمان‌بندی شد."
        )


    except Exception as error:

        await event.edit(
            f"❌ خطا: {error}"
        )


# ============================================================
# .REPLY
# ============================================================

reply_rules = {}


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.reply(?:\s|$)?"
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
            "❌ فرمت:\n"
            ".reply جواب to متن"
        )

        return


    response = match.group(1).strip()

    trigger = match.group(2).strip()


    if event.chat_id not in reply_rules:

        reply_rules[event.chat_id] = {}


    reply_rules[
        event.chat_id
    ][
        trigger.casefold()
    ] = response


    await event.edit(
        f"✅ ریپلای فعال شد\n"
        f"هدف: {trigger}\n"
        f"پاسخ: {response}"
    )


@client.on(events.NewMessage())
async def automatic_reply(event):

    if event.out or event.reply_to_msg_id:
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


    if response:

        try:

            await event.reply(
                response
            )

        except Exception:

            pass


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
        "🐱 حالت نجات پیشی فعال شد."
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

    if (
        message.chat_id not in cat_chats
        or not message.buttons
    ):
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
                and "نجات پیشی خیابونی" in text
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
                        error
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
# .PURGE
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.purge(?:\s+(\d+))?$"
    )
)
async def purge_messages(event):

    match = event.pattern_match

    count = (
        int(match.group(1))
        if match.group(1)
        else 10
    )

    deleted = 0


    async for message in client.iter_messages(
        event.chat_id,
        limit=count,
        from_user="me"
    ):

        try:

            await message.delete()

            deleted += 1

        except Exception:

            pass


    print(
        f"[PURGE] Deleted {deleted} messages."
    )


# ============================================================
# .SAVE
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.save$"
    )
)
async def save_message(event):

    if not event.is_reply:

        await event.edit(
            "❌ لطفا روی پیامی که می‌خواهید ذخیره کنید ریپلای بزنید."
        )

        return


    reply_msg = await event.get_reply_message()


    await client.forward_messages(
        "me",
        reply_msg
    )


    await event.edit(
        "✅ پیام در Saved Messages ذخیره شد."
    )


# ============================================================
# .UPTIME
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.uptime$"
    )
)
async def uptime_bot(event):

    uptime_seconds = int(
        time.time() - START_TIME
    )

    hours, remainder = divmod(
        uptime_seconds,
        3600
    )

    minutes, seconds = divmod(
        remainder,
        60
    )


    await event.edit(
        f"⏱ **آب‌تایم بات:** "
        f"{hours} ساعت و "
        f"{minutes} دقیقه و "
        f"{seconds} ثانیه"
    )


# ============================================================
# .FISH
# ============================================================

fish_task_running = None


async def run_fish_workflow(
    client,
    chat_id
):

    try:

        # ۱. ارسال دستور ماهی
        await client.send_message(
            chat_id,
            "ماهی"
        )

        await asyncio.sleep(4)


        # ۲. کلیک روی «بندازش تو یخچال»

        async for message in client.iter_messages(
            chat_id,
            limit=3
        ):

            if message.buttons:

                for row in message.buttons:

                    for button in row:

                        if (
                            "بندازش تو یخچال"
                            in getattr(
                                button,
                                "text",
                                ""
                            )
                        ):

                            await button.click()

                            break

                    else:
                        continue

                    break

                break


        # ۳. صبر ۶۰ ثانیه

        await asyncio.sleep(60)


        # ۴. ارسال دستور یخچال

        await client.send_message(
            chat_id,
            "یخچال میویی"
        )

        await asyncio.sleep(4)


        # ۵. پیدا کردن ماهی خام

        async for message in client.iter_messages(
            chat_id,
            limit=3
        ):

            if (
                message.text
                and message.buttons
            ):

                lines = message.text.split(
                    "\n"
                )

                target_button_text = None


                for row in message.buttons:

                    for button in row:

                        btn_text = getattr(
                            button,
                            "text",
                            ""
                        )


                        for line in lines:

                            if (
                                btn_text in line
                                and "خام" in line
                            ):

                                await button.click()

                                target_button_text = btn_text

                                break


                        if target_button_text:
                            break


                    if target_button_text:
                        break


                break


        # ۶. بپوخش

        await asyncio.sleep(4)


        async for message in client.iter_messages(
            chat_id,
            limit=3
        ):

            if message.buttons:

                for row in message.buttons:

                    for button in row:

                        if (
                            "بپوخش"
                            in getattr(
                                button,
                                "text",
                                ""
                            )
                        ):

                            await button.click()

                            break

                    else:
                        continue

                    break

                break


        # ۷. تایید نهایی

        await asyncio.sleep(4)


        async for message in client.iter_messages(
            chat_id,
            limit=3
        ):

            if message.buttons:

                for row in message.buttons:

                    for button in row:

                        btn_txt = getattr(
                            button,
                            "text",
                            ""
                        )


                        if (
                            any(
                                x in btn_txt
                                for x in [
                                    "🛠",
                                    "✅",
                                    "➕",
                                    "تیک"
                                ]
                            )
                            or len(btn_txt.strip()) == 0
                        ):

                            await button.click()

                            break

                    else:
                        continue

                    break

                break


    except Exception as error:

        print(
            "[FISH ERROR]",
            error
        )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.fish$"
    )
)
async def start_fish_loop(event):

    global fish_task_running

    chat_id = event.chat_id


    await event.edit(
        "🎣 اتوماسیون ماهی فعال شد (هر ۳۱ دقیقه)."
    )


    async def loop_job():

        while True:

            await run_fish_workflow(
                client,
                chat_id
            )

            await asyncio.sleep(
                31 * 60
            )


    if fish_task_running:

        fish_task_running.cancel()


    fish_task_running = asyncio.create_task(
        loop_job()
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.stopfish$"
    )
)
async def stop_fish_loop(event):

    global fish_task_running


    if fish_task_running:

        fish_task_running.cancel()

        fish_task_running = None


        await event.edit(
            "🛑 اتوماسیون ماهی متوقف شد."
        )

    else:

        await event.edit(
            "❌ هیچ اتوماسیونی فعالی وجود ندارد."
        )


# ============================================================
# .STATUS
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.status$"
    )
)
async def bot_status_report(event):

    report = [
        "📊 **گزارش وضعیت سلف‌بات:**\n"
    ]


    if cat_chats:

        report.append(
            f"🐱 **چت‌های فعال .cat:** "
            f"{len(cat_chats)} چت"
        )

    else:

        report.append(
            "🐱 **حالت .cat:** غیرفعال"
        )


    global fish_task_running


    if (
        fish_task_running
        and not fish_task_running.done()
    ):

        report.append(
            "🎣 **وضعیت .fish:** فعال (هر ۳۱ دقیقه)"
        )

    else:

        report.append(
            "🎣 **وضعیت .fish:** غیرفعال"
        )


    if reply_rules:

        total_rules = sum(
            len(rules)
            for rules in reply_rules.values()
        )

        report.append(
            f"🤖 **ریپلای خودکار:** "
            f"در {len(reply_rules)} چت "
            f"({total_rules} قانون)"
        )

    else:

        report.append(
            "🤖 **ریپلای خودکار:** غیرفعال"
        )


    await event.edit(
        "\n".join(report)
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


    await client.run_until_disconnected()


# ============================================================
# START
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
            "USERBOT ERROR:",
            error
        )

        raise
# ============================================================
# FIXED .FISH COMMAND
# این بخش را فقط به انتهای main.py اضافه کن
# ============================================================

# حذف هندلر قدیمی .fish تا دو نسخه همزمان اجرا نشوند
try:
    client.remove_event_handler(start_fish_loop)
    print("[FISH] Old .fish handler removed.")
except Exception as error:
    print("[FISH] Could not remove old handler:", error)


def normalize_button_text(text):
    """
    برای مقایسه دقیق‌تر متن دکمه و متن توضیحات.
    فاصله‌ها و Variation Selector را حذف می‌کند.
    """
    if not text:
        return ""

    text = str(text)

    # حذف Variation Selector-16
    text = text.replace("\ufe0f", "")

    # حذف فاصله‌های مختلف
    text = re.sub(r"\s+", "", text)

    return text


def find_raw_fish_button(message):
    """
    متن پیام یخچال را می‌خواند.
    
    مثلاً اگر در توضیحات باشد:
        🐟 | ماهی (خام)
    
    و دکمه‌ها باشند:
        ⭐   🥘   🐟
    
    دکمه 🐟 را پیدا می‌کند.
    """

    if not message or not message.buttons:
        return None

    message_text = message.text or ""

    if not message_text:
        return None

    # --------------------------------------------------------
    # پیدا کردن خطی که «خام» دارد
    # --------------------------------------------------------

    raw_lines = []

    for line in message_text.splitlines():
        if "خام" in line:
            raw_lines.append(line)

    if not raw_lines:
        print("[FISH] هیچ آیتم خامی در پیام پیدا نشد.")
        return None

    print("[FISH] Raw item lines:")
    for line in raw_lines:
        print("   ", repr(line))

    # --------------------------------------------------------
    # حالا تمام دکمه‌ها را بررسی می‌کنیم
    # دکمه‌ای که متنش داخل خط «خام» باشد همان آیتم است.
    # --------------------------------------------------------

    for raw_line in raw_lines:

        normalized_line = normalize_button_text(raw_line)

        print("[FISH] Checking raw line:", repr(raw_line))

        for row in message.buttons:
            for button in row:

                button_text = getattr(button, "text", "") or ""
                normalized_button = normalize_button_text(button_text)

                if not normalized_button:
                    continue

                print(
                    "[FISH] Button:",
                    repr(button_text),
                    " | normalized:",
                    repr(normalized_button)
                )

                # ------------------------------------------------
                # مثال:
                #
                # متن:
                # 🐟 ماهی (خام)
                #
                # دکمه:
                # 🐟
                #
                # پس 🐟 داخل متن خط خام وجود دارد.
                # ------------------------------------------------

                if normalized_button in normalized_line:

                    print(
                        "[FISH] ✅ RAW FISH BUTTON FOUND:",
                        repr(button_text)
                    )

                    return button

    print("[FISH] ❌ دکمه مربوط به ماهی خام پیدا نشد.")
    return None


async def find_button_in_recent_messages(chat_id, finder, limit=8):
    """
    چند پیام آخر را بررسی می‌کند تا اگر پیام موردنظر
    کمی عقب‌تر بود هم بتواند آن را پیدا کند.
    """

    async for message in client.iter_messages(chat_id, limit=limit):

        try:
            button = finder(message)

            if button is not None:
                return message, button

        except Exception as error:
            print("[FISH] Button search error:", error)

    return None, None


async def run_fish_workflow_fixed(chat_id):

    try:

        # ========================================================
        # 1. ارسال «ماهی»
        # ========================================================

        print("[FISH] Sending: ماهی")

        await client.send_message(chat_id, "ماهی")

        await asyncio.sleep(4)

        # ========================================================
        # 2. پیدا کردن «بندازش تو یخچال»
        # ========================================================

        fridge_button = None
        fridge_message = None

        async for message in client.iter_messages(chat_id, limit=8):

            if not message.buttons:
                continue

            for row in message.buttons:
                for button in row:

                    text = getattr(button, "text", "") or ""

                    if "بندازش تو یخچال" in text:

                        fridge_button = button
                        fridge_message = message
                        break

                if fridge_button:
                    break

            if fridge_button:
                break

        if fridge_button:

            print("[FISH] Clicking: بندازش تو یخچال")

            await fridge_button.click()

        else:

            print("[FISH] ❌ دکمه «بندازش تو یخچال» پیدا نشد.")
            return

        # ========================================================
        # 3. صبر برای یخچال
        # ========================================================

        await asyncio.sleep(4)

        print("[FISH] Sending: یخچال میویی")

        await client.send_message(chat_id, "یخچال میویی")

        # کمی بیشتر صبر می‌کنیم تا پیام و دکمه‌ها کامل شوند
        await asyncio.sleep(5)

        # ========================================================
        # 4. پیدا کردن ماهی‌ای که در توضیحاتش «خام» است
        # ========================================================

        print("[FISH] Searching for RAW fish...")

        fridge_message = None
        raw_fish_button = None

        async def raw_fish_finder(message):
            button = find_raw_fish_button(message)
            return button

        # پیام‌های آخر را بررسی می‌کنیم
        async for message in client.iter_messages(chat_id, limit=10):

            if not message.buttons:
                continue

            button = find_raw_fish_button(message)

            if button is not None:

                fridge_message = message
                raw_fish_button = button
                break

        # ========================================================
        # اگر ماهی خام پیدا نشد
        # ========================================================

        if raw_fish_button is None:

            print("[FISH] ❌ Could not find raw fish button.")

            # برای دیباگ، متن پیام‌های آخر را چاپ می‌کنیم
            print("[FISH] Recent messages:")

            async for message in client.iter_messages(chat_id, limit=5):

                if message.text:
                    print("--------------------------------")
                    print(message.text)

            return

        # ========================================================
        # 5. کلیک روی همان ماهی خام
        # ========================================================

        raw_button_text = getattr(raw_fish_button, "text", "")

        print(
            "[FISH] 🐟 Clicking RAW fish button:",
            repr(raw_button_text)
        )

        await raw_fish_button.click()

        # صبر برای باز شدن منوی ماهی
        await asyncio.sleep(4)

        # ========================================================
        # 6. پیدا کردن «بپوخش»
        # ========================================================

        cook_button = None

        async for message in client.iter_messages(chat_id, limit=8):

            if not message.buttons:
                continue

            for row in message.buttons:
                for button in row:

                    text = getattr(button, "text", "") or ""

                    if "بپوخش" in text:

                        cook_button = button
                        break

                if cook_button:
                    break

            if cook_button:
                break

        if cook_button:

            print("[FISH] 🍳 Clicking: بپوخش")

            await cook_button.click()

        else:

            print("[FISH] ❌ دکمه «بپوخش» پیدا نشد.")
            return

        # ========================================================
        # 7. صبر برای صفحه تأیید
        # ========================================================

        await asyncio.sleep(4)

        # ========================================================
        # 8. پیدا کردن دکمه تأیید
        # ========================================================

        confirm_button = None

        async for message in client.iter_messages(chat_id, limit=8):

            if not message.buttons:
                continue

            for row in message.buttons:
                for button in row:

                    text = getattr(button, "text", "") or ""

                    # دکمه‌های احتمالی تأیید
                    if any(
                        x in text
                        for x in [
                            "✅",
                            "تأیید",
                            "تایید",
                            "✔️",
                            "✔"
                        ]
                    ):

                        confirm_button = button
                        break

                if confirm_button:
                    break

            if confirm_button:
                break

        # ========================================================
        # 9. تأیید نهایی
        # ========================================================

        if confirm_button:

            print("[FISH] ✅ Clicking confirmation button.")

            await confirm_button.click()

            print("[FISH] 🎉 Fish cooking completed.")

        else:

            print("[FISH] ❌ Confirmation button not found.")

    except asyncio.CancelledError:

        print("[FISH] Task cancelled.")
        raise

    except Exception as error:

        print("[FISH ERROR]", repr(error))


# ============================================================
# NEW .FISH HANDLER
# ============================================================

fish_task_running = None


@client.on(events.NewMessage(outgoing=True, pattern=r"^\.fish$"))
async def start_fish_loop_fixed(event):

    global fish_task_running

    chat_id = event.chat_id

    # اگر قبلاً فعال بوده، متوقفش کن
    if fish_task_running and not fish_task_running.done():

        fish_task_running.cancel()

        print("[FISH] Previous fish task cancelled.")

    await event.edit("🎣 اتوماسیون ماهی فعال شد.")

    async def loop_job():

        while True:

            print("======================================")
            print("[FISH] Starting new fishing cycle")
            print("======================================")

            await run_fish_workflow_fixed(chat_id)

            print("[FISH] Waiting 31 minutes...")

            await asyncio.sleep(31 * 60)

    fish_task_running = asyncio.create_task(loop_job())


@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopfish$"))
async def stop_fish_loop_fixed(event):

    global fish_task_running

    if fish_task_running and not fish_task_running.done():

        fish_task_running.cancel()
        fish_task_running = None

        await event.edit("🛑 اتوماسیون ماهی متوقف شد.")

    else:

        await event.edit("❌ اتوماسیون ماهی فعالی وجود ندارد.")


print("[FISH] Fixed .fish system loaded.")
