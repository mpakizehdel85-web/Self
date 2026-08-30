import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

SESSION_PATH = Path("userbot")

API_ID = int(os.environ.get("API_ID", "0").strip() or 0)
API_HASH = os.environ.get("API_HASH", "").strip()
PHONE = os.environ.get("PHONE", "").strip()
SESSION_STRING = os.environ.get("SESSION_STRING", "").strip()
PASSWORD_2FA = os.environ.get("PASSWORD_2FA", "").strip()

if not API_ID or not API_HASH or not PHONE:
    raise ValueError("❌ لطفاً متغیرهای API_ID، API_HASH و PHONE را در بخش Environment رندر تنظیم کنید.")


# ============================================================
# EVENT HANDLERS & COMMANDS
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


def register_events(cli):
    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
    async def ping(event):
        await event.edit("✅ Userbot is online.")

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.whoami$"))
    async def whoami(event):
        me = await cli.get_me()
        username = f"@{me.username}" if me.username else "No username"
        await event.edit(f"Name: {me.first_name or ''}\nUsername: {username}\nID: {me.id}")

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.set(?:\s|$)"))
    async def set_scheduled_messages(event):
        match = re.fullmatch(r"\.set\s+(\d+)\s+(.+?)\s+(\d+(?:\.\d+)?[smh]?)", event.raw_text.strip(), re.IGNORECASE)
        if not match:
            await event.edit("❌ فرمت اشتباه.\n\nمثال:\n.set 3 سلام 5m")
            return
        count = int(match.group(1))
        message_text = match.group(2).strip()
        interval_text = match.group(3)
        interval = parse_interval(interval_text)
        if count <= 0 or interval is None or interval <= 0:
            await event.edit("❌ مقدار یا فاصله زمانی نامعتبر است.")
            return

        now = datetime.now(timezone.utc)
        scheduled = 0
        try:
            for index in range(1, count + 1):
                schedule_time = now + timedelta(seconds=interval * index)
                await cli.send_message(event.chat_id, message_text, schedule=schedule_time)
                scheduled += 1
            await event.edit(f"✅ پیام‌های زمان‌بندی‌شده ساخته شدند.\nتعداد: {scheduled}")
        except Exception as error:
            await event.edit(f"❌ خطا: {type(error).__name__}: {error}")

    reply_rules = {}

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.reply(?:\s|$)"))
    async def create_reply(event):
        match = re.fullmatch(r"\.reply\s+(.+?)\s+to\s+(.+)", event.raw_text.strip(), re.IGNORECASE)
        if not match:
            await event.edit("❌ فرمت:\n.reply جواب to متن")
            return
        response = match.group(1).strip()
        trigger = match.group(2).strip()
        if event.chat_id not in reply_rules:
            reply_rules[event.chat_id] = {}
        reply_rules[event.chat_id][trigger.casefold()] = response
        await event.edit(f"✅ ریپلای فعال شد\nهدف: {trigger}\nپاسخ: {response}")

    @cli.on(events.NewMessage())
    async def automatic_reply(event):
        if event.out: return
        chat_id = event.chat_id
        if chat_id not in reply_rules: return
        response = reply_rules[chat_id].get(event.raw_text.strip().casefold())
        if response:
            await event.reply(response)

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.stopreply$"))
    async def stop_reply(event):
        reply_rules.pop(event.chat_id, None)
        await event.edit("🛑 ریپلای خودکار متوقف شد.")

    cat_chats = set()

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.cat$"))
    async def start_cat(event):
        cat_chats.add(event.chat_id)
        await event.edit("🐱 حالت نجات پیشی فعال شد.")

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.stopcat$"))
    async def stop_cat(event):
        cat_chats.discard(event.chat_id)
        await event.edit("🛑 حالت نجات پیشی متوقف شد.")

    async def check_cat_message(message):
        if message.chat_id not in cat_chats or not message.buttons: return
        for row in message.buttons:
            for button in row:
                text = getattr(button, "text", "")
                if text and "نجات پیشی خیابونی" in text:
                    try:
                        await message.click(text=text)
                    except Exception:
                        pass
                    return

    @cli.on(events.NewMessage())
    async def cat_new_message(event):
        await check_cat_message(event.message)

    @cli.on(events.MessageEdited())
    async def cat_edited_message(event):
        await check_cat_message(event.message)


fish_tasks = {}

def register_fish(cli):
    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.fish$"))
    async def start_fish_loop(event):
        chat_id = event.chat_id
        if chat_id in fish_tasks:
            await event.edit("⚠️ اتومیشن ماهی برای این چت قبلاً روشن شده است.")
            return

        await event.edit("🐟 اتومیشن ماهی هر ۳۱ دقیقه فعال شد.")
        
        async def fish_worker():
            try:
                while chat_id in fish_tasks:
                    await cli.send_message(chat_id, "ماهی")
                    await asyncio.sleep(3)
                    
                    async for message in cli.iter_messages(chat_id, limit=2):
                        if message.buttons:
                            for row in message.buttons:
                                for btn in row:
                                    if "بندازش تو یخچال" in getattr(btn, "text", ""):
                                        await btn.click()
                                        break
                    
                    await asyncio.sleep(4)
                    await cli.send_message(chat_id, "یخچال میویی")
                    await asyncio.sleep(4)
                    
                    async for message in cli.iter_messages(chat_id, limit=2):
                        if message.buttons:
                            for row in message.buttons:
                                for btn in row:
                                    btn_text = getattr(btn, "text", "")
                                    if "خام" in btn_text or "بپوخش" in btn_text:
                                        await btn.click()
                                        await asyncio.sleep(1.5)
                    
                    await asyncio.sleep(2)
                    async for message in cli.iter_messages(chat_id, limit=2):
                        if message.buttons:
                            for row in message.buttons:
                                for btn in row:
                                    btn_text = getattr(btn, "text", "")
                                    if "✅" in btn_text or "تایید" in btn_text or "تيك" in btn_text:
                                        await btn.click()
                                        break

                    for _ in range(31 * 60):
                        if chat_id not in fish_tasks:
                            break
                        await asyncio.sleep(1)
            except Exception as error:
                print("[FISH ERROR]", type(error).__name__, str(error))
                fish_tasks.pop(chat_id, None)

        task = asyncio.create_task(fish_worker())
        fish_tasks[chat_id] = task

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.stopfish$"))
    async def stop_fish_loop(event):
        chat_id = event.chat_id
        task = fish_tasks.pop(chat_id, None)
        if task:
            task.cancel()
            await event.edit("🛑 اتومیشن ماهی متوقف شد.")
        else:
            await event.edit("⚠️ اتومیشن ماهی در این چت فعال نیست.")

    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.status$"))
    async def check_all_bot_activities(event):
        chat_id = event.chat_id
        status_lines = ["<b>🤖 گزارش جامع فعالیت‌های ربات:</b>\n"]
        if fish_tasks:
            status_lines.append("<b>🐟 اتومیشن ماهی (.fish):</b> فعال ✅")
        else:
            status_lines.append("<b>🐟 اتومیشن ماهی (.fish):</b> غیرفعال ❌")
        await event.edit("\n".join(status_lines), parse_mode='html')


# ============================================================
# MAIN & TERMINAL LOGIN
# ============================================================

async def main():
    if SESSION_STRING:
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, auto_reconnect=True)
    else:
        client = TelegramClient(str(SESSION_PATH), API_ID, API_HASH, auto_reconnect=True)

    await client.connect()

    if not await client.is_user_authorized():
        print("======================================")
        print("Sending login code to Telegram...")
        print("======================================")
        await client.send_code_request(PHONE)
        
        code = input(">>> Please enter the Telegram login code from your official app: ").strip()
        try:
            await client.sign_in(phone=PHONE, code=code)
        except SessionPasswordNeededError:
            if PASSWORD_2FA:
                password = PASSWORD_2FA
            else:
                password = input(">>> Enter your 2FA Password: ").strip()
            await client.sign_in(password=password)

    print("======================================")
    try:
        print("✅ SUCCESS! Save this SESSION_STRING in Render Environment to never login again:")
        print(client.session.save())
    except:
        pass
    print("======================================")

    register_events(client)
    register_fish(client)

    me = await client.get_me()
    print(f"Logged in as: {me.first_name} (@{me.username or 'none'})")
    
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
