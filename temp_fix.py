#!/usr/bin/env python3

# Read the bot file
with open('enhanced_bot.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the line numbers to replace
start_line = None
end_line = None

for i, line in enumerate(lines):
    if "Check if there's an active thread for this user and role" in line:
        start_line = i
    elif "return CHOOSING_ROLE" in line and start_line is not None and i > start_line + 10:
        end_line = i
        break

if start_line is not None and end_line is not None:
    # Replace the section
    new_section = '''            # Check if there's an active thread for this user and role
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
            return WAITING_FOR_MESSAGE
'''
    
    # Replace the lines
    lines[start_line:end_line+1] = [new_section + '\n']
    
    # Write back to file
    with open('enhanced_bot.py', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    
    print(f"Replaced lines {start_line+1} to {end_line+1}")
else:
    print("Could not find the section to replace")
