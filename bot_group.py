from bot_shared import *

class GroupHandlers:
    async def handle_group_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle messages in the group - check if user is in Google Sheet (handles all message types including replies, media, etc.)
        This handler silently deletes messages from unauthorized users and restricts their access - no replies or messages sent."""
        logger.info("=" * 60)
        logger.info("🔍 handle_group_message CALLED")
        logger.info("=" * 60)

        # Only process messages from the configured group
        if not update.message or not update.message.chat:
            logger.warning("❌ handle_group_message: No message or chat")
            return

        chat_id = update.message.chat.id
        chat_type = update.message.chat.type
        logger.info(f" Chat ID: {chat_id}, Type: {chat_type}, Expected GROUP_ID: {self.GROUP_ID}")

        # Compare both as int and as string to handle different formats
        if chat_id != self.GROUP_ID and str(chat_id) != str(self.GROUP_ID):
            logger.warning(f"❌ handle_group_message: Chat ID mismatch - got {chat_id} (type: {type(chat_id)}), expected {self.GROUP_ID} (type: {type(self.GROUP_ID)})")
            return  # Not our target group

        logger.info(f"✅ handle_group_message: Processing message from chat {chat_id}")

        user = update.effective_user
        # Anonymous admins and sender-chat messages have no effective user ID.
        if user is None:
            logger.info("Skipping group message without an effective user")
            return

        # Skip bot messages
        if user.is_bot:
            logger.info("⏭️ handle_group_message: Skipping bot message")
            return

        user_id = user.id
        username = user.username or "no_username"
        first_name = user.first_name or "no_name"

        logger.info(f"👤 User: {user_id} (@{username}) - {first_name}")
        logger.info(f"📝 Message text: {update.message.text or 'No text (media/sticker/etc)'}")

        # Check if user is in Google Sheet
        logger.info(f"🔍 Checking if user {user_id} is in sheet...")
        is_in_sheet = self.is_user_in_sheet(user_id)
        logger.info(f"📊 User {user_id} in sheet: {is_in_sheet}")

        # Determine is_valid status (prefer cache for speed)
        is_valid = None
        try:
            if is_in_sheet:
                if self.cache_manager.is_user_in_cache(user_id):
                    is_valid = self.cache_manager.is_user_valid(user_id)
                    logger.info(f"📊 Cached is_valid for {user_id}: {is_valid}")
                elif self.sheet_cache and user_id in self.sheet_cache:
                    is_valid = self.sheet_cache[user_id].get('is_valid') == '1'
                    logger.info(f"📊 Legacy cache is_valid for {user_id}: {is_valid}")
                else:
                    # As a fallback, try a direct sheet lookup (cheap for single user)
                    try:
                        if self.sheet:
                            all_vals = self.sheet.get_all_values()
                            for r in all_vals:
                                if r and len(r) > 0 and str(r[0]).strip() == str(user_id):
                                    is_valid = str(r[5]).strip() == '1' if len(r) > 5 else False
                                    break
                    except Exception as e:
                        logger.warning(f"Error doing direct sheet lookup for {user_id}: {e}")
        except Exception as e:
            logger.warning(f"Error determining is_valid for {user_id}: {e}")

        # If user not present in sheet OR present but not marked valid -> delete and restrict
        if not is_in_sheet or (is_in_sheet and not is_valid):
            logger.warning(f"⚠️ User {user_id} (@{username}) not allowed (in_sheet={is_in_sheet}, is_valid={is_valid}), deleting message and restricting access")

            try:
                # Delete the message silently (no error message to user)
                logger.info(f"🗑️ Attempting to delete message from user {user_id}...")
                await update.message.delete()
                logger.info(f"✅ Deleted message from user {user_id}")

                # Restrict user permissions silently
                logger.info(f"🔒 Attempting to restrict permissions for user {user_id}...")
                await self.restrict_chat_permissions(context, user_id)
                logger.info(f"✅ Restricted permissions for user {user_id}")

                # Add user to sheet silently if they were not present
                if not is_in_sheet:
                    first_name = user.first_name or ""
                    last_name = user.last_name or ""
                    username = user.username or ""
                    logger.info(f"➕ Attempting to add user {user_id} to sheet...")
                    success = await self.add_user_to_sheet(user_id, first_name, last_name, username)
                    if success:
                        logger.info(f"✅ Added user {user_id} (@{username}) to sheet")
                    else:
                        logger.error(f"❌ Failed to add user {user_id} to sheet")

                # Return immediately to prevent any other handlers from processing this message
                # No messages will be sent to the group
                logger.info("=" * 60)
                logger.info("✅ handle_group_message COMPLETED - message deleted, user restricted, added to sheet (if needed)")
                logger.info("=" * 60)
                return

            except TelegramError as e:
                logger.error(f"❌ TelegramError handling unauthorized user {user_id}: {e}")
                import traceback
                logger.error(traceback.format_exc())
                # Still return to prevent other handlers - no error messages sent
                return
            except Exception as e:
                logger.error(f"❌ Unexpected error handling unauthorized user {user_id}: {e}")
                import traceback
                logger.error(traceback.format_exc())
                # Still return to prevent other handlers - no error messages sent
                return
        else:
            logger.info(f"✅ User {user_id} is in sheet and valid, allowing message")
            logger.info("=" * 60)

    async def manual_restore_permissions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manual command to check and update chat permissions based on is_valid status (admin only)"""
        user = update.effective_user

        if not self.is_admin_user(user.id):
            await update.message.reply_text("❌ این دستور فقط برای ادمین‌ها قابل استفاده است.")
            return

        await update.message.reply_text("🔄 در حال بررسی همه کاربران و به‌روزرسانی دسترسی‌ها...")

        if not self.GROUP_ID:
            await update.message.reply_text("❌ GROUP_ID تنظیم نشده است.")
            return

        try:
            # Step 1: Sync group members to sheet
            await update.message.reply_text("📋 مرحله 1: همگام‌سازی اعضای گروه با شیت...")
            added_count = await self.sync_group_members_to_sheet(context)
            if added_count > 0:
                await update.message.reply_text(f"✅ {added_count} عضو جدید به شیت اضافه شد.")

            # Step 2: Get all users from sheet
            await update.message.reply_text("📋 مرحله 2: دریافت کاربران از شیت...")
            all_users = self.get_all_users_from_sheet()
            valid_users = all_users['valid']
            invalid_users = all_users['invalid']

            # Get group members for filtering
            group_member_ids = set()
            try:
                async for member in context.bot.get_chat_members(self.GROUP_ID):
                    if member.user and not member.user.is_bot:
                        group_member_ids.add(member.user.id)
            except Exception as e:
                logger.warning(f"Could not get group members for filtering: {e}")

            # Filter users to only those in the group
            valid_users_in_group = [uid for uid in valid_users if not group_member_ids or uid in group_member_ids]
            invalid_users_in_group = [uid for uid in invalid_users if not group_member_ids or uid in group_member_ids]

            if not valid_users_in_group and not invalid_users_in_group:
                await update.message.reply_text("✅ هیچ کاربری در شیت که در گروه باشد یافت نشد.")
                return

            await update.message.reply_text(
                f"📋 یافت شد:\n"
                f"✅ کاربران معتبر در گروه (is_valid=1): {len(valid_users_in_group)}\n"
                f"❌ کاربران نامعتبر در گروه (is_valid=0): {len(invalid_users_in_group)}\n\n"
                f"در حال به‌روزرسانی دسترسی‌ها..."
            )

            restored_count = 0
            restricted_count = 0
            error_count = 0

            # Restore permissions for users with is_valid=1 (only if in group)
            for user_id in valid_users_in_group:
                try:
                    success = await self.restore_chat_permissions(context, user_id)
                    if success:
                        restored_count += 1

                    import asyncio
                    await asyncio.sleep(0.2)

                except Exception as e:
                    logger.error(f"Error processing valid user {user_id}: {e}")
                    error_count += 1

            # Restrict permissions for users with is_valid=0 (only if in group)
            for user_id in invalid_users_in_group:
                try:
                    success = await self.restrict_chat_permissions(context, user_id)
                    if success:
                        restricted_count += 1

                    import asyncio
                    await asyncio.sleep(0.2)

                except Exception as e:
                    logger.error(f"Error processing invalid user {user_id}: {e}")
                    error_count += 1

            await update.message.reply_text(
                f"✅ به‌روزرسانی دسترسی‌ها انجام شد.\n\n"
                f"📊 آمار:\n"
                f"✅ دسترسی باز شده: {restored_count} کاربر\n"
                f"❌ دسترسی بسته شده: {restricted_count} کاربر\n"
                f"⚠️ خطا: {error_count}"
            )

        except Exception as e:
            logger.error(f"Error in manual_restore_permissions: {e}")
            await update.message.reply_text(f"❌ خطا: {str(e)}")

