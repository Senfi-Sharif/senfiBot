from bot_shared import *

class ConversationHandlers:
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user

        # Add user to database
        self.db.add_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name
        )

        # Show role selection menu
        await self.show_role_menu(update, context)
        return CHOOSING_ROLE

    async def show_role_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show the role selection menu"""
        roles = self.db.get_roles()

        keyboard = []
        for role in roles:
            keyboard.append([InlineKeyboardButton(
                role['role_name'],
                callback_data=f"role_{role['role_id']}"
            )])

        keyboard.append([InlineKeyboardButton("👥 گروه شورای صنفی", url="https://t.me/shora_sharif")])
        keyboard.append([InlineKeyboardButton("🆔 شناسه من", callback_data="get_user_id")])
        keyboard.append([InlineKeyboardButton("🎓 شماره دانشجویی", callback_data="student_number")])
        keyboard.append([InlineKeyboardButton("❓ راهنما", callback_data="help")])

        # Add block list button only for admins and role users
        if self.is_admin_user(update.effective_user.id):
            keyboard.append([InlineKeyboardButton("📋 لیست کاربران بلاک شده", callback_data="blocks_main_menu")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        welcome_text = """
🤖 **بات شورای صنفی دانشجویی**

به بات شورای صنفی خوش آمدید!

لطفاً مسئول مورد نظر خود را انتخاب کنید تا بتوانید با ایشان ارتباط برقرار کنید.

**نکات مهم:**
• تمام پیام‌ها با اطلاعات شما ارسال می‌شوند
• شناسه و اطلاعات شما برای مسئول ارسال می‌شود
• هر گفتگو در یک ترد جداگانه ذخیره می‌شود
• می‌توانید در هر زمان مسئول را تغییر دهید
        """

        # Always send message with inline keyboard
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text=welcome_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            # Send new message with inline keyboard
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=welcome_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )

    async def handle_role_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle role selection from inline keyboard"""
        query = update.callback_query
        await query.answer()

        if query.data == "get_user_id":
            await self.get_user_id(update, context)
            return CHOOSING_ROLE

        elif query.data == "help":
            await self.show_help(update, context)
            return CHOOSING_ROLE

        elif query.data == "student_number":
            await self.request_student_number(update, context)
            return WAITING_FOR_STUDENT_NUMBER

        elif query.data == "back_to_menu":
            await self.show_role_menu(update, context)
            return CHOOSING_ROLE

        elif query.data == "blocks_main_menu":
            # Show block list for main menu (only for admins)
            if not self.is_admin_user(query.from_user.id):
                await query.answer("❌ فقط مسئولین می‌توانند لیست کاربران بلاک شده را مشاهده کنند.")
                return CHOOSING_ROLE

            # Get blocked users for this admin
            blocked_users = self.db.get_blocked_users(query.from_user.id)

            if not blocked_users:
                text = "📋 **لیست کاربران بلاک شده:**\n\n"
                text += "✅ هیچ کاربری بلاک نشده است."
            else:
                text = "📋 **لیست کاربران بلاک شده:**\n\n"
                for i, user in enumerate(blocked_users, 1):
                    text += f"{i}. **شناسه:** `{user['user_id']}`\n"
                    text += f"   **تاریخ بلاک:** {user['blocked_at']}\n"
                    text += f"   **دلیل:** {user['reason']}\n\n"

            # Create back button
            keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            return CHOOSING_ROLE

        elif query.data == "send_message":
            # Create reply keyboard for typing
            reply_keyboard = [
                [KeyboardButton("🏠 منوی اصلی")]
            ]
            reply_markup_keyboard = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)

            # Edit the message to show typing interface
            await query.edit_message_text(
                text="📝 **ارسال پیام**\n\n"
                "💬 **حالا پیام خود را تایپ کنید:**\n\n"
                "برای لغو، روی دکمه «🏠 منوی اصلی» کلیک کنید.",
                parse_mode=ParseMode.MARKDOWN
            )

            # Send a separate message with reply keyboard
            await context.bot.send_message(
                chat_id=query.from_user.id,
                text="⌨️ **پیام خود را ارسال نمایید:**",
                reply_markup=reply_markup_keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
            return WAITING_FOR_MESSAGE



        elif query.data == "back_to_role":
            # Go back to message mode for current role
            user_id = query.from_user.id
            if user_id in self.user_states:
                role = self.user_states[user_id]['selected_role']
                thread_id = self.user_states[user_id].get('thread_id')

                if thread_id:
                    # Create reply keyboard for typing
                    reply_keyboard = [
                        [KeyboardButton("🏠 منوی اصلی")]
                    ]
                    reply_markup_keyboard = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)

                    # Edit the message to show typing interface
                    await query.edit_message_text(
                        text=f"✅ **گفتگو با {role['role_name']}**\n\n"
                        f"🆔 شناسه گفتگو: #{thread_id}\n\n"
                        f"💬 **حالا پیام خود را تایپ کنید:**\n\n"
                        f"برای برگشت به منو، روی دکمه «🏠 منوی اصلی» کلیک کنید.",
                        parse_mode=ParseMode.MARKDOWN
                    )

                    # Send a separate message with reply keyboard
                    await context.bot.send_message(
                        chat_id=query.from_user.id,
                        text="⌨️ **پیام خود را ارسال نمایید:**",
                        reply_markup=reply_markup_keyboard,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    return WAITING_FOR_MESSAGE
                else:
                    await self.show_role_menu(update, context)
                return CHOOSING_ROLE
            else:
                await self.show_role_menu(update, context)
                return CHOOSING_ROLE

        # Handle block buttons for admins
        elif query.data.startswith("block_"):
            # Check if user is admin
            if not self.is_admin_user(query.from_user.id):
                await query.answer("❌ فقط مسئولین می‌توانند کاربران را بلاک کنند.")
                return CHOOSING_ROLE

            # Parse block data: block_user_id_thread_id
            parts = query.data.split("_")
            if len(parts) >= 3:
                blocked_user_id = int(parts[1])
                thread_id = int(parts[2])

                # Block the user
                self.db.block_user(query.from_user.id, blocked_user_id, "بلاک شده توسط مسئول")

                # Create new keyboard with unblock button
                new_keyboard = [
                    [InlineKeyboardButton("🔓 خارج کردن از بلاک", callback_data=f"unblock_{blocked_user_id}_{thread_id}")],
                    [InlineKeyboardButton("📋 لیست کاربران بلاک شده", callback_data=f"blocks_{query.from_user.id}")]
                ]
                new_reply_markup = InlineKeyboardMarkup(new_keyboard)

                # Update the message to show user is blocked with new keyboard
                await query.edit_message_text(
                    text=f"✅ **کاربر بلاک شد!**\n\n"
                    f"🆔 شناسه کاربر: `{blocked_user_id}`\n"
                    f"🆔 شناسه گفتگو: #{thread_id}\n\n"
                    f"کاربر دیگر نمی‌تواند پیام ارسال کند.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=new_reply_markup
                )
            return CHOOSING_ROLE

        elif query.data.startswith("unblock_"):
            # Check if user is admin
            if not self.is_admin_user(query.from_user.id):
                await query.answer("❌ فقط مسئولین می‌توانند کاربران را از بلاک خارج کنند.")
                return CHOOSING_ROLE

            # Parse unblock data: unblock_user_id_thread_id
            parts = query.data.split("_")
            if len(parts) >= 3:
                blocked_user_id = int(parts[1])
                thread_id = int(parts[2])

                # Unblock the user
                self.db.unblock_user(query.from_user.id, blocked_user_id)

                # Create new keyboard with block button
                new_keyboard = [
                    [InlineKeyboardButton("🔒 بلاک کاربر", callback_data=f"block_{blocked_user_id}_{thread_id}")],
                    [InlineKeyboardButton("📋 لیست کاربران بلاک شده", callback_data=f"blocks_{query.from_user.id}")]
                ]
                new_reply_markup = InlineKeyboardMarkup(new_keyboard)

                # Update the message to show user is unblocked with new keyboard
                await query.edit_message_text(
                    text=f"✅ **کاربر از بلاک خارج شد!**\n\n"
                    f"🆔 شناسه کاربر: `{blocked_user_id}`\n"
                    f"🆔 شناسه گفتگو: #{thread_id}\n\n"
                    f"کاربر می‌تواند دوباره پیام ارسال کند.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=new_reply_markup
                )
            return CHOOSING_ROLE

        elif query.data.startswith("blocks_"):
            # Check if user is admin
            if not self.is_admin_user(query.from_user.id):
                await query.answer("❌ فقط مسئولین می‌توانند لیست کاربران بلاک شده را مشاهده کنند.")
                return CHOOSING_ROLE

            # Parse admin user ID
            admin_user_id = int(query.data.split("_")[1])

            # Get blocked users
            blocked_users = self.db.get_blocked_users(admin_user_id)

            if not blocked_users:
                await query.edit_message_text(
                    text="📋 **لیست کاربران بلاک شده**\n\n"
                    "هیچ کاربری بلاک نشده است.",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                text = "📋 **لیست کاربران بلاک شده:**\n\n"
                for i, blocked in enumerate(blocked_users[:10], 1):  # Show first 10
                    text += f"{i}. شناسه: `{blocked['user_id']}`\n"
                    text += f"   تاریخ: {blocked['blocked_at'][:16]}\n"
                    if blocked['reason']:
                        text += f"   دلیل: {blocked['reason']}\n"
                    text += "\n"

                if len(blocked_users) > 10:
                    text += f"\n... و {len(blocked_users) - 10} کاربر دیگر"

                await query.edit_message_text(
                    text=text,
                    parse_mode=ParseMode.MARKDOWN
                )
            return CHOOSING_ROLE

        elif query.data.startswith("role_"):
            role_id = int(query.data.split("_")[1])
            role = self.db.get_role_by_id(role_id)

            if not role:
                await query.edit_message_text("❌ خطا: مسئول مورد نظر یافت نشد.")
                return ConversationHandler.END

            # Check if user is blocked by this specific admin
            user_id = query.from_user.id
            admin_user_id = role['user_id']
            is_blocked = self.db.is_user_blocked(admin_user_id, user_id)

            if is_blocked:
                # User is blocked by this admin - show error message
                await query.edit_message_text(
                    f"❌ **شما توسط {role['role_name']} بلاک شده‌اید.**\n\n"
                    f"نمی‌توانید با این مسئول ارتباط برقرار کنید.\n"
                    f"لطفاً مسئول دیگری انتخاب کنید.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_to_menu")
                    ]])
                )
                return CHOOSING_ROLE

            # Store selected role in user state
            self.user_states[user_id] = {
                'selected_role': role,
                'thread_id': None
            }

            # Check if there's an active thread for this user and role
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
                [KeyboardButton("🏠 منوی اصلی")]
            ]
            reply_markup_keyboard = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)

            # Edit the message to show typing interface
            await query.edit_message_text(
                text=f"✅ **گفتگو با {role['role_name']}**\n\n"
                f"🆔 شناسه گفتگو: #{thread_id}\n\n"
                f"💬 **حالا پیام خود را تایپ کنید:**\n\n"
                f"برای برگشت به منو، روی دکمه «🏠 منوی اصلی» کلیک کنید.",
                parse_mode=ParseMode.MARKDOWN
            )

            # Send a separate message with reply keyboard
            await context.bot.send_message(
                chat_id=query.from_user.id,
                text="⌨️ **پیام خود را ارسال نمایید:**",
                reply_markup=reply_markup_keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
            return WAITING_FOR_MESSAGE


    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle user messages"""
        user_id = update.effective_user.id

        if user_id not in self.user_states:
            back_to_menu_markup = self.create_back_to_menu_button()
            await update.message.reply_text(
                "❌ لطفاً ابتدا مسئول مورد نظر خود را انتخاب کنید.\n"
                "از دستور /start استفاده کنید.",
                reply_markup=back_to_menu_markup
            )
            return CHOOSING_ROLE

        # Check rate limit
        if not self.check_rate_limit(user_id):
            back_to_menu_markup = self.create_back_to_menu_button()
            await update.message.reply_text(
                "⚠️ لطفاً کمی صبر کنید و دوباره تلاش کنید.\n"
                "برای جلوگیری از اسپم، محدودیت زمانی اعمال شده است.",
                reply_markup=back_to_menu_markup
            )
            return WAITING_FOR_MESSAGE

        user_state = self.user_states[user_id]
        role = user_state['selected_role']
        thread_id = user_state.get('thread_id')

        # If no active thread, create one
        if not thread_id:
            thread_id = self.db.create_thread(user_id, role['role_id'])
            self.user_states[user_id]['thread_id'] = thread_id

        message_text = update.message.text

        # Handle reply keyboard buttons
        if message_text == "🏠 منوی اصلی":
            # Remove reply keyboard
            remove_keyboard = ReplyKeyboardRemove()
            await context.bot.send_message(
                chat_id=user_id,
                text="🏠 بازگشت به منوی اصلی",
                reply_markup=remove_keyboard
            )
            # Go back to main menu
            await self.show_role_menu(update, context)
            return CHOOSING_ROLE

        # Store user message
        self.db.add_message(
            thread_id=thread_id,
            telegram_message_id=update.message.message_id,
            sender_type='user',
            message_text=message_text
        )

        # Send notification to admin
        admin_user_id = role['user_id']

        # Check if user is blocked by this specific admin
        is_blocked = self.db.is_user_blocked(admin_user_id, update.effective_user.id)

        if is_blocked:
            # User is blocked by this admin - don't send message to admin
            await update.message.reply_text(
                f"❌ **شما توسط {role['role_name']} بلاک شده‌اید.**\n\n"
                f"نمی‌توانید به این مسئول پیام ارسال کنید.\n"
                f"لطفاً مسئول دیگری انتخاب کنید.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.create_back_to_menu_button()
            )
            return WAITING_FOR_MESSAGE

        # Create admin notification message
        username = update.effective_user.username
        username_display = f"@{username}" if username else "بدون نام کاربری"

        admin_message = f"""
📨 **پیام جدید از دانشجو**

👤 **اطلاعات دانشجو:**
🆔 شناسه: `{update.effective_user.id}`
👤 نام: {update.effective_user.first_name or 'بدون نام'}
📝 نام کاربری: {username_display}

💬 **پیام:**
{message_text}

🆔 **شناسه گفتگو:** #{thread_id}

---
برای پاسخ، روی این پیام ریپلای کنید.
        """

        # Create admin keyboard with block button (user is not blocked)
        admin_keyboard = [
            [InlineKeyboardButton("🔒 بلاک کاربر", callback_data=f"block_{update.effective_user.id}_{thread_id}")]
        ]
        admin_reply_markup = InlineKeyboardMarkup(admin_keyboard)

        # Send to channel for logging
        channel_message = f"""
📨 **پیام جدید در کانال لاگ**

👤 **دانشجو:** {update.effective_user.id} | {username_display} | {update.effective_user.first_name}
💬 **پیام:** {message_text}
🆔 **گفتگو:** #{thread_id}
👨‍💼 **مسئول:** {role['role_name']}
        """
        await self.send_to_channel(context, channel_message)

        try:
            sent_msg = await self.safe_send_message(
                context,
                admin_user_id,
                admin_message,
                reply_markup=admin_reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )

            # Store the mapping between role message and thread
            self.message_thread_map[sent_msg.message_id] = thread_id

            # Add role message to database
            self.db.add_message(
                thread_id=thread_id,
                telegram_message_id=sent_msg.message_id,
                sender_type='admin',
                message_text=f"پیام کاربر (Thread #{thread_id}): {message_text}"
            )

            # Save the mapping to database for persistence
            self.save_message_mapping(sent_msg.message_id, thread_id)

            # Confirm to user - just show success message without menu
            await update.message.reply_text(
                f"✅ **پیام شما ارسال شد!**\n\n"
                f"مسئول: {role['role_name']}\n"
                f"🆔 شناسه گفتگو: #{thread_id}\n\n"
                f"پاسخ مسئول به شما ارسال خواهد شد.\n"
                f"💬 **می‌توانید پیام بعدی خود را تایپ کنید.**",
                parse_mode=ParseMode.MARKDOWN
            )

            # Update rate limit
            self.update_rate_limit(user_id)

        except Exception as e:
            logger.error(f"Error sending message to admin: {e}")
            back_to_menu_markup = self.create_back_to_menu_button()
            await update.message.reply_text(
                "❌ خطا در ارسال پیام. لطفاً دوباره تلاش کنید.",
                reply_markup=back_to_menu_markup
            )

        return WAITING_FOR_MESSAGE

    async def handle_commands(self, update: Update, context: ContextTypes.DEFAULT_TYPE, command: str):
        """Handle user commands"""
        user_id = update.effective_user.id
        user_state = self.user_states[user_id]

        if command == '/history':
            # Show thread history
            thread_id = user_state.get('thread_id')
            if thread_id:
                messages = self.db.get_thread_messages(thread_id)
                if messages:
                    text = f"📋 **تاریخچه گفتگو #{thread_id}:**\n\n"
                    for msg in messages[-10:]:  # Show last 10 messages
                        sender = "👤 شما" if msg['sender_type'] == 'user' else "👨‍💼 مسئول"
                        text += f"{sender}:\n{msg['message_text']}\n\n"

                    back_to_menu_markup = self.create_back_to_menu_button()
                    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_to_menu_markup)
                else:
                    back_to_menu_markup = self.create_back_to_menu_button()
                    await update.message.reply_text("📋 هنوز پیامی در این گفتگو وجود ندارد.", reply_markup=back_to_menu_markup)
            else:
                back_to_menu_markup = self.create_back_to_menu_button()
                await update.message.reply_text("❌ گفتگوی فعالی یافت نشد.", reply_markup=back_to_menu_markup)

        elif command == '/back':
            # Return to role selection
            await self.show_role_menu(update, context)
            return CHOOSING_ROLE

    def create_back_to_menu_button(self) -> InlineKeyboardMarkup:
        """Create a back to main menu button"""
        keyboard = [[InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_to_menu")]]
        return InlineKeyboardMarkup(keyboard)

    async def handle_admin_reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle replies to admin messages (both from admins and regular users) - only for private chats"""
        logger.info(f"handle_admin_reply called for user {update.effective_user.id}")

        # Only process private chat replies (group messages are handled by handle_group_message)
        if update.message and update.message.chat:
            if update.message.chat.type != "private":
                return  # Not a private chat, skip

        if not update.message.reply_to_message:
            logger.info("No reply_to_message found")
            return  # Not a reply

        user_id = update.effective_user.id
        is_admin = self.is_admin_user(user_id)

        # Log the reply attempt
        logger.info(f"Reply to admin message from user {user_id} (admin: {is_admin})")

        # Log admin action for security
        logger.info(f"Admin reply from user {user_id} for message {update.message.reply_to_message.message_id}")

        # Get the original message that was replied to
        original_message_id = update.message.reply_to_message.message_id
        admin_message = update.message.text

        # Debug: Log all message mappings
        logger.info(f"Message thread map contents: {self.message_thread_map}")
        logger.info(f"Looking for message ID: {original_message_id}")
        logger.info(f"Admin message: {admin_message}")
        logger.info(f"Reply message text: {update.message.text}")
        logger.info(f"Reply message ID: {update.message.message_id}")
        logger.info(f"Original message ID: {original_message_id}")

        # Find the thread for this message - try both direct mapping and database lookup
        thread_id = self.message_thread_map.get(original_message_id)

        # If not found in memory, try to find it in the database
        if not thread_id:
            try:
                conn = sqlite3.connect(self.db.db_path)
                cursor = conn.cursor()

                # Try multiple approaches to find the thread
                # 1. First try the message_mappings table
                cursor.execute('''
                    SELECT thread_id FROM message_mappings
                    WHERE telegram_message_id = ?
                ''', (original_message_id,))
                result = cursor.fetchone()

                # 2. If not found, try the messages table for admin messages
                if not result:
                    cursor.execute('''
                        SELECT thread_id FROM messages
                        WHERE telegram_message_id = ? AND sender_type = 'admin'
                    ''', (original_message_id,))
                    result = cursor.fetchone()

                # 3. If still not found, try the messages table for user messages
                if not result:
                    cursor.execute('''
                        SELECT thread_id FROM messages
                        WHERE telegram_message_id = ? AND sender_type = 'user'
                    ''', (original_message_id,))
                    result = cursor.fetchone()

                # 4. If still not found, try to find by message text pattern (for admin notifications)
                if not result:
                    cursor.execute('''
                        SELECT thread_id FROM messages
                        WHERE sender_type = 'admin' AND message_text LIKE ?
                        ORDER BY message_id DESC LIMIT 1
                    ''', (f'%Thread #{original_message_id}%',))
                    result = cursor.fetchone()

                # 5. Last resort: try to find any recent message in the same chat
                if not result:
                    # For regular users, search by their user_id in threads
                    # For admins, search more broadly
                    if not self.is_admin_user(user_id):
                        cursor.execute('''
                            SELECT thread_id FROM messages
                            WHERE telegram_message_id IN (
                                SELECT telegram_message_id FROM messages
                                WHERE thread_id IN (
                                    SELECT thread_id FROM threads WHERE user_id = ?
                                )
                                ORDER BY message_id DESC LIMIT 10
                            )
                            ORDER BY message_id DESC LIMIT 1
                        ''', (user_id,))
                    else:
                        # For admins, search more broadly across all threads they might be involved in
                        cursor.execute('''
                            SELECT thread_id FROM messages
                            WHERE telegram_message_id IN (
                                SELECT telegram_message_id FROM messages
                                ORDER BY message_id DESC LIMIT 20
                            )
                            ORDER BY message_id DESC LIMIT 1
                        ''')
                    result = cursor.fetchone()

                conn.close()

                if result:
                    thread_id = result[0]
                    # Add to memory mapping for future use
                    self.message_thread_map[original_message_id] = thread_id
                    logger.info(f"Found thread {thread_id} for message {original_message_id} in database")
                else:
                    logger.warning(f"No thread found for message {original_message_id} in database")
                    logger.warning(f"Available message IDs: {list(self.message_thread_map.keys())}")
                    await update.message.reply_text("❌ پیام مورد نظر یافت نشد. لطفاً روی پیام اصلی ریپلای کنید.")
                    return
            except Exception as e:
                logger.error(f"Error looking up message in database: {e}")
                await update.message.reply_text("❌ خطا در یافتن پیام. لطفاً دوباره تلاش کنید.")
                return

        # Get thread information
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, role_id FROM threads WHERE thread_id = ?', (thread_id,))
        result = cursor.fetchone()
        conn.close()

        if not result:
            logger.error(f"Thread {thread_id} not found in database")
            return

        student_user_id = result[0]
        role_id = result[1]
        reply_message = update.message.text

        # Get role information
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT role_name, user_id FROM roles WHERE role_id = ?', (role_id,))
        role_result = cursor.fetchone()
        conn.close()

        role_name = role_result[0] if role_result else "مسئول"
        admin_user_id = role_result[1] if role_result else None

        # Debug logging
        logger.info(f"Reply - Thread ID: {thread_id}, Student User ID: {student_user_id}, Reply User ID: {user_id}, Is Admin: {is_admin}")
        logger.info(f"Reply message text: {reply_message}")

        # Handle different scenarios - check if replying user is actually part of this thread
        admin_user_id_int = int(admin_user_id) if admin_user_id else None

        # Determine the role of the replying user in this specific thread
        if user_id == student_user_id:
            # Student is replying - send to admin
            is_student_reply = True
            target_user_id = admin_user_id_int
            sender_name = "دانشجو"
            logger.info(f"Student reply - Will send reply to admin chat_id: {admin_user_id_int}")

            # Check if user is blocked by admin
            if admin_user_id_int and self.db.is_user_blocked(admin_user_id_int, user_id):
                await update.message.reply_text("❌ شما توسط این مسئول بلاک شده‌اید.")
                return
        elif user_id == admin_user_id_int:
            # Admin is replying - send to student
            is_student_reply = False
            target_user_id = student_user_id
            sender_name = role_name
            logger.info(f"Admin reply - Will send reply to student chat_id: {student_user_id}")

            # Check if user is blocked
            if self.db.is_user_blocked(user_id, student_user_id):
                await update.message.reply_text("❌ این کاربر توسط شما بلاک شده است.")
                return
        else:
            # User is not part of this thread
            logger.warning(f"User {user_id} is not part of thread {thread_id} (student: {student_user_id}, admin: {admin_user_id_int})")
            await update.message.reply_text("❌ شما مجاز به پاسخ در این گفتگو نیستید.")
            return

        # Handle admin commands (only for admin replies)
        if not is_student_reply and reply_message.startswith('/block'):
            # Block the user
            reason = reply_message[7:].strip() if len(reply_message) > 7 else None
            self.db.block_user(user_id, student_user_id, reason)
            await update.message.reply_text(f"✅ کاربر بلاک شد.\nدلیل: {reason or 'بدون دلیل'}")
            return

        if not is_student_reply and reply_message.startswith('/unblock'):
            # Unblock the user
            self.db.unblock_user(user_id, student_user_id)
            await update.message.reply_text("✅ کاربر از بلاک خارج شد.")
            return

        if not is_student_reply and reply_message.startswith('/blocks'):
            # List blocked users
            blocked_users = self.db.get_blocked_users(user_id)
            if not blocked_users:
                await update.message.reply_text("📋 هیچ کاربری بلاک نشده است.")
                return

            text = "📋 **کاربران بلاک شده:**\n\n"
            for i, blocked in enumerate(blocked_users[:10], 1):  # Show first 10
                text += f"{i}. شناسه: `{blocked['user_id']}`\n"
                text += f"   تاریخ: {blocked['blocked_at'][:16]}\n"
                if blocked['reason']:
                    text += f"   دلیل: {blocked['reason']}\n"
                text += "\n"

            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            return

        # Add message to database
        sender_type = 'admin' if is_admin else 'user'
        self.db.add_message(
            thread_id=thread_id,
            telegram_message_id=update.message.message_id,
            sender_type=sender_type,
            message_text=reply_message
        )

        # Send reply
        try:
            if not is_student_reply:
                # Admin sending reply to student
                reply_text = f"""
💬 **پاسخ از {role_name}**

🆔 **شناسه گفتگو:** #{thread_id}

{reply_message}

---
برای پاسخ، پیام خود را ارسال کنید.
                """
            else:
                # Student sending reply to admin
                reply_text = f"""
💬 **پاسخ از دانشجو**

🆔 **شناسه گفتگو:** #{thread_id}

{reply_message}

---
برای پاسخ، روی این پیام ریپلای کنید.
                """

            # Find the corresponding message in the target chat to reply to
            # We need to find the message in the target user's chat that corresponds to this thread
            conn = sqlite3.connect(self.db.db_path)
            cursor = conn.cursor()

            if not is_student_reply:
                # Admin replying to student - find the student's last message in this thread
                cursor.execute('''
                    SELECT telegram_message_id FROM messages
                    WHERE thread_id = ? AND sender_type = 'user'
                    ORDER BY message_id DESC LIMIT 1
                ''', (thread_id,))
            else:
                # Student replying to admin - find the admin's last message in this thread
                cursor.execute('''
                    SELECT telegram_message_id FROM messages
                    WHERE thread_id = ? AND sender_type = 'admin'
                    ORDER BY message_id DESC LIMIT 1
                ''', (thread_id,))

            msg_result = cursor.fetchone()
            conn.close()

            # Get student information for channel logging
            conn = sqlite3.connect(self.db.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT t.user_id, u.username, u.first_name
                FROM threads t
                LEFT JOIN users u ON t.user_id = u.user_id
                WHERE t.thread_id = ?
            ''', (thread_id,))
            student_result = cursor.fetchone()
            conn.close()

            student_user_id = student_result[0] if student_result else "Unknown"
            student_username = student_result[1] if student_result and student_result[1] else "بدون نام کاربری"
            student_name = student_result[2] if student_result and student_result[2] else "نامشخص"

            # Format student info with both ID and username
            student_display = f"ID: {student_user_id} | @{student_username} | {student_name}"

            # Send reply to channel for logging with detailed student info
            channel_reply_message = f"""
💬 <b>پاسخ</b>

🆔 <b>شناسه گفتگو:</b> #{thread_id}
👤 <b>از:</b> {sender_name}
📝 <b>به:</b> {'دانشجو' if not is_student_reply else role_name}
📝 <b>پیام:</b> {reply_message}
👤 <b>دانشجو:</b> {student_display}
            """
            await self.send_to_channel(context, channel_reply_message)

            # Send reply
            logger.info(f"Sending reply to {target_user_id} with text: {reply_text[:100]}...")

            try:
                sent_message = None
                if msg_result:
                    if is_admin:
                        # Admin sending reply to student - use back to menu button
                        back_to_menu_markup = self.create_back_to_menu_button()
                        sent_message = await self.safe_send_message(
                            context,
                            target_user_id,
                            reply_text,
                            reply_to_message_id=msg_result[0],
                            reply_markup=back_to_menu_markup,
                            parse_mode=ParseMode.MARKDOWN
                        )
                    else:
                        # Student sending reply to admin - add block buttons
                        is_blocked = self.db.is_user_blocked(admin_user_id, student_user_id)

                        if is_blocked:
                            # User is blocked - show unblock button
                            admin_keyboard = [
                                [InlineKeyboardButton("🔓 خارج کردن از بلاک", callback_data=f"unblock_{student_user_id}_{thread_id}")]
                            ]
                        else:
                            # User is not blocked - show block button
                            admin_keyboard = [
                                [InlineKeyboardButton("🔒 بلاک کاربر", callback_data=f"block_{student_user_id}_{thread_id}")]
                            ]

                        reply_markup = InlineKeyboardMarkup(admin_keyboard)
                        sent_message = await self.safe_send_message(
                            context,
                            target_user_id,
                            reply_text,
                            reply_to_message_id=msg_result[0],
                            reply_markup=reply_markup,
                            parse_mode=ParseMode.MARKDOWN
                        )
                else:
                    if is_admin:
                        # Admin sending reply to student - use back to menu button
                        back_to_menu_markup = self.create_back_to_menu_button()
                        sent_message = await self.safe_send_message(
                            context,
                            target_user_id,
                            reply_text,
                            reply_markup=back_to_menu_markup,
                            parse_mode=ParseMode.MARKDOWN
                        )
                    else:
                        # Student sending reply to admin - add block buttons
                        is_blocked = self.db.is_user_blocked(admin_user_id, student_user_id)

                        if is_blocked:
                            # User is blocked - show unblock button
                            admin_keyboard = [
                                [InlineKeyboardButton("🔓 خارج کردن از بلاک", callback_data=f"unblock_{student_user_id}_{thread_id}")]
                            ]
                        else:
                            # User is not blocked - show block button
                            admin_keyboard = [
                                [InlineKeyboardButton("🔒 بلاک کاربر", callback_data=f"block_{student_user_id}_{thread_id}")]
                            ]

                        reply_markup = InlineKeyboardMarkup(admin_keyboard)
                        sent_message = await self.safe_send_message(
                            context,
                            target_user_id,
                            reply_text,
                            reply_markup=reply_markup,
                            parse_mode=ParseMode.MARKDOWN
                        )

                # Save message mapping for future replies
                if sent_message:
                    self.save_message_mapping(sent_message.message_id, thread_id)
                    logger.info(f"Saved message mapping: {sent_message.message_id} -> {thread_id}")

                logger.info(f"Reply successfully forwarded to {target_user_id} for thread {thread_id}")

                # Send confirmation to sender
                if is_admin:
                    await update.message.reply_text(f"✅ پاسخ شما به دانشجو ارسال شد.\n🆔 شناسه گفتگو: #{thread_id}")
                else:
                    await update.message.reply_text(f"✅ پاسخ شما به {sender_name} ارسال شد.\n🆔 شناسه گفتگو: #{thread_id}")

            except Exception as send_error:
                logger.error(f"Error sending message to {target_user_id}: {send_error}")
                if is_admin:
                    await update.message.reply_text(f"❌ خطا در ارسال پاسخ به دانشجو: {str(send_error)}")
                else:
                    await update.message.reply_text(f"❌ خطا در ارسال پاسخ به {sender_name}: {str(send_error)}")

        except Exception as e:
            logger.error(f"Error forwarding reply: {e}")
            # Send error message to sender
            await update.message.reply_text(f"❌ خطا در ارسال پاسخ: {str(e)}")

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel current operation and return to main menu"""
        user_id = update.effective_user.id

        # Clear user state
        if user_id in self.user_states:
            del self.user_states[user_id]

        # Return to main menu
        await self.show_role_menu(update, context)

        await update.message.reply_text("🔙 بازگشت به منوی اصلی")

        return CHOOSING_ROLE

    async def get_user_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Get user ID for configuration purposes"""
        user = update.effective_user

        # Simple text without Markdown to avoid parsing issues
        user_info_text = f"👤 اطلاعات کاربر:\n\n🆔 شناسه: {user.id}\n👤 نام: {user.first_name or 'بدون نام'}\n📝 نام کاربری: @{user.username or 'بدون نام کاربری'}"

        # Create back to menu button
        back_to_menu_markup = self.create_back_to_menu_button()

        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                text=user_info_text,
                reply_markup=back_to_menu_markup
            )
        else:
            await update.message.reply_text(
                text=user_info_text,
                reply_markup=back_to_menu_markup
            )

    async def test_admin_reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Test admin reply functionality"""
        user = update.effective_user

        if not self.is_admin_user(user.id):
            await update.message.reply_text("❌ این دستور فقط برای ادمین‌ها قابل استفاده است.")
            return

        # Show current message mappings
        mapping_info = f"📊 **اطلاعات نگاشت پیام‌ها:**\n\n"
        mapping_info += f"تعداد نگاشت‌های موجود: {len(self.message_thread_map)}\n\n"

        if self.message_thread_map:
            mapping_info += "**نگاشت‌های موجود:**\n"
            for msg_id, thread_id in list(self.message_thread_map.items())[:5]:  # Show first 5
                mapping_info += f"• پیام {msg_id} → ترد {thread_id}\n"
        else:
            mapping_info += "هیچ نگاشتی موجود نیست."

        # Also show recent threads
        try:
            conn = sqlite3.connect(self.db.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT t.thread_id, t.user_id, r.role_name, t.created_at,
                       (SELECT COUNT(*) FROM messages m WHERE m.thread_id = t.thread_id) as message_count
                FROM threads t
                JOIN roles r ON t.role_id = r.role_id
                ORDER BY t.last_activity DESC
                LIMIT 3
            ''')
            threads = cursor.fetchall()
            conn.close()

            if threads:
                mapping_info += "\n\n**آخرین گفتگوها:**\n"
                for thread_id, user_id, role_name, created_at, msg_count in threads:
                    mapping_info += f"• ترد #{thread_id} - {role_name} (پیام‌ها: {msg_count})\n"
        except Exception as e:
            mapping_info += f"\n\nخطا در دریافت گفتگوها: {e}"

        back_to_menu_markup = self.create_back_to_menu_button()
        await update.message.reply_text(mapping_info, parse_mode=ParseMode.MARKDOWN, reply_markup=back_to_menu_markup)

    async def debug_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Debug information for troubleshooting"""
        user = update.effective_user

        debug_info = f"🔧 **اطلاعات دیباگ:**\n\n"
        debug_info += f"👤 کاربر: {user.id}\n"
        debug_info += f"🔑 ادمین: {self.is_admin_user(user.id)}\n"
        debug_info += f"📊 نگاشت‌های پیام: {len(self.message_thread_map)}\n"
        debug_info += f"🕒 زمان: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"

        # Check if this is a reply
        if update.message.reply_to_message:
            debug_info += f"\n📝 **اطلاعات ریپلای:**\n"
            debug_info += f"پیام اصلی: {update.message.reply_to_message.message_id}\n"
            debug_info += f"پیام ریپلای: {update.message.message_id}\n"

            # Check if the original message is in our mapping
            original_id = update.message.reply_to_message.message_id
            thread_id = self.message_thread_map.get(original_id)
            debug_info += f"ترد یافت شده: {thread_id}\n"
        else:
            debug_info += f"\n❌ این پیام ریپلای نیست."

        back_to_menu_markup = self.create_back_to_menu_button()
        await update.message.reply_text(debug_info, parse_mode=ParseMode.MARKDOWN, reply_markup=back_to_menu_markup)

    async def handle_admin_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle admin messages for replying to students"""
        user = update.effective_user

        if not self.is_admin_user(user.id):
            return  # Not an admin, let other handlers deal with it

        message_text = update.message.text

        # Check if this is a reply command
        if message_text.startswith('/reply '):
            try:
                # Format: /reply <thread_id> <message>
                parts = message_text.split(' ', 2)
                if len(parts) >= 3:
                    thread_id = int(parts[1])
                    reply_text = parts[2]

                    # Get thread information
                    conn = sqlite3.connect(self.db.db_path)
                    cursor = conn.cursor()
                    cursor.execute('SELECT user_id FROM threads WHERE thread_id = ?', (thread_id,))
                    result = cursor.fetchone()
                    conn.close()

                    if result:
                        user_id = result[0]

                        # Get role information
                        conn = sqlite3.connect(self.db.db_path)
                        cursor = conn.cursor()
                        cursor.execute('''
                            SELECT r.role_name FROM threads t
                            JOIN roles r ON t.role_id = r.role_id
                            WHERE t.thread_id = ?
                        ''', (thread_id,))
                        role_result = cursor.fetchone()
                        conn.close()

                        role_name = role_result[0] if role_result else "مسئول"

                        formatted_reply = f"""
💬 **پاسخ از {role_name}**

🆔 **شناسه گفتگو:** #{thread_id}

{reply_text}

---
برای پاسخ، پیام خود را ارسال کنید.
                        """

                        # Send reply to student
                        back_to_menu_markup = self.create_back_to_menu_button()
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=formatted_reply,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=back_to_menu_markup
                        )

                        # Add admin message to database
                        self.db.add_message(
                            thread_id=thread_id,
                            telegram_message_id=update.message.message_id,
                            sender_type='admin',
                            message_text=reply_text
                        )

                        # Save message mapping for future replies
                        self.save_message_mapping(update.message.message_id, thread_id)

                        await update.message.reply_text(f"✅ پاسخ شما به دانشجو ارسال شد.\n🆔 شناسه گفتگو: #{thread_id}")
                    else:
                        await update.message.reply_text(f"❌ ترد #{thread_id} یافت نشد.")
                else:
                    await update.message.reply_text("❌ فرمت صحیح: /reply <thread_id> <پیام>")
            except ValueError:
                await update.message.reply_text("❌ شناسه ترد باید عدد باشد.")
            except Exception as e:
                logger.error(f"Error in handle_admin_message: {e}")
                await update.message.reply_text(f"❌ خطا: {str(e)}")

        # Show recent threads for admin
        elif message_text == '/threads':
            try:
                conn = sqlite3.connect(self.db.db_path)
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT t.thread_id, t.user_id, r.role_name, t.created_at,
                           (SELECT COUNT(*) FROM messages m WHERE m.thread_id = t.thread_id) as message_count
                    FROM threads t
                    JOIN roles r ON t.role_id = r.role_id
                    ORDER BY t.last_activity DESC
                    LIMIT 10
                ''')
                threads = cursor.fetchall()
                conn.close()

                if threads:
                    threads_text = "📋 **آخرین گفتگوها:**\n\n"
                    for thread_id, user_id, role_name, created_at, msg_count in threads:
                        threads_text += f"🆔 **#{thread_id}** - {role_name}\n"
                        threads_text += f"👤 کاربر: {user_id}\n"
                        threads_text += f"📝 پیام‌ها: {msg_count}\n"
                        threads_text += f"📅 تاریخ: {created_at[:16]}\n"
                        threads_text += f"💬 پاسخ: `/reply {thread_id} پیام شما`\n\n"

                    await update.message.reply_text(threads_text, parse_mode=ParseMode.MARKDOWN)
                else:
                    await update.message.reply_text("📋 هیچ گفتگویی یافت نشد.")
            except Exception as e:
                logger.error(f"Error showing threads: {e}")
                await update.message.reply_text(f"❌ خطا: {str(e)}")

    async def show_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help information"""
        query = update.callback_query

        help_text = """
❓ **راهنمای استفاده از بات**

**نحوه استفاده:**
1. مسئول مورد نظر خود را انتخاب کنید
2. پیام خود را تایپ کنید
3. پیام شما برای مسئول ارسال می‌شود

**نکات مهم:**
• پیام‌ها ناشناس نیستند
• هر گفتگو در یک ترد جداگانه ذخیره می‌شود
• می‌توانید در هر زمان مسئول را تغییر دهید
        """

        keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text=help_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

    async def request_student_number(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Request student number from user"""
        query = update.callback_query
        await query.answer()

        keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text="🎓 **شماره دانشجویی**\n\n"
                 "شماره دانشجویی‌تون رو با حروف انگلیسی وارد کنید:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

    async def handle_student_number(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle student number input"""
        user = update.effective_user
        user_id = user.id
        student_number_text = update.message.text.strip()

        # Check if user wants to go back to menu
        if student_number_text == "🏠 منوی اصلی":
            remove_keyboard = ReplyKeyboardRemove()
            await context.bot.send_message(
                chat_id=user_id,
                text="🏠 بازگشت به منوی اصلی",
                reply_markup=remove_keyboard
            )
            await self.show_role_menu(update, context)
            return CHOOSING_ROLE

        # Validate student number
        try:
            # Check if it's a number
            student_number = int(student_number_text)

            # Check range: 80000000 to 410000000
            if student_number < 80000000 or student_number > 410000000:
                await update.message.reply_text(
                    "❌ شماره دانشجویی باید یک عدد انگلیسی معتبر باشد.\n"
                "لطفاً دوباره تلاش کنید:"
                )
                return WAITING_FOR_STUDENT_NUMBER

        except ValueError:
            await update.message.reply_text(
                "❌ شماره دانشجویی باید یک عدد انگلیسی معتبر باشد.\n"
                "لطفاً دوباره تلاش کنید:"
            )
            return WAITING_FOR_STUDENT_NUMBER

        # Update Google Sheets
        try:
            if not self.sheet:
                await update.message.reply_text(
                    "❌ خطا در اتصال به Google Sheets. لطفاً بعداً تلاش کنید."
                )
                await self.show_role_menu(update, context)
                return CHOOSING_ROLE

            # Get all values from the sheet
            all_values = self.sheet.get_all_values()

            # Find user by Telegram ID (column A)
            user_found = False
            row_index = None

            for idx, row in enumerate(all_values, start=1):
                if row and len(row) > 0:
                    try:
                        # Column A is the Telegram ID
                        if str(row[0]) == str(user_id):
                            user_found = True
                            row_index = idx
                            break
                    except (ValueError, IndexError):
                        continue

            # Prepare user data
            first_name = user.first_name or ""
            last_name = user.last_name or ""
            username = user.username or ""

            if user_found and row_index:
                # Update existing row - update column E (index 4)
                # Ensure row has at least 5 columns
                current_row = all_values[row_index - 1]
                while len(current_row) < 5:
                    current_row.append("")

                # Update column E (student number)
                self.sheet.update_cell(row_index, 5, str(student_number))

                # Also update other columns if they're empty
                if len(current_row) > 1 and not current_row[1]:
                    self.sheet.update_cell(row_index, 2, first_name)
                if len(current_row) > 2 and not current_row[2]:
                    self.sheet.update_cell(row_index, 3, last_name)
                if len(current_row) > 3 and not current_row[3]:
                    self.sheet.update_cell(row_index, 4, username)

                await update.message.reply_text(
                    f"✅ شماره دانشجویی شما با موفقیت به‌روزرسانی شد!\n\n"
                    f"🎓 شماره دانشجویی: {student_number}"
                )
            else:
                # Create new row
                new_row = [
                    str(user_id),      # Column A: Telegram ID
                    first_name,        # Column B: First Name
                    last_name,         # Column C: Last Name
                    username,          # Column D: Username
                    str(student_number)  # Column E: Student Number
                ]
                self.sheet.append_row(new_row)

                await update.message.reply_text(
                    f"✅ شماره دانشجویی شما با موفقیت ثبت شد!\n\n"
                    f"🎓 شماره دانشجویی: {student_number}"
                )

            # Return to main menu
            await self.show_role_menu(update, context)
            return CHOOSING_ROLE

        except Exception as e:
            logger.error(f"Error updating Google Sheets: {e}")
            await update.message.reply_text(
                f"❌ خطا در به‌روزرسانی اطلاعات: {str(e)}\n"
                "لطفاً بعداً تلاش کنید."
            )
            await self.show_role_menu(update, context)
            return CHOOSING_ROLE

