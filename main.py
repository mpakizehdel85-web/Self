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
    print(f"[WEB] Login page running on port {PORT}")
    return server

# ============================================================
# AUTHENTICATION
# ============================================================

async def authenticate():
    await client.connect()
    if await client.is_user_authorized():
        set_login_state("authenticated", "Existing Telegram session is valid.")
        print("[LOGIN] Existing Telegram session reused.")
        return

    print("[LOGIN] Session is not authorized.")
    set_login_state("starting", "Requesting a new Telegram login code...")
    await client.send_code_request(PHONE)
    set_login_state("code", "Telegram login code requested.")
    print("[LOGIN] Waiting for login code...")
    code = await code_queue.get()

    try:
        await client.sign_in(phone=PHONE, code=code)
    except SessionPasswordNeededError:
        print("[LOGIN] Telegram requires 2FA password.")
        set_login_state("password", "Telegram requires your 2FA password.")
        password = PASSWORD_2FA if PASSWORD_2FA else await password_queue.get()
        await client.sign_in(password=password)

    set_login_state("authenticated", "Authentication successful.")
    print("[LOGIN] Authentication successful.")

# ============================================================
# HELPER FOR CHAT TITLE/LINK
# ============================================================

async def get_chat_display_info(chat_id):
    try:
        chat = await client.get_entity(chat_id)
        name = getattr(chat, 'title', None) or getattr(chat, 'first_name', 'Unknown')
        if getattr(chat, 'username', None):
            return f"[{name}](https://t.me/{chat.username})"
        else:
            return f"{name} (`{chat_id}`)"
    except Exception:
        return f"Chat ID: `{chat_id}`"

# ============================================================
# COMMAND REGISTRY & .I / .STATUS HELPERS
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
    ".tag": "تگ کردن گروهی کاربران",
    ".stopall": "توقف تمام قابلیت‌های فعال در همه جا",
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
# .SESSION
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

# ============================================================
# TIME PARSER & .SET
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

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.set(?:\s|$)?"))
async def set_scheduled_messages(event):
    match = re.fullmatch(r"\.set\s+(\d+)\s+(.+?)\s+(\d+(?:\.\d+)?[smh]?)", event.raw_text.strip(), re.IGNORECASE)
    if not match:
        await event.edit("❌ فرمت اشتباه.\nمثال:\n`.set 3 سلام 5m`")
        return

    count = int(match.group(1))
    message_text = match.group(2).strip()
    interval = parse_interval(match.group(3))

    if count <= 0 or interval is None or interval <= 0:
        await event.edit("❌ مقادیر نامعتبر است.")
        return

    now = datetime.now(timezone.utc)
    scheduled = 0
    try:
        for index in range(1, count + 1):
            schedule_time = now + timedelta(seconds=interval * index)
            await client.send_message(event.chat_id, message_text, schedule=schedule_time)
            scheduled += 1
        await event.edit(f"✅ {scheduled} پیام زمان‌بندی شد.")
    except Exception as error:
        await event.edit(f"❌ خطا: {error}")

# ============================================================
# .REPLY
# ============================================================

reply_rules = {}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.reply(?:\s|$)?"))
async def create_reply(event):
    match = re.fullmatch(r"\.reply\s+(.+?)\s+to\s+(.+)", event.raw_text.strip(), re.IGNORECASE)
    if not match:
        await event.edit("❌ فرمت:\n`.reply جواب to متن`")
        return

    response = match.group(1).strip()
    trigger = match.group(2).strip()

    if event.chat_id not in reply_rules:
        reply_rules[event.chat_id] = {}

    reply_rules[event.chat_id][trigger.casefold()] = response
    await event.edit(f"✅ ریپلای فعال شد\nهدف: {trigger}\nپاسخ: {response}")

@client.on(events.NewMessage())
async def automatic_reply(event):
    if event.out or event.reply_to_msg_id:
        return
    chat_id = event.chat_id
    if chat_id not in reply_rules:
        return
    incoming = event.raw_text.strip()
    response = reply_rules[chat_id].get(incoming.casefold())
    if response:
        try:
            await event.reply(response)
        except Exception:
            pass

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopreply$"))
async def stop_reply(event):
    reply_rules.pop(event.chat_id, None)
    await event.edit("🛑 ریپلای خودکار متوقف شد.")

# ============================================================
# .CAT (SILENT MODE)
# ============================================================

cat_chats = set()

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.cat$"))
async def start_cat(event):
    cat_chats.add(event.chat_id)
    try:
        await event.delete()
    except Exception:
        pass

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopcat$"))
async def stop_cat(event):
    cat_chats.discard(event.chat_id)
    await event.edit("🛑 حالت نجات پیشی متوقف شد.")

async def check_cat_message(message):
    if message.chat_id not in cat_chats or not message.buttons:
        return
    for row in message.buttons:
        for button in row:
            text = getattr(button, "text", "")
            if text and "نجات پیشی خیابونی" in text:
                try:
                    await message.click(text=text)
                except Exception:
                    pass
                return

@client.on(events.NewMessage())
async def cat_new_message(event):
    await check_cat_message(event.message)

@client.on(events.MessageEdited())
async def cat_edited_message(event):
    await check_cat_message(event.message)

# ============================================================
# .DELETE (REPLACES .PURGE)
# ============================================================

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.delete(?:\s+(\d+))?$"))
async def delete_messages(event):
    match = event.pattern_match
    count = int(match.group(1)) if match.group(1) else 10
    deleted = 0
    async for message in client.iter_messages(event.chat_id, limit=count, from_user="me"):
        try:
            await message.delete()
            deleted += 1
        except Exception:
            pass
    print(f"[DELETE] Deleted {deleted} messages.")

# ============================================================
# .SAVE
# ============================================================

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.save$"))
async def save_message(event):
    if not event.is_reply:
        await event.edit("❌ لطفا روی پیامی که می‌خواهید ذخیره کنید ریپلای بزنید.")
        return
    reply_msg = await event.get_reply_message()
    await client.forward_messages("me", reply_msg)
    await event.edit("✅ پیام در Saved Messages ذخیره شد.")

# ============================================================
# .UPTIME
# ============================================================

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.uptime$"))
async def uptime_bot(event):
    uptime_seconds = int(time.time() - START_TIME)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    await event.edit(f"⏱ **آب‌تایم بات:** {hours} ساعت و {minutes} دقیقه و {seconds} ثانیه")

# ============================================================
# .FISH (DYNAMIC INTERVAL)
# ============================================================

fish_task_running = None

async def run_fish_workflow(client, chat_id):
    try:
        await client.send_message(chat_id, "ماهی")
        await asyncio.sleep(4)

        async for message in client.iter_messages(chat_id, limit=3):
            if message.text and message.buttons:
                text_content = message.text
                if "افسانه‌ای" in text_content or "افسانه ای" in text_content:
                    target_text = "بندازش تو یخچال"
                else:
                    target_text = "فروش ماهی"

                clicked = False
                for row in message.buttons:
                    for button in row:
                        if target_text in getattr(button, "text", ""):
                            await button.click()
                            clicked = True
                            break
                    if clicked:
                        break
                break
    except Exception as error:
        print("[FISH ERROR]", error)

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.fish(?:\s|$)?"))
async def start_fish_loop(event):
    global fish_task_running
    
    cmd_text = event.raw_text.strip()
    match = re.search(r"^\.fish\s+(.+)$", cmd_text, re.IGNORECASE)
    
    if not match:
        await event.edit("❌ لطفا زمان را وارد کنید.\nمثال:\n`.fish 11m` یا `.fish 30s` یا `.fish 1h`")
        return
        
    interval_str = match.group(1).strip()
    interval_seconds = parse_interval(interval_str)
    
    if interval_seconds is None or interval_seconds <= 0:
        await event.edit("❌ فرمت زمان نامعتبر است. از s (ثانیه)، m (دقیقه) یا h (ساعت) استفاده کنید.")
        return

    chat_id = event.chat_id
    await event.edit(f"🎣 اتوماسیون ماهی فعال شد (هر {interval_str} یک‌بار | افسانه‌ای ⬅️ یخچال، عادی ⬅️ فروش).")

    async def loop_job():
        while True:
            await run_fish_workflow(client, chat_id)
            await asyncio.sleep(interval_seconds)

    if fish_task_running:
        fish_task_running.cancel()
    fish_task_running = asyncio.create_task(loop_job())

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopfish$"))
async def stop_fish_loop(event):
    global fish_task_running
    if fish_task_running:
        fish_task_running.cancel()
        fish_task_running = None
        await event.edit("🛑 اتوماسیون ماهی متوقف شد.")
    else:
        await event.edit("❌ هیچ اتوماسیونی فعالی وجود ندارد.")

# ============================================================
# AUTO-REACTION FEATURE (SUPPORTING USERNAME, NUMERIC ID, & REPLY)
# ============================================================

autoreact_rules = {}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.autoreact(?:\s|$)"))
async def set_autoreact(event):
    cmd_text = event.raw_text.strip()
    match = re.match(r"^\.autoreact\s+(.+?)\s+([^\s]+)$", cmd_text)
    
    target = None
    emoji = None

    if match:
        target = match.group(1).strip()
        emoji = match.group(2).strip()
    elif event.is_reply:
        parts = cmd_text.split()
        if len(parts) == 2:
            emoji = parts[1].strip()
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.sender_id:
                target = str(reply_msg.sender_id)

    if not target or not emoji:
        await event.edit("❌ فرمت اشتباه.\nمثال:\n`.autoreact @username ❤️`\nیا روی پیام شخص ریپلای کنید و بنویسید:\n`.autoreact ❤️`")
        return

    if event.chat_id not in autoreact_rules:
        autoreact_rules[event.chat_id] = {}

    autoreact_rules[event.chat_id][target.casefold()] = emoji
    await event.edit(f"✅ ریکشن خودکار فعال شد.\nهدف: {target}\nایموجی: {emoji}")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopautoreact$"))
async def stop_autoreact(event):
    autoreact_rules.pop(event.chat_id, None)
    await event.edit("🛑 ریکشن خودکار در این چت متوقف شد.")

@client.on(events.NewMessage())
async def handle_autoreact(event):
    chat_id = event.chat_id
    if chat_id not in autoreact_rules:
        return
    
    rules = autoreact_rules[chat_id]
    sender = await event.get_sender()
    sender_id_str = str(sender.id) if sender else ""
    sender_username = f"@{sender.username}".casefold() if sender and getattr(sender, 'username', None) else ""
    msg_text = event.raw_text or ""

    for target, emoji in rules.items():
        t_clean = target.casefold()
        matched = False

        if t_clean.startswith("@") or t_clean.isdigit():
            if t_clean == sender_id_str or t_clean == sender_username:
                matched = True
        else:
            if t_clean in msg_text.casefold():
                matched = True

        if matched:
            try:
                from telethon.tl.functions.messages import SendReactionRequest
                from telethon.tl.types import ReactionEmoji
                await client(SendReactionRequest(
                    peer=event.chat_id,
                    msg_id=event.id,
                    reaction=[ReactionEmoji(emoticon=emoji)]
                ))
            except Exception as err:
                print("[AUTOREACT ERROR]", err)
            break

# ============================================================
# .READMENTIONS (CURRENT CHAT ONLY)
# ============================================================

@client.on(
    events.NewMessage(
        outgoing=True,
        pattern=r"^\.readmentions$"
    )
)
async def read_mentions(event):

    await event.edit(
        "⏳ در حال سین کردن منشن‌های این چت..."
    )

    try:
        await client(
            functions.messages.ReadMentionsRequest(
                peer=event.chat_id
            )
        )

        await event.edit(
            "✅ منشن‌های این چت با موفقیت سین شدند."
        )

    except Exception as error:

        print(
            "[READMENTIONS ERROR]",
            error
        )

        await event.edit(
            f"❌ خطا در سین کردن منشن‌های این چت:\n{error}"
        )

# ============================================================
# .USERINFO (GET ID & USER DETAILS VIA REPLY OR USERNAME/ID)
# ============================================================

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.userinfo(?:\s|$)?"))
async def user_info(event):
    target_user = None
    cmd_text = event.raw_text.strip()
    match = re.match(r"^\.userinfo\s+(.+)$", cmd_text)

    try:
        if match:
            query = match.group(1).strip()
            target_user = await client.get_entity(query)
        elif event.is_reply:
            reply_msg = await event.get_reply_message()
            if reply_msg:
                target_user = await client.get_entity(reply_msg.sender_id)
        else:
            target_user = await event.get_sender()

        if not target_user:
            await event.edit("❌ کاربر مورد نظر یافت نشد.")
            return

        name = f"{target_user.first_name or ''} {target_user.last_name or ''}".strip()
        username = f"@{target_user.username}" if getattr(target_user, 'username', None) else "ندارد"
        user_id = target_user.id
        is_bot = "بله" if getattr(target_user, 'bot', False) else "خیر"
        is_premium = "بله" if getattr(target_user, 'premium', False) else "خیر"

        info_text = (
            f"👤 **مشخصات حساب کاربری:**\n\n"
            f"• نام: `{name}`\n"
            f"• آیدی عددی: `{user_id}`\n"
            f"• یوزرنیم: {username}\n"
            f"• ربات است؟: {is_bot}\n"
            f"• پرمیوم است؟: {is_premium}"
        )
        await event.edit(info_text)

    except Exception as error:
        await event.edit(f"❌ خطا در دریافت اطلاعات کاربر:\n{error}")

# ============================================================
# .TAG (TAG ACTIVE/ONLINE USERS IN GROUP)
# ============================================================

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.tag(?:\s+(\d+))?$"))
async def tag_users(event):
    if not event.is_group and not event.is_channel:
        await event.edit("❌ این دستور فقط در گروه‌ها یا سوپرگروه‌ها قابل استفاده است.")
        return

    match = event.pattern_match
    count = int(match.group(1)) if match.group(1) else 10
    if count > 50:
        count = 50

    await event.edit(f"⏳ در حال استخراج و تگ کردن {count} کاربر...")

    users_to_tag = []
    try:
        async for user in client.iter_participants(event.chat_id, limit=100):
            if user.bot or user.deleted:
                continue
            name = user.first_name or "دوست"
            if user.username:
                mention = f"@{user.username}"
            else:
                mention = f"[{name}](tg://user?id={user.id})"
            
            users_to_tag.append(mention)
            if len(users_to_tag) >= count:
                break

        if not users_to_tag:
            await event.edit("❌ کاربری برای تگ کردن یافت نشد.")
            return

        chunk_size = 5
        for i in range(0, len(users_to_tag), chunk_size):
            chunk = users_to_tag[i:i + chunk_size]
            text = "👥 **دوستان عزیز:**\n" + " ".join(chunk)
            await client.send_message(event.chat_id, text)
            await asyncio.sleep(1.5)

        await event.delete()
    except Exception as error:
        await event.edit(f"❌ خطا در تگ کردن کاربران:\n{error}")

# ============================================================
# .STOPALL (NEW: STOP ALL FEATURES GLOBALLY)
# ============================================================

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopall$"))
async def stop_all_features(event):
    global fish_task_running, reply_rules, cat_chats, autoreact_rules

    # متوقف کردن اتوماسیون ماهی
    if fish_task_running:
        fish_task_running.cancel()
        fish_task_running = None

    # پاک کردن تمام قوانین پاسخ خودکار در تمام چت‌ها
    reply_rules.clear()

    # پاک کردن تمام چت‌های حالت پیشی (.cat)
    cat_chats.clear()

    # پاک کردن تمام قوانین ریکشن خودکار در تمام چت‌ها
    autoreact_rules.clear()

    await event.edit(
        "🛑 **تمام قابلیت‌های تنظیمی بات با موفقیت متوقف و پاکسازی شدند!**\n\n"
        "• اتوماسیون ماهی متوقف شد.\n"
        "• تمام پاسخ‌های خودکار (`.reply`) پاک شدند.\n"
        "• تمام چت‌های حالت پیشی (`.cat`) غیرفعال شدند.\n"
        "• تمام ریکشن‌های خودکار (`.autoreact`) متوقف شدند."
    )

# ============================================================
# .STATUS (UPDATED)
# ============================================================

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.status$"))
async def bot_status_report(event):
    report = ["📊 **گزارش دقیق وضعیت سلف‌بات:**\n"]

    if cat_chats:
        cat_lines = []
        for cid in cat_chats:
            info = await get_chat_display_info(cid)
            cat_lines.append(f"  • {info}")
        report.append(f"🐱 **حالت .cat (فعال - مخفی):**\n" + "\n".join(cat_lines))
    else:
        report.append("🐱 **حالت .cat:** غیرفعال")

    global fish_task_running
    if fish_task_running and not fish_task_running.done():
        report.append("🎣 **اتوماسیون .fish:** فعال (افسانه‌ای ⬅️ یخچال، عادی ⬅️ فروش)")
    else:
        report.append("🎣 **اتوماسیون .fish:** غیرفعال")

    if reply_rules:
        reply_lines = []
        for cid, rules in reply_rules.items():
            info = await get_chat_display_info(cid)
            reply_lines.append(f"  • چت {info} ({len(rules)} قانون)")
        report.append("🤖 **پاسخ خودکار (.reply):**\n" + "\n".join(reply_lines))
    else:
        report.append("🤖 **پاسخ خودکار (.reply):** غیرفعال")

    if autoreact_rules:
        react_lines = []
        for cid, rules in autoreact_rules.items():
            info = await get_chat_display_info(cid)
            react_lines.append(f"  • چت {info} ({len(rules)} قانون)")
        report.append("❤️ **ریکشن خودکار (.autoreact):**\n" + "\n".join(react_lines))
    else:
        report.append("❤️ **ریکشن خودکار (.autoreact):** غیرفعال")

    await event.edit("\n".join(report), link_preview=False)

# ============================================================
# .PING
# ============================================================

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
async def ping(event):
    await event.edit("✅ Userbot is online.")

# ============================================================
# .WHOAMI
# ============================================================

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
# START
# ============================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Userbot stopped.")
    except Exception as error:
        print("USERBOT ERROR:", error)
        raise
