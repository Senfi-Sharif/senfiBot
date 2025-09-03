#!/usr/bin/env python3
import os
import asyncio
from dotenv import load_dotenv
from telegram import Bot

load_dotenv()

async def get_channel_info():
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    channel_id = int(os.getenv('CHANNEL_ID'))
    
    bot = Bot(token=bot_token)
    
    try:
        # Try to get chat information
        chat = await bot.get_chat(chat_id=channel_id)
        print(f"✅ کانال پیدا شد!")
        print(f"نام کانال: {chat.title}")
        print(f"نوع: {chat.type}")
        print(f"شناسه: {chat.id}")
        
        # Try to get chat administrators
        try:
            admins = await bot.get_chat_administrators(chat_id=channel_id)
            print(f"\n👥 ادمین‌های کانال:")
            for admin in admins:
                print(f"  - {admin.user.first_name} (@{admin.user.username}) - {admin.status}")
        except Exception as e:
            print(f"❌ خطا در دریافت لیست ادمین‌ها: {e}")
            
    except Exception as e:
        print(f"❌ خطا در دریافت اطلاعات کانال: {e}")
        print(f"نوع خطا: {type(e).__name__}")
        
        if "Chat not found" in str(e):
            print("\n🔧 راه‌حل‌های ممکن:")
            print("1. مطمئن شوید بات به کانال اضافه شده است")
            print("2. مطمئن شوید شناسه کانال درست است")
            print("3. از @userinfobot برای دریافت شناسه صحیح کانال استفاده کنید")

if __name__ == "__main__":
    asyncio.run(get_channel_info())
