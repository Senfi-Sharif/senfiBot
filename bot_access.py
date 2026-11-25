from bot_shared import *

class AccessManagement:
    def is_admin_user(self, user_id: int) -> bool:
        """Check if user is an authorized admin"""
        admin_ids = [
            Config.ROLE_USERS['ROLE_SECRETARY_USER_ID'],
            Config.ROLE_USERS['ROLE_DEPUTY_SECRETARY_USER_ID'],
            Config.ROLE_USERS['ROLE_ORGANIZATION_USER_ID'],
            Config.ROLE_USERS['ROLE_EDUCATION_USER_ID'],
            Config.ROLE_USERS['ROLE_LEGAL_USER_ID'],
            Config.ROLE_USERS['ROLE_WOMEN_USER_ID'],
            Config.ROLE_USERS['ROLE_NUTRITION_USER_ID'],
            Config.ROLE_USERS['ROLE_HEALTH_USER_ID'],
            Config.ROLE_USERS['ROLE_FACILITIES_USER_ID'],
            Config.ROLE_USERS['ROLE_DORMITORY_USER_ID'],
            Config.ROLE_USERS['ROLE_PUBLIC_RELATIONS_USER_ID'],
            Config.ADMIN_USER_ID
        ]
        return str(user_id) in admin_ids

    def check_rate_limit(self, user_id: int) -> bool:
        """Check if user has exceeded rate limits"""
        now = datetime.now()

        # Check 10-minute message limit
        if user_id not in self.user_message_counts:
            self.user_message_counts[user_id] = {}

        # Remove old entries (older than 10 minutes)
        ten_minutes_ago = now.timestamp() - 600  # 10 minutes in seconds
        self.user_message_counts[user_id] = {
            timestamp: count for timestamp, count in self.user_message_counts[user_id].items()
            if float(timestamp) > ten_minutes_ago
        }

        # Count messages in last 10 minutes
        recent_count = sum(self.user_message_counts[user_id].values())
        if recent_count >= Config.MAX_MESSAGES_PER_10_MINUTES:
            return False

        # Check message frequency (minimum 10 seconds between messages)
        if user_id in self.user_last_message:
            time_diff = now - self.user_last_message[user_id]
            if time_diff.total_seconds() < 10:
                return False

        return True

    def load_message_mappings(self):
        """Load message mappings from database on startup"""
        try:
            conn = sqlite3.connect(self.db.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT telegram_message_id, thread_id FROM message_mappings
                ORDER BY created_at DESC
            ''')
            results = cursor.fetchall()
            conn.close()

            for telegram_message_id, thread_id in results:
                self.message_thread_map[telegram_message_id] = thread_id

            logger.info(f"Loaded {len(results)} message mappings from database")

        except Exception as e:
            logger.error(f"Error loading message mappings: {e}")

    def save_message_mapping(self, telegram_message_id: int, thread_id: int):
        """Save message mapping to database for persistence"""
        try:
            conn = sqlite3.connect(self.db.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO message_mappings (telegram_message_id, thread_id)
                VALUES (?, ?)
            ''', (telegram_message_id, thread_id))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error saving message mapping: {e}")

    def update_rate_limit(self, user_id: int):
        """Update rate limiting counters"""
        now = datetime.now()
        timestamp = str(now.timestamp())

        if user_id not in self.user_message_counts:
            self.user_message_counts[user_id] = {}

        if timestamp not in self.user_message_counts[user_id]:
            self.user_message_counts[user_id][timestamp] = 0

        self.user_message_counts[user_id][timestamp] += 1
        self.user_last_message[user_id] = now

    def refresh_sheet_cache(self) -> Dict[str, List[int]]:
        """Refresh local cache from Google Sheets using SheetCacheManager and detect changes
        Returns dictionary with change information (added, modified, removed, to_restore, to_restrict)"""
        try:
            if not self.sheet:
                logger.warning("Google Sheets not initialized")
                return {'added': [], 'modified': [], 'removed': [], 'to_restore': [], 'to_restrict': []}

            logger.info("🔄 Refreshing sheet cache from Google Sheets...")
            # Get all values from the sheet
            all_values = self.sheet.get_all_values()

            # Update cache using cache manager (will detect changes automatically)
            changes = self.cache_manager.update_cache(all_values)

            # Update legacy cache for backward compatibility
            self.sheet_cache = self.cache_manager.get_cache()
            self.sheet_cache_last_update = datetime.now()

            # Log changes
            if changes['added']:
                logger.info(f"✅ Added {len(changes['added'])} new users: {changes['added'][:5]}..." if len(changes['added']) > 5 else f"✅ Added {len(changes['added'])} new users: {changes['added']}")
            if changes['modified']:
                logger.info(f"✅ Modified {len(changes['modified'])} users: {changes['modified'][:5]}..." if len(changes['modified']) > 5 else f"✅ Modified {len(changes['modified'])} users: {changes['modified']}")
            if changes['removed']:
                logger.info(f"✅ Removed {len(changes['removed'])} users: {changes['removed'][:5]}..." if len(changes['removed']) > 5 else f"✅ Removed {len(changes['removed'])} users: {changes['removed']}")
            if changes['to_restore']:
                logger.info(f"🔓 Need to restore permissions for {len(changes['to_restore'])} users")
            if changes['to_restrict']:
                logger.info(f"🔒 Need to restrict permissions for {len(changes['to_restrict'])} users")

            logger.info(f"✅ Sheet cache refreshed: {len(self.sheet_cache)} users cached")
            return changes

        except Exception as e:
            logger.error(f"Error refreshing sheet cache: {e}")
            return {'added': [], 'modified': [], 'removed': [], 'to_restore': [], 'to_restrict': []}

    def get_all_users_from_sheet(self, use_cache: bool = True) -> dict:
        """Get all users from cache or Google Sheets with their is_valid status
        Returns: dict with 'valid' (list of user_ids with is_valid=1) and 'invalid' (list of user_ids with is_valid=0)
        """
        result = {'valid': [], 'invalid': []}

        # Use cache if available and fresh (less than 1 hour old)
        if use_cache and self.sheet_cache and self.sheet_cache_last_update:
            time_diff = datetime.now() - self.sheet_cache_last_update
            if time_diff.total_seconds() < 3600:  # Cache is less than 1 hour old
                logger.debug("Using cached sheet data")
                for user_id, user_data in self.sheet_cache.items():
                    if user_data.get('is_valid') == "1":
                        result['valid'].append(user_id)
                    else:
                        result['invalid'].append(user_id)
                logger.info(f"Found {len(result['valid'])} valid users and {len(result['invalid'])} invalid users from cache")
                return result

        # Cache is stale or doesn't exist, refresh it
        if self.refresh_sheet_cache():
            for user_id, user_data in self.sheet_cache.items():
                if user_data.get('is_valid') == "1":
                    result['valid'].append(user_id)
                else:
                    result['invalid'].append(user_id)
            logger.info(f"Found {len(result['valid'])} valid users (is_valid=1) and {len(result['invalid'])} invalid users (is_valid=0) in Google Sheets")
        else:
            logger.warning("Failed to refresh cache, returning empty result")

        return result

    def get_valid_users_from_sheet(self) -> list:
        """Get list of user IDs from Google Sheets where is_valid (column F) is 1"""
        all_users = self.get_all_users_from_sheet()
        return all_users['valid']

    async def restore_chat_permissions(self, context: ContextTypes.DEFAULT_TYPE, user_id: int):
        """Restore chat permissions (send messages and media) for a user"""
        if not self.GROUP_ID:
            logger.warning("GROUP_ID not configured, skipping permission restore")
            return False

        try:
            # Create permissions that allow sending text messages, media, stickers, gifs, polls, links, and adding users
            permissions = ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,  # Send polls
                can_send_other_messages=True,  # Send stickers & gifs
                can_add_web_page_previews=True,  # Embed links
                can_invite_users=True,  # Add users
                can_change_info=False,
                can_pin_messages=False
            )

            # Restore permissions using restrict_chat_member
            await context.bot.restrict_chat_member(
                chat_id=self.GROUP_ID,
                user_id=user_id,
                permissions=permissions
            )

            logger.info(f"Restored chat permissions for user {user_id} in group {self.GROUP_ID}")
            # Mark access opened time in sheet (Iran time) so admins can see when access was granted
            try:
                await self.mark_access_opened_in_sheet(context, user_id)
            except Exception as e:
                logger.warning(f"Failed to mark access opened for {user_id} after restoring permissions: {e}")
            return True

        except TelegramError as e:
            # User might not be in the group, or bot doesn't have permission
            if "not enough rights" in str(e).lower() or "chat not found" in str(e).lower():
                logger.warning(f"Bot doesn't have permission to restrict members in group {self.GROUP_ID}: {e}")
            elif "user not found" in str(e).lower() or "not a member" in str(e).lower():
                logger.debug(f"User {user_id} not found or not a member: {e}")
            else:
                logger.debug(f"Error restoring permissions for user {user_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error restoring permissions for user {user_id}: {e}")
            return False

    async def restrict_chat_permissions(self, context: ContextTypes.DEFAULT_TYPE, user_id: int):
        """Restrict chat permissions (close all permissions) for a user"""
        if not self.GROUP_ID:
            logger.warning("GROUP_ID not configured, skipping permission restrict")
            return False

        try:
            # Create permissions that restrict everything
            permissions = ChatPermissions(
                can_send_messages=False,
                can_send_audios=False,
                can_send_documents=False,
                can_send_photos=False,
                can_send_videos=False,
                can_send_video_notes=False,
                can_send_voice_notes=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_invite_users=False,
                can_change_info=False,
                can_pin_messages=False
            )

            # Restrict permissions using restrict_chat_member
            await context.bot.restrict_chat_member(
                chat_id=self.GROUP_ID,
                user_id=user_id,
                permissions=permissions
            )

            logger.info(f"Restricted chat permissions for user {user_id} in group {self.GROUP_ID}")
            return True

        except TelegramError as e:
            # User might not be in the group, or bot doesn't have permission
            if "not enough rights" in str(e).lower() or "chat not found" in str(e).lower():
                logger.warning(f"Bot doesn't have permission to restrict members in group {self.GROUP_ID}: {e}")
            elif "user not found" in str(e).lower() or "not a member" in str(e).lower():
                logger.debug(f"User {user_id} not found or not a member: {e}")
            else:
                logger.debug(f"Error restricting permissions for user {user_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error restricting permissions for user {user_id}: {e}")
            return False

    async def is_user_restricted(self, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
        """Check if user is currently restricted from sending messages in group"""
        if not self.GROUP_ID:
            logger.warning("GROUP_ID not configured, cannot check restriction status")
            return False
        try:
            member = await context.bot.get_chat_member(self.GROUP_ID, user_id)
            status = getattr(member, 'status', None)
            if status == 'restricted':
                # ChatMemberRestricted exposes permission attributes like can_send_messages
                perms = getattr(member, 'can_send_messages', None)
                if perms is not None:
                    return not perms
                return True
            if status in ('left', 'kicked'):
                # Treat non-members as effectively restricted
                return True
            # For normal members/admins/creator -> not restricted
            return False
        except Exception as e:
            logger.warning(f"Could not determine restriction status for {user_id}: {e}")
            return False

    async def mark_access_opened_in_sheet(self, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
        """Write Iran time to column J for a user when access is opened - queued for batch processing"""
        if not self.sheet:
            logger.warning("Google Sheets not initialized, cannot mark access opened")
            return False
        try:
            iran_now = datetime.now(ZoneInfo('Asia/Tehran'))
            ts = iran_now.strftime("%Y-%m-%d %H:%M:%S")

            # Add to queue for batch processing
            async with self.access_update_lock:
                self.access_updates_queue.append((user_id, ts))
                logger.info(f"Queued access update for user {user_id} at {ts} (queue size: {len(self.access_updates_queue)})")

            # Start the batch processor if not already running
            if not self.access_update_task or self.access_update_task.done():
                self.access_update_task = asyncio.create_task(self._process_access_updates_loop())

            return True
        except Exception as e:
            logger.error(f"Error queuing access opened for user {user_id}: {e}")
            return False

    def _setup_logging_forwarder(self, application: Application):
        """Set up a logging.Handler that enqueues all logs and start the sender task"""
        # Store bot for send_to_channel fallback
        self.bot = application.bot

        # Initialize queue and background task
        loop = asyncio.get_event_loop()
        self.log_queue = asyncio.Queue()

        class TelegramQueueHandler(logging.Handler):
            def __init__(self, loop, queue):
                super().__init__()
                self.loop = loop
                self.queue = queue

            def emit(self, record):
                try:
                    # Only send WARNING and above to Telegram to reduce noise
                    if record.levelno >= logging.WARNING:
                        msg = self.format(record)
                        # Enqueue message in loop thread
                        self.loop.call_soon_threadsafe(self.queue.put_nowait, f"[{record.levelname}] {msg}")
                except Exception:
                    pass

        handler = TelegramQueueHandler(loop, self.log_queue)
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)

        # Add handler to root logger
        logging.getLogger().addHandler(handler)

        # Start sender task
        if not self.log_sender_task:
            self.log_sender_task = asyncio.create_task(self._log_sender_loop())

        # Start access update processor
        if not self.access_update_task:
            self.access_update_task = asyncio.create_task(self._process_access_updates_loop())

    async def _log_sender_loop(self):
        """Background loop to batch logs and send them to Telegram channel"""
        if not self.log_queue:
            return
        while True:
            try:
                batch = []
                try:
                    # Wait for first message (with timeout)
                    msg = await asyncio.wait_for(self.log_queue.get(), timeout=self.log_batch_interval)
                    batch.append(msg)
                except asyncio.TimeoutError:
                    pass

                # Drain quickly up to max
                while not self.log_queue.empty() and len(batch) < self.log_batch_max:
                    batch.append(self.log_queue.get_nowait())

                if not batch:
                    continue

                combined = "\n".join(batch)

                # Split large messages into chunks
                for chunk in self._split_message(combined):
                    try:
                        await self.send_to_channel(None, f"<pre>{html.escape(chunk)}</pre>", parse_mode='HTML')
                    except Exception as e:
                        logger.warning(f"Failed to send log chunk to channel: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in log sender loop: {e}")
                await asyncio.sleep(5)

    def _split_message(self, text: str, limit: int = 3800):
        """Yield chunks of text under the given limit"""
        for i in range(0, len(text), limit):
            yield text[i:i+limit]

    async def _process_access_updates_loop(self):
        """Background loop to process access updates in batches"""
        while True:
            try:
                # Process any pending updates every 30 seconds
                await asyncio.sleep(30)
                await self._process_access_updates()
            except asyncio.CancelledError:
                # Process any remaining updates before shutting down
                await self._process_access_updates()
                break
            except Exception as e:
                logger.error(f"Error in access update processing loop: {e}")
                await asyncio.sleep(5)  # Brief pause before retrying

    async def _process_access_updates(self):
        """Process queued access updates in batch to reduce Google Sheets API calls"""
        # Get current queue and clear it
        async with self.access_update_lock:
            if not self.access_updates_queue:
                return

            # Take only a very small batch to avoid API quota limits
            # Process max 3 updates per batch to stay safely within limits
            MAX_BATCH_SIZE = 3
            if len(self.access_updates_queue) > MAX_BATCH_SIZE:
                updates_to_process = self.access_updates_queue[:MAX_BATCH_SIZE]
                # Keep the rest in the queue for next processing cycle
                self.access_updates_queue = self.access_updates_queue[MAX_BATCH_SIZE:]
            else:
                updates_to_process = self.access_updates_queue[:]
                self.access_updates_queue.clear()

        if not updates_to_process:
            return

        try:
            # Get current sheet data once
            all_values = self.sheet.get_all_values()

            # Create a mapping of user_id to row index for quick lookup
            user_to_row = {}
            for idx, row in enumerate(all_values, start=1):
                if row and len(row) > 0:
                    try:
                        user_id = str(row[0]).strip()
                        if user_id:
                            user_to_row[int(user_id)] = idx
                    except (ValueError, IndexError):
                        continue

            # Process each update with delay between calls to avoid rate limits
            success_count = 0
            for i, (user_id, timestamp) in enumerate(updates_to_process):
                if user_id in user_to_row:
                    row_index = user_to_row[user_id]
                    try:
                        # Update the cell in column J (index 10)
                        self.sheet.update_cell(row_index, 10, timestamp)
                        logger.info(f"Batch updated access time for user {user_id} at {timestamp}")
                        success_count += 1
                        # Add delay between API calls to avoid rate limiting
                        # Except for the last item in the batch
                        if i < len(updates_to_process) - 1:
                            await asyncio.sleep(1)  # 1 second delay between writes
                    except Exception as e:
                        logger.error(f"Failed to update access time for user {user_id}: {e}")
                        # Check if it's a quota error and add longer delay if so
                        if "429" in str(e) or "Quota exceeded" in str(e):
                            logger.warning(f"Quota exceeded for user {user_id}, adding extended delay")
                            await asyncio.sleep(5)  # 5 second delay for quota errors
                        # Re-queue the failed update
                        async with self.access_update_lock:
                            self.access_updates_queue.append((user_id, timestamp))
                else:
                    logger.warning(f"User {user_id} not found in sheet for access update")

            if updates_to_process:
                logger.info(f"Processed batch of {len(updates_to_process)} access updates, {success_count} successful")

        except Exception as e:
            logger.error(f"Error processing access updates batch: {e}")
            # Check if it's a quota error and add longer delay if so
            if "429" in str(e) or "Quota exceeded" in str(e):
                logger.warning(f"Quota exceeded in batch processing, adding extended delay before retry")
                await asyncio.sleep(5)  # 5 second delay for quota errors
            # Re-queue all updates on failure
            async with self.access_update_lock:
                # Add to front of queue to retry sooner
                self.access_updates_queue = updates_to_process + self.access_updates_queue

    async def sync_group_members_to_sheet(self, context: ContextTypes.DEFAULT_TYPE):
        """Sync group members to sheet - add members who are in group but not in sheet"""
        logger.info(f"[SYNC] Starting sync - GROUP_ID: {self.GROUP_ID}, sheet available: {self.sheet is not None}")
        if not self.GROUP_ID or not self.sheet:
            logger.warning(f"[SYNC] Cannot sync - GROUP_ID={self.GROUP_ID}, sheet={self.sheet is not None}")
            return 0

        try:
            # Get all current members from the group using Pyrogram
            group_members = []

            if self.pyrogram_client:
                try:
                    # Use Pyrogram to get all members
                    await self.pyrogram_client.start()
                    chat = await self.pyrogram_client.get_chat(self.GROUP_ID)
                    logger.info(f"Fetching members from group: {chat.title}")

                    async for member in self.pyrogram_client.get_chat_members(chat.id):
                        if member.user and not member.user.is_bot:
                            group_members.append({
                                'user_id': member.user.id,
                                'first_name': member.user.first_name or "",
                                'last_name': member.user.last_name or "",
                                'username': member.user.username or ""
                            })

                    await self.pyrogram_client.stop()
                    logger.info(f"Found {len(group_members)} members in group {self.GROUP_ID} using Pyrogram")
                except Exception as e:
                    logger.error(f"Error getting members with Pyrogram: {e}")
                    if self.pyrogram_client.is_connected:
                        await self.pyrogram_client.stop()
            else:
                # Fallback: try to get administrators if Pyrogram is not available
                logger.warning("Pyrogram not available, trying to get administrators as fallback")
                try:
                    admins = await context.bot.get_chat_administrators(self.GROUP_ID)
                    for admin in admins:
                        if admin.user and not admin.user.is_bot:
                            group_members.append({
                                'user_id': admin.user.id,
                                'first_name': admin.user.first_name or "",
                                'last_name': admin.user.last_name or "",
                                'username': admin.user.username or ""
                            })
                    logger.info(f"Found {len(group_members)} administrators (fallback mode)")
                except Exception as e:
                    logger.error(f"Error getting administrators: {e}")
                    return 0

            # Get all users currently in sheet
            all_values = self.sheet.get_all_values()
            sheet_user_ids = set()
            for row in all_values:
                if row and len(row) > 0:
                    try:
                        user_id = str(row[0]).strip()
                        if user_id:
                            sheet_user_ids.add(int(user_id))
                    except (ValueError, IndexError):
                        continue

            # Add members who are in group but not in sheet
            added_count = 0
            logger.info(f"[SYNC] Checking {len(group_members)} group members against {len(sheet_user_ids)} existing sheet users")

            for member in group_members:
                user_id = member['user_id']
                username = member.get('username', '')
                first_name = member.get('first_name', '')

                # Log each member being checked
                logger.debug(f"[SYNC] Checking member: {user_id} (@{username}) - {first_name}")

                if user_id not in sheet_user_ids:
                    try:
                        new_row = [
                            str(user_id),                # Column A: Telegram ID
                            first_name,                  # Column B: First Name
                            member.get('last_name', ''), # Column C: Last Name
                            username,                    # Column D: Username
                            "",                          # Column E: Student Number (empty)
                            "0"                          # Column F: is_valid (default 0)
                        ]
                        self.sheet.append_row(new_row)
                        added_count += 1
                        logger.info(f"✅ [SYNC] Added new member to sheet: {user_id} (@{username}) - {first_name}")
                    except Exception as e:
                        logger.error(f"❌ [SYNC] Error adding member {user_id} (@{username}) to sheet: {e}")
                else:
                    logger.debug(f"[SYNC] Member {user_id} (@{username}) already in sheet, skipping")

            if added_count > 0:
                logger.info(f"✅ [SYNC] Successfully added {added_count} new members to sheet")
                # Refresh cache after adding new members
                self.refresh_sheet_cache()
                try:
                    iran_now = datetime.now(ZoneInfo('Asia/Tehran')).strftime("%Y-%m-%d %H:%M:%S")
                    sample = ', '.join(str(m['user_id']) for m in group_members[:5])
                    msg = f"➕ <b>Added {added_count} members to sheet</b>\n🕒 {iran_now}\n📋 Sample: {sample}"
                    await self.send_to_channel(context, msg, parse_mode='HTML')
                except Exception as e:
                    logger.warning(f"Failed to send sync summary to channel: {e}")
            else:
                logger.info(f"[SYNC] No new members to add (all {len(group_members)} members already in sheet)")

            return added_count

        except TelegramError as e:
            logger.error(f"Error getting group members: {e}")
            return 0
        except Exception as e:
            logger.error(f"Error syncing group members to sheet: {e}")
            return 0
    async def apply_permissions_from_sheet_direct(self, context):
        """
        Apply permissions directly from Google Sheet
        WITHOUT using cache at all
        """

        if not self.sheet or not self.GROUP_ID:
            logger.warning("Sheet or GROUP_ID not available")
            return

        # Get all sheet rows
        rows = self.sheet.get_all_values()

        # Get group members (to avoid restricting non-members)
        group_members = set()
        if self.pyrogram_client:
            try:
                await self.pyrogram_client.start()
                async for m in self.pyrogram_client.get_chat_members(self.GROUP_ID):
                    if m.user and not m.user.is_bot:
                        group_members.add(m.user.id)
                await self.pyrogram_client.stop()
            except Exception:
                if self.pyrogram_client.is_connected:
                    await self.pyrogram_client.stop()

        restore_list = []
        restrict_list = []

        for row in rows:
            if len(row) < 6:
                continue

            try:
                user_id = int(row[0])
                is_valid = str(row[5]).strip()
            except:
                continue

            if group_members and user_id not in group_members:
                continue

            if is_valid == "1":
                restore_list.append(user_id)
            else:
                restrict_list.append(user_id)

        logger.info(f"[FIRST RUN] Restore: {len(restore_list)}, Restrict: {len(restrict_list)}")

        import asyncio
        restored_count = 0
        restricted_count = 0
        for uid in restore_list:
            try:
                success = await self.restore_chat_permissions(context, uid)
                if success:
                    restored_count += 1
                    try:
                        await self.mark_access_opened_in_sheet(context, uid)
                    except Exception as e:
                        logger.warning(f"Failed to mark access opened for {uid} during FIRST RUN: {e}")
            except Exception as e:
                logger.error(f"Error restoring permissions for {uid} during FIRST RUN: {e}")
            await asyncio.sleep(0.15)

        for uid in restrict_list:
            try:
                success = await self.restrict_chat_permissions(context, uid)
                if success:
                    restricted_count += 1
            except Exception as e:
                logger.error(f"Error restricting permissions for {uid} during FIRST RUN: {e}")
            await asyncio.sleep(0.15)

        logger.info(f"[FIRST RUN] Restored: {restored_count}, Restricted: {restricted_count}")
        try:
            iran_now = datetime.now(ZoneInfo('Asia/Tehran')).strftime("%Y-%m-%d %H:%M:%S")
            msg = (f"🆕 <b>FIRST RUN permissions applied</b>\n🕒 {iran_now}\n"
                   f"🔧 Restored: {restored_count}\n🔒 Restricted: {restricted_count}")
            await self.send_to_channel(context, msg, parse_mode='HTML')
        except Exception as e:
            logger.warning(f"Failed to send FIRST RUN summary to channel: {e}")

    async def check_and_restore_chat_permissions(self, context: ContextTypes.DEFAULT_TYPE):
        """Periodic job to check and update chat permissions based on is_valid status
        This runs in background via job queue and doesn't block the bot
        Uses local cache and only applies changes"""
        if self.FIRST_RUN_NO_CACHE:
            logger.info("🆕 FIRST RUN: applying permissions directly from sheet (NO CACHE)")
            await self.apply_permissions_from_sheet_direct(context)
            logger.info("✅ FIRST RUN permission apply finished, building cache...")
            self.refresh_sheet_cache()
            self.FIRST_RUN_NO_CACHE = False
            return

        logger.info("=" * 50)
        logger.info("Starting check_and_restore_chat_permissions job (background - non-blocking)")
        logger.info("=" * 50)

        if not self.GROUP_ID:
            logger.warning("GROUP_ID not configured, skipping permission check")
            return

        try:
            # Step 1: Sync group members to sheet (add members in group but not in sheet)
            logger.info("Step 1: Syncing group members to sheet...")
            added_count = await self.sync_group_members_to_sheet(context)
            if added_count > 0:
                logger.info(f"Added {added_count} new members to sheet")
                # Refresh cache after adding new members
                self.refresh_sheet_cache()

            # Step 2: Refresh cache from Google Sheets and get changes automatically
            logger.info("Step 2: Refreshing sheet cache and detecting changes...")
            # Log current cache age for debugging (may be None if no cache)
            cache_age = self.cache_manager.get_cache_age()
            logger.info(f"Current cache age (seconds): {cache_age}")
            changes = self.refresh_sheet_cache()

            if not changes:
                logger.error("Failed to refresh cache, skipping permission check")
                return

            # Step 3: Check if any changes detected
            if not changes['to_restore'] and not changes['to_restrict']:
                logger.info("✅ No permission changes detected, all permissions are up to date")
                return

            logger.info(f"Found {len(changes['to_restore'])} users to restore, {len(changes['to_restrict'])} users to restrict")

            # Send summary to channel about job start
            try:
                cache_age = self.cache_manager.get_cache_age()
                iran_now = datetime.now(ZoneInfo('Asia/Tehran')).strftime("%Y-%m-%d %H:%M:%S")
                message = (f"🔄 <b>Permissions job started</b>\n"
                           f"🕒 {iran_now}\n"
                           f"🔍 Found: {len(changes['to_restore'])} to restore, {len(changes['to_restrict'])} to restrict\n"
                           f"📋 Cache age (s): {cache_age}")
                await self.send_to_channel(context, message, parse_mode='HTML')
            except Exception as e:
                logger.warning(f"Failed to post job-start to channel: {e}")

            # Step 4: Get group members to filter (only process users in group)
            group_member_ids = set()
            if self.pyrogram_client:
                try:
                    await self.pyrogram_client.start()
                    chat = await self.pyrogram_client.get_chat(self.GROUP_ID)
                    async for member in self.pyrogram_client.get_chat_members(chat.id):
                        if member.user and not member.user.is_bot:
                            group_member_ids.add(member.user.id)
                    await self.pyrogram_client.stop()
                    logger.info(f"Got {len(group_member_ids)} group members for filtering")
                except Exception as e:
                    logger.warning(f"Could not get group members with Pyrogram: {e}")
                    if self.pyrogram_client.is_connected:
                        await self.pyrogram_client.stop()

            # Filter changes to only users in the group
            to_restore = [uid for uid in changes['to_restore'] if not group_member_ids or uid in group_member_ids]
            to_restrict = [uid for uid in changes['to_restrict'] if not group_member_ids or uid in group_member_ids]

            if not to_restore and not to_restrict:
                logger.info("No changes for users in the group")
                return

            # Step 5: Apply changes
            logger.info("Step 4: Applying permission changes...")
            restored_count = 0
            restricted_count = 0
            error_count = 0

            import asyncio
            # Restore permissions for users with is_valid=1
            for user_id in to_restore:
                try:
                    success = await self.restore_chat_permissions(context, user_id)
                    if success:
                        restored_count += 1
                    await asyncio.sleep(0.2)  # Rate limiting
                except Exception as e:
                    logger.error(f"Error restoring permissions for user {user_id}: {e}")
                    error_count += 1

            # Restrict permissions for users with is_valid=0
            for user_id in to_restrict:
                try:
                    success = await self.restrict_chat_permissions(context, user_id)
                    if success:
                        restricted_count += 1
                    await asyncio.sleep(0.2)  # Rate limiting
                except Exception as e:
                    logger.error(f"Error restricting permissions for user {user_id}: {e}")
                    error_count += 1

            if restored_count > 0 or restricted_count > 0:
                logger.info(f"✅ Successfully updated permissions: {restored_count} restored, {restricted_count} restricted")
                try:
                    iran_now = datetime.now(ZoneInfo('Asia/Tehran')).strftime("%Y-%m-%d %H:%M:%S")
                    summary = (f"✅ <b>Permissions job finished</b>\n🕒 {iran_now}\n"
                               f"🔧 Restored: {restored_count}\n🔒 Restricted: {restricted_count}")
                    await self.send_to_channel(context, summary, parse_mode='HTML')
                except Exception as e:
                    logger.warning(f"Failed to send permissions summary to channel: {e}")

        except Exception as e:
            logger.error(f"Error in check_and_restore_chat_permissions: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _find_permission_changes(self, old_cache: Dict[int, Dict[str, str]], new_cache: Dict[int, Dict[str, str]]) -> dict:
        """Find changes in is_valid status between old and new cache
        Returns: {'to_restore': [user_ids], 'to_restrict': [user_ids]}"""
        changes = {'to_restore': [], 'to_restrict': []}

        # Check users in new cache
        for user_id, user_data in new_cache.items():
            new_is_valid = user_data.get('is_valid', '0')

            if user_id in old_cache:
                old_is_valid = old_cache[user_id].get('is_valid', '0')
                # Status changed
                if old_is_valid != new_is_valid:
                    if new_is_valid == "1":
                        changes['to_restore'].append(user_id)
                    else:
                        changes['to_restrict'].append(user_id)
            else:
                # New user
                if new_is_valid == "1":
                    changes['to_restore'].append(user_id)
                else:
                    changes['to_restrict'].append(user_id)

        # Check users removed from cache (shouldn't happen often, but handle it)
        for user_id in old_cache:
            if user_id not in new_cache:
                # User removed from sheet - restrict them
                changes['to_restrict'].append(user_id)

        return changes

    def is_user_in_sheet(self, user_id: int) -> bool:
        """Check if a user ID exists in Google Sheet (Column A) - uses cache manager"""
        try:
            # Use cache manager first (most efficient)
            if self.cache_manager.is_user_in_cache(user_id):
                return True

            # Fallback to legacy cache if available
            if self.sheet_cache:
                return user_id in self.sheet_cache

            # Cache not available, check directly (will be slow but works)
            if not self.sheet:
                logger.warning("Google Sheets not initialized")
                return False

            # Get all values from the sheet
            all_values = self.sheet.get_all_values()

            # Check if user_id exists in Column A (index 0)
            for row in all_values:
                if row and len(row) > 0:
                    try:
                        if str(row[0]).strip() == str(user_id):
                            return True
                    except (ValueError, IndexError):
                        continue

            return False

        except Exception as e:
            logger.error(f"Error checking user in sheet: {e}")
            return False

    async def add_user_to_sheet(self, user_id: int, first_name: str = "", last_name: str = "", username: str = ""):
        """Add a user to Google Sheet"""
        try:
            if not self.sheet:
                logger.warning("Google Sheets not initialized")
                return False

            # Check if user already exists
            if self.is_user_in_sheet(user_id):
                logger.info(f"User {user_id} already exists in sheet")
                return True

            # Add new row
            new_row = [
                str(user_id),      # Column A: Telegram ID
                first_name,        # Column B: First Name
                last_name,         # Column C: Last Name
                username,          # Column D: Username
                "",                # Column E: Student Number (empty)
                "0"                # Column F: is_valid (default 0 - restricted)
            ]
            self.sheet.append_row(new_row)
            logger.info(f"Added user {user_id} to sheet: {first_name} {last_name} (@{username})")

            # Immediately refresh cache to include the new user
            # This ensures the cache is always in sync with the sheet
            try:
                self.refresh_sheet_cache()
                logger.info(f"✅ Cache refreshed after adding user {user_id}")
            except Exception as e:
                logger.warning(f"Failed to refresh cache after adding user: {e}")
                # Fallback: manually update cache
                self.sheet_cache[user_id] = {
                    'first_name': first_name,
                    'last_name': last_name,
                    'username': username,
                    'student_number': '',
                    'is_valid': '0'
                }

            return True

        except Exception as e:
            logger.error(f"Error adding user to sheet: {e}")
            return False
    async def ensure_user_is_restricted(self, context, user_id):
        try:
            await self.restrict_chat_permissions(context, user_id)
            logger.info(f"🔒 Ensured restriction for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to ensure restriction: {e}")


