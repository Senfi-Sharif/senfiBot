#!/usr/bin/env python3
import re

# Read the bot file
with open('enhanced_bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the role selection logic to go directly to message mode
# Find the section where role is selected and replace it

# First, replace the active thread section
old_active_thread = '''            # Check if there's an active thread for this user and role
            thread_id = self.db.get_active_thread(user_id, role_id)
            if thread_id:
                self.user_states[user_id]['thread_id'] = thread_id
                
                # Show active thread with inline keyboard
                keyboard = [
                    [InlineKeyboardButton("�� ارسال پیام", callback_data="send_message")],
                    [InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_to_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    text=f"✅ **گفتگوی فعال یافت شد!**\\n\\n"
                    f"مسئول: {role['role_name']}\\n"
                    f"🆔 شناسه گفتگو: #{thread_id}\\n\\n"
                    f"برای ارسال پیام، روی «📝 ارسال پیام» کلیک کنید.",
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                # Show new conversation with inline keyboard
                keyboard = [
                    [InlineKeyboardButton("📝 ارسال پیام", callback_data="send_message")],
                    [InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_to_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    text=f"✅ **مسئول انتخاب شد!**\\n\\n"
                    f"مسئول: {role['role_name']}\\n\\n"
                    f"برای ارسال پیام، روی «📝 ارسال پیام» کلیک کنید.\\n\\n"
                    f"⚠️ توجه: پیام‌ها ناشناس نیستند و اطلاعات شما برای مسئول ارسال می‌شود.",
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            
            return CHOOSING_ROLE'''

new_active_thread = '''            # Check if there's an active thread for this user and role
            thread_id = self.db.get_active_thread(user_id, role_id)
            if thread_id:
                self.user_states[user_id]['thread_id'] = thread_id
            else:
                # Create new thread if none exists
                thread_id = self.db.create_thread(user_id, role_id)
                self.user_states[user_id]['thread_id'] = thread_id
            
            # Go directly to message mode
            # Create reply keyboard for typing
            reply_keyboard = [
                [KeyboardButton("🔙 بازگشت")],
                [KeyboardButton("🏠 منوی اصلی")]
            ]
            reply_markup_keyboard = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
            
            # Edit the message to show typing interface
            await query.edit_message_text(
                text=f"✅ **گفتگو با {role['role_name']}**\\n\\n"
                f"🆔 شناسه گفتگو: #{thread_id}\\n\\n"
                f"💬 **حالا پیام خود را تایپ کنید:**\\n\\n"
                f"برای لغو، روی دکمه «🔙 بازگشت» کلیک کنید.",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Send a separate message with reply keyboard
            await context.bot.send_message(
                chat_id=query.from_user.id,
                text="⌨️ **دکمه‌های زیر را برای ناوبری استفاده کنید:**",
                reply_markup=reply_markup_keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
            return WAITING_FOR_MESSAGE'''

# Apply the replacement
content = re.sub(old_active_thread, new_active_thread, content, flags=re.MULTILINE | re.DOTALL)

# Write the fixed content
with open('enhanced_bot.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Direct message mode applied!")
