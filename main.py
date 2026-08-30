import asyncio
import os
from pathlib import Path
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
PHONE = os.environ.get("TELEGRAM_PHONE")
PASSWORD_2FA = os.environ.get("TELEGRAM_PASSWORD_2FA", "")

SESSION_PATH = Path("userbot")

async def main():
    if not API_ID or not API_HASH or not PHONE:
        print("[ERROR] لطفاً متغیرها را کامل کن.")
        return

    client = TelegramClient(str(SESSION_PATH), int(API_ID), API_HASH, auto_reconnect=True)
    await client.connect()

    if not await client.is_user_authorized():
        print("[INFO] در حال ارسال درخواست کد...")
        await client.send_code_request(PHONE)
        
        print("\n============================================================")
        print("کد تایید ارسال شد! حالا کد را در متغیر TELEGRAM_CODE بگذار.")
        print("============================================================\n")

        # یک حلقه هوشمند که تا چند دقیقه منتظر می‌ماند تا کد را وارد کنی
        while True:
            code = os.environ.get("TELEGRAM_CODE", "").strip()
            if code:
                try:
                    print("[INFO] کد خوانده شد، در حال ورود...")
                    await client.sign_in(phone=PHONE, code=code)
                    break
                except SessionPasswordNeededError:
                    await client.sign_in(password=PASSWORD_2FA)
                    break
                except Exception as e:
                    print(f"[ERROR] منتظر کد معتبر یا خطای دیگر: {e}")
            await asyncio.sleep(5)

    print("[SUCCESS] ربات با موفقیت آنلاین شد!")
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
    async def ping(event):
        await event.edit("✅ Online!")

    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
