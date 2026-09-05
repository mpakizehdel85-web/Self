import os
import re
import time
import asyncio
import random
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient, events, functions
from telethon.sessions import StringSession
from telethon.tl.types import UserStatusOnline, UserStatusRecently, InputMediaDice, ReactionEmoji
from telethon.tl.functions.messages import SendReactionRequest
from aiohttp import web

# ============================================================
# CONFIGURATION
# ============================================================
API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
STRING_SESSION = os.environ.get("TELEGRAM_SESSION", "")
PORT = int(os.environ.get("PORT", 8080))
START_TIME = time.time()

# ============================================================
# WEB SERVER (For keep-alive in hosting)
# ============================================================
async def handle_ping(request):
    return web.Response(text="Bot is running!")

def start_web_server():
    app = web.Application()
    app.add_routes([web.get('/', handle_ping)])
    runner = web.AppRunner(app)
    MAIN_LOOP.create_task(runner.setup())
    MAIN_LOOP.create_task(web.TCPSite(runner, '0.0.0.0', PORT).start())
    print(f"Web server started on port {PORT}")

# ============================================================
# TELEGRAM CLIENT SETUP
# ============================================================
client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

async def authenticate():
    await client.connect()
    if not await client.is_user_authorized():
        print("❌ Session is invalid or not authorized.")
        return
    print("✅ Successfully authorized.")

async def get_chat_display_info(chat_id):
    try:
        entity = await client.get_entity(chat_id)
        return getattr(entity, 'title', None) or getattr(entity, 'username', None) or str(chat_id)
    except Exception:
        return str(chat_id)

# ============================================================
# COMMAND REGISTRY
# ============================================================
COMMAND_DESCRIPTIONS = {
    ".session": "دریافت رشته سشن",
    ".set": "زمان‌بندی ارسال پیام",
    ".reply": "تنظیم پاسخ خودکار",
    ".stopreply": "توقف پاسخ خودکار",
    ".cat": "حالت نجات پیشی (مخفی و خودکار)",
    ".stopcat": "توقف نجات پیشی",
    ".khofash": "حالت شکار خفاش (مخفی و خودکار با ریپلای سریع)",
    ".stopkhofash": "توقف شکار خفاش",
    ".delete": "پاکسازی پیام‌ها",
    ".save": "ذخیره پیام در سیو مسیج",
    ".uptime": "آب‌تایم بات",
    ".fish": "اتوماسیون ماهی خودکار",
    ".stopfish": "توقف اتوماسیون ماهی",
    ".automeo": "ارسال خودکار meo هر ۵ دقیقه",
    ".stopautomeo": "توقف ارسال خودکار meo",
    ".autoreact": "تنظیم ریکشن خودکار",
    ".stopautoreact": "توقف ریکشن خودکار",
    ".readmentions": "سین کردن منشن‌های این چت",
    ".userinfo": "اطلاعات حساب کاربر با ریپلای",
    ".tag": "تگ کردن هوشمند کاربران",
    ".kazino": "اتوماسیون کازینو",
    ".stopkazino": "توقف اتوماسیون کازینو",
    ".stopall": "توقف تمام قابلیت‌های فعال",
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
# .KHOFASH (BAT GAME AUTO-HUNTER - SPAWN DETECTOR)
# ============================================================
khofash_chats = set()

BAT_CODE_MAPPING = {
    1: "✨", 2: "🧄", 3: "👀", 4: "👶", 6: "👾", 7: "🌦️", 8: "💨", 9: "⚫️",
    10: "🕷️", 11: "🧼", 12: "🐥", 13: "💙", 15: "🙍‍♀", 16: "🧽", 17: "🌹",
    18: "🤖", 19: "💥", 20: "🍋", 21: "🎭", 22: "🏔", 23: "🪞", 24: "🃏",
    25: "❤️", 26: "🚒", 27: "🌕", 28: "🧛", 29: "🧊", 30: "😇", 31: "😈",
    33: "🇫🇷", 34: "⭐️", 35: "🌧", 36: "🪙", 37: "⚡️", 38: "🌑"
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

async def process_khofash_spawn(message):
    text = message.raw_text or ""
    
    if message.chat_id in khofash_chats:
        if "خفاش میویی توی گروه پیدا شد" in text:
            code_match = re.search(r"کد\s*:\s*(\d+)", text)
            if code_match:
                bat_code = int(code_match.group(1))
                emoji = BAT_CODE_MAPPING.get(bat_code)
                if emoji:
                    try:
                        await message.reply(emoji)
                    except Exception as err:
                        print("[KHOFASH ERROR]", err)

@client.on(events.NewMessage())
async def khofash_new_message(event):
    await process_khofash_spawn(event.message)

@client.on(events.MessageEdited())
async def khofash_edited_message(event):
    await process_khofash_spawn(event.message)

# ============================================================
# .DELETE & .SAVE & .UPTIME
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

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.save$"))
async def save_message(event):
    if not event.is_reply:
        await event.edit("❌ لطفا روی پیامی که می‌خواهید ذخیره کنید ریپلای بزنید.")
        return
    reply_msg = await event.get_reply_message()
    await client.forward_messages("me", reply_msg)
    await event.edit("✅ پیام در Saved Messages ذخیره شد.")

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
        await event.edit("❌ لطفا زمان را وارد کنید.\nمثال:\n`.fish 11m` یا `.fish 30s`")
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
# .AUTOMEO
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
# .AUTOREACT & .READMENTIONS & .USERINFO
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
        await event.edit("❌ فرمت اشتباه.")
        return

    if event.chat_id not in autoreact_rules:
        autoreact_rules[event.chat_id] = {}

    autoreact_rules[event.chat_id][target.casefold()] = emoji
    await event.edit(f"✅ ریکشن خودکار فعال شد.")

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
                await client(SendReactionRequest(
                    peer=event.chat_id,
                    msg_id=event.id,
                    reaction=[ReactionEmoji(emoticon=emoji)]
                ))
            except Exception as err:
                print("[AUTOREACT ERROR]", err)
            break

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.readmentions$"))
async def read_mentions(event):
    await event.edit("⏳ در حال سین کردن منشن‌های این چت...")
    try:
        await client(functions.messages.ReadMentionsRequest(peer=event.chat_id))
        await event.edit("✅ منشن‌های این چت با موفقیت سین شدند.")
    except Exception as error:
        await event.edit(f"❌ خطا:\n{error}")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.userinfo(?:\s|$)?"))
async def user_info(event):
    target_user = None
    cmd_text = event.raw_text.strip()
    match = re.match(r"^\.userinfo\s+(.+)$", cmd_text)

    try:
        if match:
            target_user = await client.get_entity(match.group(1).strip())
        elif event.is_reply:
            reply_msg = await event.get_reply_message()
            if reply_msg:
                target_user = await client.get_entity(reply_msg.sender_id)
        else:
            target_user = await event.get_sender()

        if not target_user:
            await event.edit("❌ کاربر یافت نشد.")
            return

        name = f"{target_user.first_name or ''} {target_user.last_name or ''}".strip()
        username = f"@{target_user.username}" if getattr(target_user, 'username', None) else "ندارد"
        info_text = (f"👤 **مشخصات حساب:**\n\n• نام: `{name}`\n• آیدی: `{target_user.id}`\n"
                     f"• یوزرنیم: {username}\n• ربات؟: {'بله' if target_user.bot else 'خیر'}\n"
                     f"• پرمیوم؟: {'بله' if getattr(target_user, 'premium', False) else 'خیر'}")
        await event.edit(info_text)
    except Exception as error:
        await event.edit(f"❌ خطا:\n{error}")

# ============================================================
# .TAG 
# ============================================================
recent_tagged = {}
@client.on(events.NewMessage(outgoing=True, pattern=r"^\.tag(?:\s+(\d+))?$"))
async def tag_users(event):
    if not event.is_group and not event.is_channel:
        await event.edit("❌ فقط در گروه‌ها.")
        return

    match = event.pattern_match
    requested_count = min(int(match.group(1)) if match.group(1) else 10, 100)
    await event.edit(f"⏳ در حال استخراج...")

    chat_id = event.chat_id
    if chat_id not in recent_tagged:
        recent_tagged[chat_id] = []

    online_pool, recent_pool, other_pool = [], [], []
    seen_ids = set()
    me_id = (await client.get_me()).id

    try:
        async for msg in client.iter_messages(chat_id, limit=300):
            if not msg.sender_id or msg.sender_id in seen_ids or msg.sender_id == me_id:
                continue
            user = msg.sender
            if not user or user.bot or user.deleted: continue
            seen_ids.add(msg.sender_id)
            if msg.sender_id in recent_tagged[chat_id]: continue

            mention = f"@{user.username}" if getattr(user, 'username', None) else f"[{getattr(user, 'first_name', 'دوست')}](tg://user?id={user.id})"
            status = getattr(user, 'status', None)
            
            if isinstance(status, UserStatusOnline): online_pool.append((msg.sender_id, mention))
            elif isinstance(status, UserStatusRecently): recent_pool.append((msg.sender_id, mention))
            else: other_pool.append((msg.sender_id, mention))

        if len(online_pool) + len(recent_pool) + len(other_pool) < requested_count:
            async for user in client.iter_participants(chat_id):
                if not user or user.bot or user.deleted or user.id == me_id or user.id in seen_ids or user.id in recent_tagged[chat_id]:
                    continue
                seen_ids.add(user.id)
                mention = f"@{user.username}" if getattr(user, 'username', None) else f"[{getattr(user, 'first_name', 'دوست')}](tg://user?id={user.id})"
                status = getattr(user, 'status', None)
                if isinstance(status, UserStatusOnline): online_pool.append((user.id, mention))
                elif isinstance(status, UserStatusRecently): recent_pool.append((user.id, mention))
                else: other_pool.append((user.id, mention))

        random.shuffle(online_pool)
        random.shuffle(recent_pool)
        random.shuffle(other_pool)

        full_pool = online_pool + recent_pool + other_pool
        selected_pairs = full_pool[:requested_count]
        users_to_tag = [item[1] for item in selected_pairs]

        if not users_to_tag:
            recent_tagged[chat_id].clear()
            await event.edit("🔄 لیست قبلی پاک شد، مجدداً ارسال کنید.")
            return

        for uid, _ in selected_pairs: recent_tagged[chat_id].append(uid)
        if len(recent_tagged[chat_id]) > 150: recent_tagged[chat_id] = recent_tagged[chat_id][-150:]

        for i in range(0, len(users_to_tag), 5):
            await client.send_message(chat_id, "👥 **دوستان:**\n" + " ".join(users_to_tag[i:i + 5]))
            await asyncio.sleep(1.5)
        await event.delete()
    except Exception as error:
        await event.edit(f"❌ خطا:\n{error}")

# ============================================================
# .KAZINO & .STOPALL & .STATUS & .PING
# ============================================================
kazino_active_chats = set()

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.kazino(?:\s+(.+))?$"))
async def start_kazino(event):
    emoji = event.pattern_match.group(1).strip() if event.pattern_match.group(1) else "🎰"
    winning_value = {"🎰": 64, "🎲": 6, "🎯": 6, "🎳": 6, "🏀": 5, "⚽": 5}.get(emoji, 6)

    if not event.is_reply:
        try: await event.delete()
        except: pass
        return

    reply_msg = await event.get_reply_message()
    chat_id = event.chat_id
    kazino_active_chats.add(chat_id)
    try: await event.delete()
    except: pass

    try:
        while chat_id in kazino_active_chats:
            sent_msg = await client.send_message(chat_id, file=InputMediaDice(emoticon=emoji), reply_to=reply_msg.id)
            dice_value = sent_msg.media.value if sent_msg.media and hasattr(sent_msg.media, 'value') else None
            if dice_value == winning_value:
                kazino_active_chats.discard(chat_id)
                break
            try: await sent_msg.delete()
            except: pass
            await asyncio.sleep(0.02)
    except Exception as error:
        kazino_active_chats.discard(chat_id)

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopkazino$"))
async def stop_kazino(event):
    kazino_active_chats.discard(event.chat_id)
    try: await event.delete()
    except: pass

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.stopall$"))
async def stop_all_features(event):
    global fish_task_running
    if fish_task_running: fish_task_running.cancel(); fish_task_running = None
    for task in automeo_tasks.values(): task.cancel()
    automeo_tasks.clear(); reply_rules.clear(); cat_chats.clear(); autoreact_rules.clear(); kazino_active_chats.clear(); khofash_chats.clear()
    await event.edit("🛑 **تمام قابلیت‌های تنظیمی متوقف شدند!**")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.status$"))
async def bot_status_report(event):
    report = ["📊 **گزارش وضعیت سلف‌بات:**\n"]
    report.append(f"🐱 **حالت .cat:** {'فعال' if cat_chats else 'غیرفعال'}")
    report.append(f"🦇 **شکارچی خفاش:** {'فعال' if khofash_chats else 'غیرفعال'}")
    report.append(f"🎣 **اتوماسیون .fish:** {'فعال' if fish_task_running else 'غیرفعال'}")
    report.append(f"🐱 **ارسال .automeo:** {'فعال' if automeo_tasks else 'غیرفعال'}")
    report.append(f"🤖 **پاسخ .reply:** {'فعال' if reply_rules else 'غیرفعال'}")
    report.append(f"❤️ **ریکشن .autoreact:** {'فعال' if autoreact_rules else 'غیرفعال'}")
    report.append(f"🎰 **اتوماسیون .kazino:** {'فعال' if kazino_active_chats else 'غیرفعال'}")
    await event.edit("\n".join(report))

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
async def ping(event): await event.edit("✅ Userbot is online.")

@client.on(events.NewMessage(outgoing=True, pattern=r"^\.whoami$"))
async def whoami(event):
    me = await client.get_me()
    await event.edit(f"Name: {me.first_name or ''}\nUsername: @{me.username or 'none'}\nID: {me.id}")

# ============================================================
# MAIN
# ============================================================
async def main():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    start_web_server()
    print("======================================\nTelegram Userbot starting...\n======================================")
    await authenticate()
    me = await client.get_me()
    print(f"✅ USERBOT CONNECTED\nName: {me.first_name or ''}\n======================================")
    await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Userbot stopped.")
