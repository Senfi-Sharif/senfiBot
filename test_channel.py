#!/usr/bin/env python3
import os
import asyncio
from dotenv import load_dotenv
from telegram import Bot

load_dotenv()

async def test_channel():
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    channel_id = int(os.getenv('CHANNEL_ID'))
    
    print(f"Bot Token: {bot_token[:10]}...")
    print(f"Channel ID: {channel_id}")
    
    bot = Bot(token=bot_token)
    
    try:
        # Test sending a message to the channel
        message = await bot.send_message(
            chat_id=channel_id,
            text="🧪 تست کانال لاگ - این پیام برای تست ارسال شده است"
        )
        print(f"✅ پیام با موفقیت ارسال شد! Message ID: {message.message_id}")
        
    except Exception as e:
        print(f"❌ خطا در ارسال پیام: {e}")
        print(f"نوع خطا: {type(e).__name__}")

if __name__ == "__main__":
    asyncio.run(test_channel())
