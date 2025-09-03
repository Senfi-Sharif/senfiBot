#!/usr/bin/env python3
import re

# Read the bot file
with open('enhanced_bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the specific problematic send_message call
old_pattern = r'sent_msg = await context\.bot\.send_message\(\s*chat_id=admin_user_id,\s*text=admin_message,\s*parse_mode=ParseMode\.MARKDOWN,\s*reply_markup=admin_reply_markup\s*\)'

new_replacement = '''sent_msg = await self.safe_send_message(
                context,
                admin_user_id,
                admin_message,
                reply_markup=admin_reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )'''

# Apply the replacement
content = re.sub(old_pattern, new_replacement, content, flags=re.MULTILINE | re.DOTALL)

# Write the fixed content
with open('enhanced_bot.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed the parsing error in enhanced_bot.py!")
