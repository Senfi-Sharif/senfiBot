#!/usr/bin/env python3
import re

# Read the bot file
with open('enhanced_bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix 1: Replace direct send_message calls with safe_send_message for admin messages
# This fixes the "Can't parse entities" error
content = re.sub(
    r'sent_msg = await context\.bot\.send_message\(\s*chat_id=admin_user_id,\s*text=admin_message,\s*parse_mode=ParseMode\.MARKDOWN,\s*reply_markup=admin_reply_markup\s*\)',
    'sent_msg = await self.safe_send_message(\n                context,\n                admin_user_id,\n                admin_message,\n                reply_markup=admin_reply_markup,\n                parse_mode=ParseMode.MARKDOWN\n            )',
    content
)

# Fix 2: Replace other send_message calls that use ParseMode.MARKDOWN in reply handling
# Pattern for send_message with parse_mode=ParseMode.MARKDOWN
pattern1 = r'sent_message = await context\.bot\.send_message\(\s*chat_id=target_user_id,\s*text=reply_text,\s*reply_to_message_id=msg_result\[0\],\s*parse_mode=ParseMode\.MARKDOWN,\s*reply_markup=back_to_menu_markup\s*\)'
replacement1 = '''sent_message = await self.safe_send_message(
                            context,
                            target_user_id,
                            reply_text,
                            reply_to_message_id=msg_result[0],
                            reply_markup=back_to_menu_markup,
                            parse_mode=ParseMode.MARKDOWN
                        )'''
content = re.sub(pattern1, replacement1, content)

pattern2 = r'sent_message = await context\.bot\.send_message\(\s*chat_id=target_user_id,\s*text=reply_text,\s*reply_to_message_id=msg_result\[0\],\s*parse_mode=ParseMode\.MARKDOWN,\s*reply_markup=reply_markup\s*\)'
replacement2 = '''sent_message = await self.safe_send_message(
                            context,
                            target_user_id,
                            reply_text,
                            reply_to_message_id=msg_result[0],
                            reply_markup=reply_markup,
                            parse_mode=ParseMode.MARKDOWN
                        )'''
content = re.sub(pattern2, replacement2, content)

pattern3 = r'sent_message = await context\.bot\.send_message\(\s*chat_id=target_user_id,\s*text=reply_text,\s*parse_mode=ParseMode\.MARKDOWN,\s*reply_markup=back_to_menu_markup\s*\)'
replacement3 = '''sent_message = await self.safe_send_message(
                            context,
                            target_user_id,
                            reply_text,
                            reply_markup=back_to_menu_markup,
                            parse_mode=ParseMode.MARKDOWN
                        )'''
content = re.sub(pattern3, replacement3, content)

pattern4 = r'sent_message = await context\.bot\.send_message\(\s*chat_id=target_user_id,\s*text=reply_text,\s*parse_mode=ParseMode\.MARKDOWN,\s*reply_markup=reply_markup\s*\)'
replacement4 = '''sent_message = await self.safe_send_message(
                            context,
                            target_user_id,
                            reply_text,
                            reply_markup=reply_markup,
                            parse_mode=ParseMode.MARKDOWN
                        )'''
content = re.sub(pattern4, replacement4, content)

# Write the fixed content
with open('enhanced_bot.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Comprehensive fix applied to enhanced_bot.py!")
