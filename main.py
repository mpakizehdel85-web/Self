import asyncio
import os
from pathlib import Path
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

# خواندن اطلاعات از Environment رندر
API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
PHONE = os.environ.get("TELEGRAM_PHONE")
PASSWORD_2FA = os.environ.get("TELEGRAM_TELEGRAM_PASSWORD_2FA", "")
CODE = os.environ.get("TELEGRAM_CODE", "").strip()

SESSION_PATH = Path("userbot")

def register_events(cli):
    @cli.on(events.NewMessage(outgoing=True, pattern=r"^\.ping$"))
    async def ping(event):
        await event.edit("✅ Userbot is online and working!")

async def main():
    if not API_ID or not API_HASH or not PHONE:
        print("[ERROR] لطفاً متغیرهای اصلی را در رندر چک کنید.")
        return

    client = TelegramClient(str(SESSION_PATH), int(API_ID), API_HASH, auto_reconnect=True)
    await client.connect()

    if not await client.is_user_authorized():
        # اگر هنوز کدی وارد نشده است، درخواست کد بده
        if not CODE:
            print("[INFO] در حال ارسال درخواست کد به تلگرام...")
            await client.send_code_request(PHONE)
            print("\n-------------------------------------------------------------")
            print("کد تایید تلگرام ارسال شد!")
            print("حالا به بخش Environment رندر برو، متغیر TELEGRAM_CODE را بساز")
            print("و کد دریافتی را داخل آن قرار بده و Save کن.")
            print("-------------------------------------------------------------\n")
            return
        else:
            print("[INFO] کد از متغیر محیطی خوانده شد، در حال ورود...")
            try:
                await client.sign_in(phone=PHONE, code=CODE)
            except SessionPasswordNeededError:
                print("[INFO] اعمال رمز دومرحله‌ای...")
                await client.sign_in(password=PASSWORD_2FA)
            except Exception as e:
                print(f"[ERROR] خطا در ورود یا انقضای کد: {e}")
                return

    print("[SUCCESS] یوزربات با موفقیت روشن و آنلاین شد!")
    register_events(client)
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
