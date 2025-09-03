#!/usr/bin/env python3
import re

# Read the bot file
with open('enhanced_bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the channel ID validation logic
# The current condition incorrectly checks if CHANNEL_ID > 0, but channel IDs are negative
old_condition = r'if not hasattr\(self, \'CHANNEL_ID\'\) or not self\.CHANNEL_ID or self\.CHANNEL_ID == -1001234567890 or \(isinstance\(self\.CHANNEL_ID, int\) and self\.CHANNEL_ID > 0\):'

new_condition = r'if not hasattr(self, \'CHANNEL_ID\') or not self.CHANNEL_ID or self.CHANNEL_ID == -1001234567890:'

# Apply the replacement
content = re.sub(old_condition, new_condition, content)

# Write the fixed content
with open('enhanced_bot.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed channel ID validation logic!")
