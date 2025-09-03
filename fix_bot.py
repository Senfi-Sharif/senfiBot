#!/usr/bin/env python3
import re

# Read the bot file
with open('enhanced_bot_fixed.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix 1: Replace direct send_message calls with safe_send_message for admin messages
# This fixes the "Can't parse entities" error
content = re.sub(
    r'await context\.bot\.send_message\(\s*chat_id=admin_user_id,\s*text=admin_message,\s*parse_mode=ParseMode\.MARKDOWN,\s*reply_markup=admin_reply_markup\s*\)',
    'await self.safe_send_message(\n                context,\n                admin_user_id,\n                admin_message,\n                reply_markup=admin_reply_markup,\n                parse_mode=ParseMode.MARKDOWN\n            )',
    content
)

# Fix 2: Replace other direct send_message calls that use ParseMode.MARKDOWN
# Pattern for send_message with parse_mode=ParseMode.MARKDOWN
pattern = r'await context\.bot\.send_message\(\s*([^)]*parse_mode=ParseMode\.MARKDOWN[^)]*)\)'
def replace_send_message(match):
    params = match.group(1)
    # Extract parameters
    params_dict = {}
    # Simple parameter extraction (this is a basic implementation)
    if 'chat_id=' in params:
        chat_id = re.search(r'chat_id=([^,)]+)', params).group(1)
        params_dict['chat_id'] = chat_id
    if 'text=' in params:
        text = re.search(r'text=([^,)]+)', params).group(1)
        params_dict['text'] = text
    if 'reply_markup=' in params:
        reply_markup = re.search(r'reply_markup=([^,)]+)', params).group(1)
        params_dict['reply_markup'] = reply_markup
    if 'reply_to_message_id=' in params:
        reply_to_message_id = re.search(r'reply_to_message_id=([^,)]+)', params).group(1)
        params_dict['reply_to_message_id'] = reply_to_message_id
    
    # Build safe_send_message call
    safe_call = 'await self.safe_send_message(\n                context,\n'
    for key, value in params_dict.items():
        safe_call += f'                {key}={value},\n'
    safe_call += '                parse_mode=ParseMode.MARKDOWN\n            )'
    
    return safe_call

# Apply the replacement
content = re.sub(pattern, replace_send_message, content)

# Fix 3: Also fix update.message.reply_text calls with ParseMode.MARKDOWN
content = re.sub(
    r'await update\.message\.reply_text\(\s*([^)]*parse_mode=ParseMode\.MARKDOWN[^)]*)\)',
    lambda m: f'await self.safe_send_message(\n                context,\n                update.effective_user.id,\n                {m.group(1).split(",")[0]},\n                parse_mode=ParseMode.MARKDOWN\n            )',
    content
)

# Write the fixed content
with open('enhanced_bot_fixed.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Bot file fixed!")
