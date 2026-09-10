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
# .KHOFASH (BAT GAME AUTO-HUNTER - HYPER OPTIMIZED)
# ============================================================

import asyncio
from telethon.tl.types import MessageEntityCustomEmoji

khofash_chats = set()

BAT_ID_TO_REPLY_EMOJI = {
    5828139598399677018: "✨",
    5827686629673803792: "🧄",
    5828137768743610848: "👀",
    5827736077632282320: "👶",
    5830144081111556696: "💦",
    5827807962499918016: "👾",
    5828095703833911977: "🌦️",
    5827967623614177566: "💨",
    5830194250624539956: "⚫️",
    5830044845892181995: "🕷️",
    5828144619216445823: "🧼",
    5827872691952033692: "🐥",
    5827921796313129733: "💙",
    5827736876496199660: "💙",
    5828014859664498597: "🙍‍♀",
    5830118178163793495: "🧽",
    5830016778280903350: "🌹",
    5827796245829131387: "🤖",
    5828055404155772825: "💥",
    5830216485670232327: "🍋",
    5830226089217106100: "🎭",
    5830387013051752377: "🗻",
    5830310657123164469: "🪞",
    5830157146402071095: "🃏",
    5830464923758501937: "❤️",
    5827802293143085479: "🚒",
    5827846831953944550: "🌕",
    5828047969567384122: "🧛",
    5827706798840225857: "🧊",
    5827788154110746763: "😇",
    5827881556764537070: "😈",
    5829945000787451381: "🔥",
    5830362162370977979: "🇫🇷",
    5828140203990065484: "⭐️",
    5830240017796046141: "🌧",
    5830358739282041409: "🪙",
    5828064006975267325: "⚡️",
    5829968266625293241: "🌑"
}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.khofash$"))
async def start_khofash(event):
    khofash_chats.add(event.chat_id)
    try:
        await event.delete()
    except Exception:
        pass

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopkhofash$"))
async def stop_khofash(event):
    khofash_chats.discard(event.chat_id)
    await event.edit("🛑 شکارچی خفاش متوقف شد.")

async def process_khofash_message(message):
    if message.chat_id not in khofash_chats:
        return
    
    if message.entities:
        for entity in message.entities:
            if isinstance(entity, MessageEntityCustomEmoji):
                emoji_char = BAT_ID_TO_REPLY_EMOJI.get(entity.document_id)
                if emoji_char:
                    # ارسال آنی بدون معطلی و انتظار برای پاسخ ربات اصلی
                    asyncio.create_task(message.reply(emoji_char))
                    break

@client.on(events.NewMessage())
async def khofash_new_message(event):
    await process_khofash_message(event.message)

@client.on(events.MessageEdited())
async def khofash_edited_message(event):
    await process_khofash_message(event.message)

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
        await event.edit("❌ فرمت زمان نامعتبر است.")
        return

    chat_id = event.chat_id
    await event.edit(f"🎣 اتوماسیون ماهی فعال شد (هر {interval_str} یک‌بار).")

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
# .AUTOMEO (AUTO MEO EVERY 5 MINUTES)
# ============================================================

automeo_tasks = {}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.automeo$"))
async def start_automeo(event):
    chat_id = event.chat_id
    if chat_id in automeo_tasks:
        await event.edit("⚠️ ارسال خودکار meo از قبل در این چت فعال است.")
        return

    await event.edit("🐱 **ارسال خودکار meo هر ۵ دقیقه فعال شد.**")

    async def meo_loop():
        while True:
            try:
                await client.send_message(chat_id, "meo")
            except Exception as err:
                print("[AUTOMEO ERROR]", err)
            await asyncio.sleep(300)

    task = asyncio.create_task(meo_loop())
    automeo_tasks[chat_id] = task

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopautomeo$"))
async def stop_automeo(event):
    chat_id = event.chat_id
    task = automeo_tasks.pop(chat_id, None)
    if task:
        task.cancel()
        await event.edit("🛑 ارسال خودکار meo در این چت متوقف شد.")
    else:
        await event.edit("❌ هیچ ارسال خودکاری در این چت فعال نیست.")




# ============================================================
# .TAG (SMART, NON-REPEATABLE & FULL COUNT WITH PRIORITY)
# ============================================================

recent_tagged = {}

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.tag(?:\s+(\d+))?$"))
async def tag_users(event):
    if not event.is_group and not event.is_channel:
        await event.edit("❌ این دستور فقط در گروه‌ها یا سوپرگروه‌ها قابل استفاده است.")
        return

    match = event.pattern_match
    requested_count = int(match.group(1)) if match.group(1) else 10
    if requested_count > 100:
        requested_count = 100

    await event.edit(f"⏳ در حال استخراج و مرتب‌سازی هوشمند کاربران...")

    chat_id = event.chat_id
    if chat_id not in recent_tagged:
        recent_tagged[chat_id] = []

    online_pool = []
    recent_pool = []
    other_pool = []
    
    seen_ids = set()
    me_id = (await client.get_me()).id

    try:
        async for msg in client.iter_messages(chat_id, limit=300):
            if not msg.sender_id or msg.sender_id in seen_ids or msg.sender_id == me_id:
                continue
            
            user = msg.sender
            if not user or user.bot or user.deleted:
                continue
                
            seen_ids.add(msg.sender_id)

            if msg.sender_id in recent_tagged[chat_id]:
                continue

            name = getattr(user, 'first_name', None) or "دوست"
            if getattr(user, 'username', None):
                mention = f"@{user.username}"
            else:
                mention = f"[{name}](tg://user?id={user.id})"

            status = getattr(user, 'status', None)
            from telethon.tl.types import UserStatusOnline, UserStatusRecently
            
            if isinstance(status, UserStatusOnline):
                online_pool.append((msg.sender_id, mention))
            elif isinstance(status, UserStatusRecently):
                recent_pool.append((msg.sender_id, mention))
            else:
                other_pool.append((msg.sender_id, mention))

        if len(online_pool) + len(recent_pool) + len(other_pool) < requested_count:
            async for user in client.iter_participants(chat_id):
                if not user or user.bot or user.deleted or user.id == me_id or user.id in seen_ids:
                    continue
                if user.id in recent_tagged[chat_id]:
                    continue
                
                seen_ids.add(user.id)
                name = getattr(user, 'first_name', None) or "دوست"
                if getattr(user, 'username', None):
                    mention = f"@{user.username}"
                else:
                    mention = f"[{name}](tg://user?id={user.id})"
                
                status = getattr(user, 'status', None)
                from telethon.tl.types import UserStatusOnline, UserStatusRecently
                if isinstance(status, UserStatusOnline):
                    online_pool.append((user.id, mention))
                elif isinstance(status, UserStatusRecently):
                    recent_pool.append((user.id, mention))
                else:
                    other_pool.append((user.id, mention))

        import random
        random.shuffle(online_pool)
        random.shuffle(recent_pool)
        random.shuffle(other_pool)

        full_pool = online_pool + recent_pool + other_pool
        selected_pairs = full_pool[:requested_count]
        users_to_tag = [item[1] for item in selected_pairs]

        if not users_to_tag:
            recent_tagged[chat_id].clear()
            await event.edit("🔄 لیست تگ‌های قبلی پاک شد، لطفاً مجدداً دستور `.tag` را ارسال کنید.")
            return

        for uid, _ in selected_pairs:
            recent_tagged[chat_id].append(uid)
        if len(recent_tagged[chat_id]) > 150:
            recent_tagged[chat_id] = recent_tagged[chat_id][-150:]

        chunk_size = 5
        for i in range(0, len(users_to_tag), chunk_size):
            chunk = users_to_tag[i:i + chunk_size]
            text = "👥 **دوستان عزیز:**\n" + " ".join(chunk)
            await client.send_message(chat_id, text)
            await asyncio.sleep(1.5)

        await event.delete()
    except Exception as error:
        await event.edit(f"❌ خطا:\n{error}")




# ============================================================
# .STATUS
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

    if khofash_chats:
        kh_lines = []
        for cid in khofash_chats:
            info = await get_chat_display_info(cid)
            kh_lines.append(f"  • {info}")
        report.append("🦇 **شکارچی خفاش (.khofash):**\n" + "\n".join(kh_lines))
    else:
        report.append("🦇 **شکارچی خفاش (.khofash):** غیرفعال")

    global fish_task_running
    if fish_task_running and not fish_task_running.done():
        report.append("🎣 **اتوماسیون .fish:** فعال")
    else:
        report.append("🎣 **اتوماسیون .fish:** غیرفعال")

    if automeo_tasks:
        meo_lines = []
        for cid in automeo_tasks.keys():
            info = await get_chat_display_info(cid)
            meo_lines.append(f"  • {info}")
        report.append("🐱 **ارسال خودکار meo (.automeo):**\n" + "\n".join(meo_lines))
    else:
        report.append("🐱 **ارسال خودکار meo (.automeo):** غیرفعال")

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

    if kazino_active_chats:
        report.append("🎰 **اتوماسیون کازینو (.kazino):** فعال")
    else:
        report.append("🎰 **اتوماسیون کازینو (.kazino):** غیرفعال")

    await event.edit("\n".join(report), link_preview=False)


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
