import asyncio
import html
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from telethon import TelegramClient, events, functions, types
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
# DYNAMIC FEATURE REGISTRY
# ============================================================
#
# هر قابلیت جدید باید اینجا ثبت شود.
# .info و .status از همین اطلاعات استفاده می‌کنند.
#

FEATURES = {}


def register_feature(
    command,
    description,
    category="general"
):

    FEATURES[command] = {
        "description": description,
        "category": category,
    }


# ============================================================
# CHAT / USER HELPERS
# ============================================================

def get_chat_title(entity):

    if entity is None:
        return "Unknown Chat"

    title = getattr(
        entity,
        "title",
        None
    )

    if title:
        return title

    first_name = getattr(
        entity,
        "first_name",
        None
    )

    last_name = getattr(
        entity,
        "last_name",
        None
    )

    name = " ".join(
        x for x in [
            first_name,
            last_name
        ]
        if x
    ).strip()

    if name:
        return name

    username = getattr(
        entity,
        "username",
        None
    )

    if username:
        return f"@{username}"

    return str(
        getattr(
            entity,
            "id",
            "Unknown"
        )
    )


async def get_chat_display(chat_id):

    try:

        entity = await client.get_entity(
            chat_id
        )

        title = get_chat_title(
            entity
        )

        username = getattr(
            entity,
            "username",
            None
        )

        if username:

            return f"{title} (@{username})"

        # لینک عمومی بعضی کانال‌ها/گروه‌ها
        if getattr(entity, "megagroup", False):
            pass

        return title

    except Exception:

        return str(chat_id)


async def resolve_user(value):

    value = value.strip()

    # حذف @ در صورت وجود
    if value.startswith("@"):
        value = value[1:]

    try:

        return await client.get_entity(
            value
        )

    except Exception:

        try:

            if value.isdigit():

                return await client.get_entity(
                    int(value)
                )

        except Exception:

            pass

    return None


def user_display_name(user):

    username = getattr(
        user,
        "username",
        None
    )

    if username:
        return f"@{username}"

    first_name = getattr(
        user,
        "first_name",
        None
    ) or ""

    last_name = getattr(
        user,
        "last_name",
        None
    ) or ""

    name = (
        f"{first_name} {last_name}"
    ).strip()

    if name:
        return name

    return "کاربر"


def user_mention(user):

    name = user_display_name(
        user
    )

    if getattr(user, "username", None):

        return f"@{user.username}"

    user_id = getattr(
        user,
        "id",
        None
    )

    if user_id:

        safe_name = html.escape(
            name
        )

        return (
            f'<a href="tg://user?id={user_id}">'
            f'{safe_name}'
            f'</a>'
        )

    return html.escape(
        name
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

        session_string = client.session.save()

        if not session_string:

            await event.edit(
                "❌ Session هنوز آماده نیست."
            )

            return

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


register_feature(
    ".session",
    "دریافت Session",
    "account"
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

scheduled_messages = {}


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

        scheduled_messages[event.chat_id] = (
            scheduled_messages.get(
                event.chat_id,
                0
            ) + scheduled
        )

        await event.edit(
            f"✅ {scheduled} پیام زمان‌بندی شد."
        )

    except Exception as error:

        await event.edit(
            f"❌ خطا: {error}"
        )


register_feature(
    ".set",
    "زمان‌بندی پیام",
    "automation"
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


register_feature(
    ".reply",
    "ریپلای خودکار",
    "automation"
)

register_feature(
    ".stopreply",
    "توقف ریپلای خودکار",
    "automation"
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


register_feature(
    ".cat",
    "نجات خودکار پیشی",
    "automation"
)

register_feature(
    ".stopcat",
    "توقف نجات پیشی",
    "automation"
)


# ============================================================
# .DELETE
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.delete(?:\s+(\d+))?$"
    )
)
async def delete_messages(event):

    match = event.pattern_match

    count = (
        int(match.group(1))
        if match.group(1)
        else 1
    )

    if count <= 0:

        await event.edit(
            "❌ تعداد باید بیشتر از صفر باشد."
        )

        return

    deleted = 0

    # خود دستور هم جزو پیام‌های خود کاربر است
    # ابتدا پیام‌های اخیر خودمان را پیدا می‌کنیم.
    async for message in client.iter_messages(
        event.chat_id,
        limit=count + 1,
        from_user="me"
    ):

        try:

            await message.delete()

            deleted += 1

        except Exception:

            pass

        if deleted >= count:
            break


register_feature(
    ".delete",
    "حذف تعداد مشخصی از پیام‌ها",
    "messages"
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


register_feature(
    ".save",
    "ذخیره پیام در Saved Messages",
    "messages"
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


register_feature(
    ".uptime",
    "نمایش زمان فعالیت سلف‌بات",
    "system"
)


# ============================================================
# REACTION AUTOMATION DATA
# ============================================================

reaction_rules = {}


def ensure_reaction_chat(chat_id):

    if chat_id not in reaction_rules:
        reaction_rules[chat_id] = []


def reaction_rule_matches(rule, event):

    # پیام‌های خودمان را نادیده می‌گیریم
    if event.out:
        return False

    message = event.message

    # اگر شخص مشخص شده باشد
    target_user_id = rule.get(
        "user_id"
    )

    if target_user_id is not None:

        sender_id = getattr(
            message,
            "sender_id",
            None
        )

        if sender_id != target_user_id:
            return False

    # اگر متن مشخص شده باشد
    target_text = rule.get(
        "text"
    )

    if target_text:

        incoming = (
            message.raw_text or ""
        ).strip()

        if incoming.casefold() != target_text.casefold():
            return False

    return True


async def apply_reaction_rule(
    event,
    rule
):

    try:

        if not reaction_rule_matches(
            rule,
            event
        ):
            return

        emoji = rule["emoji"]

        await event.message.react(
            emoji
        )

        print(
            "[REACTION] Reacted:",
            emoji,
            "chat:",
            event.chat_id,
            "message:",
            event.message.id
        )

    except Exception as error:

        print(
            "[REACTION ERROR]",
            error
        )


@client.on(
    events.NewMessage()
)
async def automatic_reaction(event):

    chat_id = event.chat_id

    rules = reaction_rules.get(
        chat_id,
        []
    )

    if not rules:
        return

    for rule in list(rules):

        await apply_reaction_rule(
            event,
            rule
        )


# ============================================================
# .REACTION
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.reaction(?:\s|$)?"
    )
)
async def create_reaction_rule(event):

    raw = event.raw_text.strip()

    # --------------------------------------------------------
    # فرمت‌ها:
    #
    # .reaction @username ❤️
    # .reaction متن ❤️
    # .reaction @username متن ❤️
    #
    # برای تشخیص بهتر، ایموجی آخر دستور در نظر گرفته می‌شود.
    # --------------------------------------------------------

    match = re.fullmatch(
        r"\.reaction\s+(.+?)\s+(\S+)$",
        raw,
        re.DOTALL
    )

    if not match:

        await event.edit(
            "❌ فرمت:\n\n"
            ".reaction @username ❤️\n"
            ".reaction متن ❤️\n"
            ".reaction @username متن ❤️"
        )

        return

    target = match.group(1).strip()

    emoji = match.group(2).strip()

    if not emoji:

        await event.edit(
            "❌ ایموجی ری‌اکشن مشخص نشده."
        )

        return

    user = None

    # --------------------------------------------------------
    # اگر اولین بخش @ یا ID باشد،
    # آن را به‌عنوان شخص در نظر می‌گیریم.
    # --------------------------------------------------------

    parts = target.split(
        maxsplit=1
    )

    possible_user = parts[0]

    if (
        possible_user.startswith("@")
        or possible_user.isdigit()
    ):

        user = await resolve_user(
            possible_user
        )

        if user:

            text = (
                parts[1].strip()
                if len(parts) > 1
                else None
            )

        else:

            text = target

    else:

        text = target

    rule = {
        "user_id": (
            getattr(user, "id", None)
            if user
            else None
        ),
        "user_name": (
            user_display_name(user)
            if user
            else None
        ),
        "text": text,
        "emoji": emoji,
        "created_at": datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }

    ensure_reaction_chat(
        event.chat_id
    )

    # جلوگیری از ثبت دقیقاً همان قانون
    for old_rule in reaction_rules[
        event.chat_id
    ]:

        if (
            old_rule.get("user_id")
            == rule.get("user_id")
            and old_rule.get("text")
            == rule.get("text")
            and old_rule.get("emoji")
            == rule.get("emoji")
        ):

            await event.edit(
                "⚠️ این قانون قبلاً فعال است."
            )

            return

    reaction_rules[
        event.chat_id
    ].append(
        rule
    )

    target_description = ""

    if user:

        target_description = (
            f"👤 شخص: {user_display_name(user)}"
        )

    if text:

        if target_description:
            target_description += "\n"

        target_description += (
            f"📝 متن: {text}"
        )

    if not target_description:

        target_description = "همه پیام‌ها"

    await event.edit(
        "✅ ری‌اکشن خودکار فعال شد.\n\n"
        f"{target_description}\n"
        f"❤️ ری‌اکشن: {emoji}"
    )


register_feature(
    ".reaction",
    "ری‌اکشن خودکار بر اساس شخص و/یا متن",
    "automation"
)


# ============================================================
# .STOPREACTION
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.stopreaction$"
    )
)
async def stop_reaction(event):

    if event.chat_id not in reaction_rules:

        await event.edit(
            "❌ در این چت قانون ری‌اکشنی فعال نیست."
        )

        return

    removed = len(
        reaction_rules.pop(
            event.chat_id
        )
    )

    await event.edit(
        f"🛑 {removed} قانون ری‌اکشن "
        f"در این چت متوقف شد."
    )


register_feature(
    ".stopreaction",
    "توقف ری‌اکشن خودکار در چت",
    "automation"
)


# ============================================================
# .REACTIONS
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.reactions$"
    )
)
async def show_reaction_rules(event):

    rules = reaction_rules.get(
        event.chat_id,
        []
    )

    if not rules:

        await event.edit(
            "📭 در این چت هیچ قانون ری‌اکشنی فعال نیست."
        )

        return

    lines = [
        "📋 قوانین ری‌اکشن این چت:\n"
    ]

    for index, rule in enumerate(
        rules,
        1
    ):

        target = []

        if rule.get("user_name"):
            target.append(
                f"👤 {rule['user_name']}"
            )

        if rule.get("text"):
            target.append(
                f"📝 {rule['text']}"
            )

        if not target:
            target_text = "همه پیام‌ها"
        else:
            target_text = " | ".join(
                target
            )

        lines.append(
            f"{index}. {target_text} → "
            f"{rule['emoji']}"
        )

    await event.edit(
        "\n".join(lines)
    )


register_feature(
    ".reactions",
    "نمایش قوانین ری‌اکشن فعال",
    "automation"
)


# ============================================================
# FISH DATA
# ============================================================

fish_tasks = {}


def is_fish_legendary(text):

    if not text:
        return False

    normalized = text.casefold()

    legendary_words = [
        "افسانه‌ای",
        "افسانه ای",
        "افسانهای",
        "legendary",
        "لجندری",
    ]

    return any(
        word.casefold() in normalized
        for word in legendary_words
    )


async def click_button_containing(
    message,
    text
):

    if not message.buttons:
        return False

    for row in message.buttons:

        for button in row:

            button_text = getattr(
                button,
                "text",
                ""
            ) or ""

            if text in button_text:

                try:

                    await button.click()

                    return True

                except Exception as error:

                    print(
                        "[BUTTON ERROR]",
                        error
                    )

                    return False

    return False


async def find_latest_fish_message(
    chat_id
):

    async for message in client.iter_messages(
        chat_id,
        limit=5
    ):

        text = (
            message.text or ""
        )

        if (
            "ماهی" in text
            and message.buttons
        ):

            return message

    return None


async def run_fish_workflow(
    client,
    chat_id
):

    try:

        # ----------------------------------------------------
        # فقط دستور ماهی
        # ----------------------------------------------------

        sent = await client.send_message(
            chat_id,
            "ماهی"
        )

        print(
            "[FISH] Sent ماهی:",
            sent.id
        )

        # ----------------------------------------------------
        # صبر برای نتیجه بازی
        # ----------------------------------------------------

        await asyncio.sleep(4)

        fish_message = (
            await find_latest_fish_message(
                chat_id
            )
        )

        if not fish_message:

            print(
                "[FISH] Result message not found."
            )

            return

        fish_text = (
            fish_message.text or ""
        )

        print(
            "[FISH] Result:",
            fish_text[:300]
        )

        # ----------------------------------------------------
        # ماهی افسانه‌ای → یخچال
        # ماهی عادی → فروش
        # ----------------------------------------------------

        if is_fish_legendary(
            fish_text
        ):

            print(
                "[FISH] Legendary fish detected."
            )

            clicked = (
                await click_button_containing(
                    fish_message,
                    "یخچال"
                )
            )

            if not clicked:

                # اگر دکمه نتیجه در پیام بعدی ظاهر شد
                await asyncio.sleep(2)

                async for message in client.iter_messages(
                    chat_id,
                    limit=5
                ):

                    if await click_button_containing(
                        message,
                        "یخچال"
                    ):

                        clicked = True
                        break

            if clicked:

                print(
                    "[FISH] Legendary fish stored in refrigerator."
                )

            else:

                print(
                    "[FISH] Refrigerator button not found."
                )

        else:

            print(
                "[FISH] Normal fish detected."
            )

            clicked = (
                await click_button_containing(
                    fish_message,
                    "فروش"
                )
            )

            if not clicked:

                await asyncio.sleep(2)

                async for message in client.iter_messages(
                    chat_id,
                    limit=5
                ):

                    if await click_button_containing(
                        message,
                        "فروش"
                    ):

                        clicked = True
                        break

            if clicked:

                print(
                    "[FISH] Normal fish sold."
                )

            else:

                print(
                    "[FISH] Sell button not found."
                )

    except asyncio.CancelledError:

        raise

    except Exception as error:

        print(
            "[FISH ERROR]",
            error
        )


async def fish_loop(
    chat_id
):

    while True:

        await run_fish_workflow(
            client,
            chat_id
        )

        await asyncio.sleep(
            31 * 60
        )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.fish$"
    )
)
async def start_fish_loop(event):

    chat_id = event.chat_id

    old_task = fish_tasks.get(
        chat_id
    )

    if old_task and not old_task.done():

        old_task.cancel()

    fish_tasks[
        chat_id
    ] = asyncio.create_task(
        fish_loop(chat_id)
    )

    await event.edit(
        "🎣 اتوماسیون ماهی فعال شد.\n"
        "ماهی افسانه‌ای → یخچال\n"
        "ماهی عادی → فروش\n"
        "⏱ هر ۳۱ دقیقه"
    )


@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.stopfish$"
    )
)
async def stop_fish_loop(event):

    task = fish_tasks.pop(
        event.chat_id,
        None
    )

    if task and not task.done():

        task.cancel()

        await event.edit(
            "🛑 اتوماسیون ماهی متوقف شد."
        )

    else:

        await event.edit(
            "❌ اتوماسیون ماهی در این چت فعال نیست."
        )


register_feature(
    ".fish",
    "ماهی افسانه‌ای → یخچال / ماهی عادی → فروش",
    "automation"
)

register_feature(
    ".stopfish",
    "توقف اتوماسیون ماهی",
    "automation"
)


# ============================================================
# REACTION LIST FROM CHANNEL POST
# ============================================================

def parse_telegram_message_link(
    link
):

    link = link.strip()

    parsed = urlparse(
        link
    )

    if parsed.scheme not in (
        "http",
        "https"
    ):

        return None

    host = (
        parsed.netloc
        .lower()
        .split(":")[0]
    )

    if host not in (
        "t.me",
        "telegram.me"
    ):

        return None

    parts = [
        p for p in
        parsed.path.split("/")
        if p
    ]

    if len(parts) < 2:
        return None

    # لینک خصوصی:
    # https://t.me/c/123456789/100
    if parts[0] == "c":

        if len(parts) < 3:
            return None

        try:

            channel_id = int(
                parts[1]
            )

            message_id = int(
                parts[2]
            )

        except ValueError:

            return None

        return {
            "kind": "private",
            "channel_id": channel_id,
            "message_id": message_id,
        }

    # لینک عمومی:
    # https://t.me/channel/123
    username = parts[0]

    try:

        message_id = int(
            parts[1]
        )

    except ValueError:

        return None

    return {
        "kind": "public",
        "username": username,
        "message_id": message_id,
    }


async def resolve_message_from_link(
    link
):

    parsed = parse_telegram_message_link(
        link
    )

    if not parsed:
        return None, None

    try:

        if parsed["kind"] == "public":

            entity = await client.get_entity(
                parsed["username"]
            )

        else:

            # ID لینک /c به Peer ID تبدیل می‌شود
            peer_id = int(
                "-100"
                + str(
                    parsed["channel_id"]
                )
            )

            entity = await client.get_entity(
                peer_id
            )

        message = await client.get_messages(
            entity,
            ids=parsed["message_id"]
        )

        if not message:
            return None, None

        return entity, message

    except Exception as error:

        print(
            "[REACTIONS LIST ERROR]",
            error
        )

        return None, None


async def get_reaction_users(
    entity,
    message_id
):

    users_by_id = {}

    reactions = []

    offset = ""

    while True:

        try:

            result = await client(
                functions.messages.GetMessageReactionsListRequest(
                    peer=entity,
                    id=message_id,
                    reaction=None,
                    offset=offset,
                    limit=100
                )
            )

        except Exception as error:

            print(
                "[REACTIONS API ERROR]",
                error
            )

            raise

        for user in getattr(
            result,
            "users",
            []
        ):

            users_by_id[
                user.id
            ] = user

        batch = getattr(
            result,
            "reactions",
            []
        )

        if not batch:
            break

        reactions.extend(
            batch
        )

        next_offset = getattr(
            result,
            "next_offset",
            None
        )

        if not next_offset:
            break

        offset = next_offset

    return reactions, users_by_id


def reaction_key(
    reaction
):

    if isinstance(
        reaction,
        types.ReactionEmoji
    ):

        return reaction.emoticon

    if isinstance(
        reaction,
        types.ReactionCustomEmoji
    ):

        return (
            f"custom:{reaction.document_id}"
        )

    if isinstance(
        reaction,
        types.ReactionPaid
    ):

        return "paid"

    return str(
        reaction
    )


# ============================================================
# .REACTCHECK
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.reactcheck(?:\s+(.+))?$"
    )
)
async def reaction_checker(event):

    link = (
        event.pattern_match.group(1)
        or ""
    ).strip()

    if not link:

        await event.edit(
            "❌ لینک پست را وارد کن.\n\n"
            ".reactcheck https://t.me/channel/123"
        )

        return

    await event.edit(
        "⏳ در حال بررسی ری‌اکشن‌های پست..."
    )

    entity, message = (
        await resolve_message_from_link(
            link
        )
    )

    if not entity or not message:

        await event.edit(
            "❌ پست پیدا نشد یا لینک نامعتبر است."
        )

        return

    try:

        reaction_items, users = (
            await get_reaction_users(
                entity,
                message.id
            )
        )

    except Exception as error:

        await event.edit(
            "❌ تلگرام اجازه دریافت لیست "
            "افراد ری‌اکشن‌دهنده این پست را نداد.\n\n"
            f"جزئیات: {error}"
        )

        return

    if not reaction_items:

        await event.edit(
            "📭 برای این پست ری‌اکشن قابل مشاهده‌ای پیدا نشد."
        )

        return

    grouped = {}

    for item in reaction_items:

        key = reaction_key(
            item.reaction
        )

        grouped.setdefault(
            key,
            []
        )

        user_id = getattr(
            item,
            "peer_id",
            None
        )

        if isinstance(
            user_id,
            types.PeerUser
        ):

            user_id = user_id.user_id

        if user_id in users:

            grouped[key].append(
                users[user_id]
            )

    lines = [
        "📊 ری‌اکشن‌های پست\n"
    ]

    for emoji, user_list in grouped.items():

        lines.append(
            f"{emoji} — {len(user_list)} نفر"
        )

        for user in user_list:

            lines.append(
                f"  • {user_mention(user)}"
            )

        lines.append("")

    await event.edit(
        "\n".join(lines),
        parse_mode="html",
        link_preview=False
    )


register_feature(
    ".reactcheck",
    "نمایش افرادی که روی یک پست ری‌اکشن زده‌اند",
    "tools"
)


# ============================================================
# .READMENTIONS
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.readmentions$"
    )
)
async def read_mentions(event):

    try:

        # API رسمی Telegram برای خواندن Mentions
        await client(
            functions.messages.ReadMentionsRequest(
                peer=event.chat_id
            )
        )

        await event.edit(
            "✅ منشن‌های این چت سین شدند."
        )

    except Exception as error:

        print(
            "[MENTIONS ERROR]",
            error
        )

        await event.edit(
            f"❌ خطا در سین کردن منشن‌ها:\n{error}"
        )


register_feature(
    ".readmentions",
    "سین کردن پیام‌های دارای منشن",
    "messages"
)


# ============================================================
# STATUS HELPERS
# ============================================================

async def status_chat_name(
    chat_id
):

    return await get_chat_display(
        chat_id
    )


def format_rule_target(
    rule
):

    parts = []

    if rule.get("user_name"):

        parts.append(
            f"👤 {rule['user_name']}"
        )

    if rule.get("text"):

        parts.append(
            f"📝 «{rule['text']}»"
        )

    if not parts:

        parts.append(
            "همه پیام‌ها"
        )

    return " | ".join(
        parts
    )


async def build_status():

    lines = [
        "📊 **گزارش کامل سلف‌بات**",
        ""
    ]

    # --------------------------------------------------------
    # .CAT
    # --------------------------------------------------------

    lines.append(
        "🐱 **.cat**"
    )

    if cat_chats:

        for chat_id in cat_chats:

            name = await status_chat_name(
                chat_id
            )

            lines.append(
                f"  ✅ فعال — {name}"
            )

    else:

        lines.append(
            "  ❌ غیرفعال"
        )

    lines.append("")

    # --------------------------------------------------------
    # .FISH
    # --------------------------------------------------------

    lines.append(
        "🎣 **.fish**"
    )

    active_fish = [
        chat_id
        for chat_id, task
        in fish_tasks.items()
        if task and not task.done()
    ]

    if active_fish:

        for chat_id in active_fish:

            name = await status_chat_name(
                chat_id
            )

            lines.append(
                f"  ✅ فعال — {name}"
            )

    else:

        lines.append(
            "  ❌ غیرفعال"
        )

    lines.append("")

    # --------------------------------------------------------
    # .REPLY
    # --------------------------------------------------------

    lines.append(
        "🤖 **.reply**"
    )

    if reply_rules:

        for chat_id, rules in reply_rules.items():

            name = await status_chat_name(
                chat_id
            )

            lines.append(
                f"  📍 {name}"
            )

            for trigger, response in rules.items():

                lines.append(
                    f"     «{trigger}» → «{response}»"
                )

    else:

        lines.append(
            "  ❌ غیرفعال"
        )

    lines.append("")

    # --------------------------------------------------------
    # .REACTION
    # --------------------------------------------------------

    lines.append(
        "❤️ **.reaction**"
    )

    if reaction_rules:

        for chat_id, rules in reaction_rules.items():

            if not rules:
                continue

            name = await status_chat_name(
                chat_id
            )

            lines.append(
                f"  📍 {name}"
            )

            for index, rule in enumerate(
                rules,
                1
            ):

                lines.append(
                    f"     {index}. "
                    f"{format_rule_target(rule)} "
                    f"→ {rule['emoji']}"
                )

    else:

        lines.append(
            "  ❌ غیرفعال"
        )

    lines.append("")

    # --------------------------------------------------------
    # .SET
    # --------------------------------------------------------

    lines.append(
        "⏰ **.set**"
    )

    if scheduled_messages:

        for chat_id, count in scheduled_messages.items():

            name = await status_chat_name(
                chat_id
            )

            lines.append(
                f"  📍 {name} — "
                f"{count} پیام زمان‌بندی‌شده"
            )

    else:

        lines.append(
            "  ❌ موردی ثبت نشده"
        )

    lines.append("")

    # --------------------------------------------------------
    # UPTIME
    # --------------------------------------------------------

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

    lines.append(
        "⏱ **.uptime**"
    )

    lines.append(
        f"  {hours} ساعت، "
        f"{minutes} دقیقه، "
        f"{seconds} ثانیه"
    )

    lines.append("")

    return "\n".join(
        lines
    )


# 
# ============================================================
# .INFO
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.i(?:nfo)?$"
    )
)
async def info_command(event):

    lines = [
        "📋 **دستورات سلف‌بات**",
        ""
    ]

    for command, data in FEATURES.items():

        lines.append(
            f"{command} : {data['description']}"
        )

    await event.edit(
        "\n".join(lines)
    )


register_feature(
    ".info",
    "فهرست کوتاه دستورات",
    "system"
)

register_feature(
    ".i",
    "فهرست کوتاه دستورات",
    "system"
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


register_feature(
    ".ping",
    "بررسی آنلاین بودن سلف‌بات",
    "system"
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

    try:

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

    except Exception as error:

        await event.edit(
            f"❌ خطا:\n{error}"
        )


register_feature(
    ".whoami",
    "نمایش اطلاعات اکانت",
    "account"
)


# ============================================================
# DYNAMIC STATUS - GENERAL FEATURES
# ============================================================

async def append_general_feature_status(
    lines
):

    # --------------------------------------------------------
    # قابلیت‌هایی که وضعیت اختصاصی دارند قبلاً
    # در build_status نمایش داده شده‌اند.
    # این بخش فقط بقیه قابلیت‌ها را هم در Status می‌آورد.
    # --------------------------------------------------------

    special_features = {
        ".cat",
        ".stopcat",
        ".fish",
        ".stopfish",
        ".reply",
        ".stopreply",
        ".reaction",
        ".stopreaction",
        ".reactions",
        ".set",
        ".status",
        ".info",
        ".i",
        ".session",
        ".uptime",
        ".ping",
        ".whoami",
    }

    lines.append(
        "🧩 **سایر قابلیت‌ها**"
    )

    for command, data in FEATURES.items():

        if command in special_features:
            continue

        lines.append(
            f"  • {command} : "
            f"{data['description']}"
        )

    lines.append("")


# ============================================================
# REBUILD STATUS WITH ALL FEATURES
# ============================================================

_old_build_status = build_status


async def build_status():

    lines = [
        "📊 **گزارش کامل سلف‌بات**",
        ""
    ]

    # ========================================================
    # CAT
    # ========================================================

    lines.append(
        "🐱 **.cat**"
    )

    if cat_chats:

        for chat_id in sorted(
            cat_chats,
            key=str
        ):

            name = await status_chat_name(
                chat_id
            )

            lines.append(
                f"  ✅ فعال — {name}"
            )

    else:

        lines.append(
            "  ❌ هیچ چتی فعال نیست"
        )

    lines.append("")

    # ========================================================
    # FISH
    # ========================================================

    lines.append(
        "🎣 **.fish**"
    )

    active_fish = []

    for chat_id, task in fish_tasks.items():

        if task and not task.done():

            active_fish.append(
                chat_id
            )

    if active_fish:

        for chat_id in active_fish:

            name = await status_chat_name(
                chat_id
            )

            lines.append(
                f"  ✅ فعال — {name}"
            )

            lines.append(
                "     افسانه‌ای → یخچال | "
                "عادی → فروش | هر ۳۱ دقیقه"
            )

    else:

        lines.append(
            "  ❌ هیچ چتی فعال نیست"
        )

    lines.append("")

    # ========================================================
    # REPLY
    # ========================================================

    lines.append(
        "🤖 **.reply**"
    )

    if reply_rules:

        for chat_id, rules in reply_rules.items():

            name = await status_chat_name(
                chat_id
            )

            lines.append(
                f"  📍 {name}"
            )

            if not rules:

                lines.append(
                    "     بدون قانون"
                )

            for trigger, response in rules.items():

                lines.append(
                    f"     • "
                    f"«{trigger}» → "
                    f"«{response}»"
                )

    else:

        lines.append(
            "  ❌ هیچ قانونی فعال نیست"
        )

    lines.append("")

    # ========================================================
    # REACTION
    # ========================================================

    lines.append(
        "❤️ **.reaction**"
    )

    active_reaction_chats = 0
    total_reaction_rules = 0

    for chat_id, rules in reaction_rules.items():

        if not rules:
            continue

        active_reaction_chats += 1
        total_reaction_rules += len(
            rules
        )

        name = await status_chat_name(
            chat_id
        )

        lines.append(
            f"  📍 {name}"
        )

        for index, rule in enumerate(
            rules,
            1
        ):

            target_parts = []

            if rule.get("user_name"):

                target_parts.append(
                    f"👤 {rule['user_name']}"
                )

            if rule.get("text"):

                target_parts.append(
                    f"📝 «{rule['text']}»"
                )

            if target_parts:

                target = " | ".join(
                    target_parts
                )

            else:

                target = "همه پیام‌ها"

            lines.append(
                f"     {index}. "
                f"{target} → "
                f"{rule['emoji']}"
            )

    if active_reaction_chats == 0:

        lines.append(
            "  ❌ هیچ قانون فعالی نیست"
        )

    else:

        lines.append(
            f"  📊 مجموع: "
            f"{total_reaction_rules} قانون "
            f"در {active_reaction_chats} چت"
        )

    lines.append("")

    # ========================================================
    # SET / SCHEDULE
    # ========================================================

    lines.append(
        "⏰ **.set**"
    )

    if scheduled_messages:

        for chat_id, count in scheduled_messages.items():

            name = await status_chat_name(
                chat_id
            )

            lines.append(
                f"  📍 {name} — "
                f"{count} پیام زمان‌بندی‌شده"
            )

    else:

        lines.append(
            "  ❌ پیام زمان‌بندی‌شده‌ای ثبت نشده"
        )

    lines.append("")

    # ========================================================
    # DELETE
    # ========================================================

    lines.append(
        "🗑 **.delete**"
    )

    lines.append(
        "  ✅ فعال — حذف تعداد مشخصی از "
        "پیام‌های خود کاربر"
    )

    lines.append("")

    # ========================================================
    # SAVE
    # ========================================================

    lines.append(
        "💾 **.save**"
    )

    lines.append(
        "  ✅ فعال — ذخیره پیام ریپلای‌شده "
        "در Saved Messages"
    )

    lines.append("")

    # ========================================================
    # REACTCHECK
    # ========================================================

    lines.append(
        "📊 **.reactcheck**"
    )

    lines.append(
        "  ✅ فعال — بررسی ری‌اکشن‌های "
        "یک پست با لینک"
    )

    lines.append("")

    # ========================================================
    # READ MENTIONS
    # ========================================================

    lines.append(
        "🔔 **.readmentions**"
    )

    lines.append(
        "  ✅ فعال — سین کردن منشن‌های "
        "قابل دسترسی تلگرام"
    )

    lines.append("")

    # ========================================================
    # ACCOUNT
    # ========================================================

    lines.append(
        "👤 **.whoami**"
    )

    try:

        me = await client.get_me()

        username = (
            f"@{me.username}"
            if me.username
            else "بدون username"
        )

        lines.append(
            f"  👤 {me.first_name or ''} "
            f"{me.last_name or ''}".strip()
        )

        lines.append(
            f"  🔹 {username}"
        )

        lines.append(
            f"  🆔 {me.id}"
        )

    except Exception:

        lines.append(
            "  ⚠️ اطلاعات اکانت قابل دریافت نیست"
        )

    lines.append("")

    # ========================================================
    # UPTIME
    # ========================================================

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

    lines.append(
        "⏱ **.uptime**"
    )

    lines.append(
        f"  {hours} ساعت، "
        f"{minutes} دقیقه، "
        f"{seconds} ثانیه"
    )

    lines.append("")

    # ========================================================
    # SYSTEM COMMANDS
    # ========================================================

    lines.append(
        "⚙️ **دستورات سیستمی**"
    )

    lines.append(
        "  • .ping : آنلاین"
    )

    lines.append(
        "  • .session : Session فعال"
    )

    lines.append(
        "  • .info / .i : فهرست دستورات"
    )

    lines.append("")

    # ========================================================
    # ALL REGISTERED FEATURES
    # ========================================================

    lines.append(
        "🧩 **همه قابلیت‌های ثبت‌شده**"
    )

    for command, data in FEATURES.items():

        lines.append(
            f"  • {command} : "
            f"{data['description']}"
        )

    return "\n".join(
        lines
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

    try:

        report = await build_status()

        await event.edit(
            report
        )

    except Exception as error:

        print(
            "[STATUS ERROR]",
            error
        )

        await event.edit(
            f"❌ خطا در ساخت Status:\n{error}"
        )


# ============================================================
# FEATURE REGISTRATION
# ============================================================

# این قابلیت قبلاً در قسمت اول ثبت شده بود.
# چون handler آن نیز در قسمت اول وجود دارد،
# اینجا دوباره ثبت نمی‌کنیم.


# ============================================================
# SAFE TASK CLEANUP
# ============================================================

async def cancel_all_tasks():

    tasks = []

    for task in fish_tasks.values():

        if task and not task.done():

            tasks.append(
                task
            )

    for task in tasks:

        task.cancel()

    if tasks:

        await asyncio.gather(
            *tasks,
            return_exceptions=True
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

    # ========================================================
    # AUTHENTICATION
    # ========================================================
    #
    # این بخش عمداً همان سیستم اصلی است.
    # Session موجود همچنان اولویت دارد.
    #

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
        f"ID: {me.id}"
    )

    print(
        "======================================"
    )

    try:

        await client.run_until_disconnected()

    finally:

        await cancel_all_tasks()


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
