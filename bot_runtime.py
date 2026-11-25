from bot_shared import *
from telegram.error import NetworkError

class BotRuntime:
    def run(self):
        """Run the bot"""
        # Try to acquire lock to prevent multiple instances
        if not self.acquire_lock():
            logger.error("Another instance of the bot is already running!")
            logger.error("If you're sure no other instance is running, delete the 'bot.lock' file and try again.")
            sys.exit(1)

        try:
            # Validate configuration
            try:
                Config.validate_config()
            except ValueError as e:
                logger.error(f"Configuration error: {e}")
                return

            # Create application with improved timeout settings
            from telegram.request import HTTPXRequest
            from httpx import Timeout

            # Configure request with longer timeouts for better network handling
            request = HTTPXRequest(
                connection_pool_size=8,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=10
            )
            # PTB uses a separate transport for long-polling getUpdates calls;
            # configuring only `.request()` leaves polling on default timeouts.
            get_updates_request = HTTPXRequest(
                connection_pool_size=1,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=10
            )

            application = (
                Application.builder()
                .token(Config.TELEGRAM_BOT_TOKEN)
                .request(request)
                .get_updates_request(get_updates_request)
                .build()
            )

            # PTB reports polling and handler failures through process_error. Without
            # a registered callback, transient network disconnects are logged as
            # unhandled exceptions even though the updater retries polling.
            async def handle_application_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
                error = context.error
                if isinstance(error, NetworkError):
                    logger.warning("Telegram network error; polling will retry: %s", error)
                    return

                logger.error(
                    "Unhandled Telegram application error",
                    exc_info=(type(error), error, error.__traceback__) if error else None,
                )

            application.add_error_handler(handle_application_error)

            # Add conversation handler
            conv_handler = ConversationHandler(
                entry_points=[CommandHandler('start', self.start)],
                states={
                    CHOOSING_ROLE: [
                        CallbackQueryHandler(self.handle_role_selection),
                        CommandHandler('start', self.start)
                    ],
                    WAITING_FOR_MESSAGE: [
                        CallbackQueryHandler(self.handle_role_selection),
                        MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.REPLY, self.handle_message),
                        CommandHandler('cancel', self.cancel)
                    ],
                    WAITING_FOR_STUDENT_NUMBER: [
                        CallbackQueryHandler(self.handle_role_selection),
                        MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.REPLY, self.handle_student_number),
                        CommandHandler('cancel', self.cancel)
                    ]
                },
                fallbacks=[CommandHandler('cancel', self.cancel)]
            )

            application.add_handler(conv_handler, group=2)

            # Add command handler for getting user ID
            application.add_handler(CommandHandler('myid', self.get_user_id))

            # Add command handler for testing admin reply functionality
            application.add_handler(CommandHandler('testreply', self.test_admin_reply))

            # Add debug command
            application.add_handler(CommandHandler('debug', self.debug_info))

            # Add command to manually check and restore chat permissions (admin only)
            application.add_handler(CommandHandler('restore_permissions', self.manual_restore_permissions))

            # Add handler for admin replies (from any user) - only for private chats, not groups
            application.add_handler(
                MessageHandler(
                    filters.TEXT & filters.REPLY & filters.ChatType.PRIVATE,
                    self.handle_admin_reply
                ),
                group=0  # Highest priority group
            )

            # Add handler for admin commands (alternative way to reply)
            application.add_handler(
                MessageHandler(
                    filters.TEXT & filters.ChatType.PRIVATE,
                    self.handle_admin_message
                ),
                group=1
            )

            # Add handler for block/unblock callbacks
            application.add_handler(
                CallbackQueryHandler(
                    self.handle_role_selection,
                    pattern="^(block_|unblock_|blocks_)"
                ),
                group=0  # High priority
            )

            # Add handler for group messages - check if user is in Google Sheet
            # This handler should process ALL messages in group (including replies, media, etc.) before other handlers
            logger.info("=" * 60)
            logger.info(f"🔧 Registering group message handler")
            logger.info(f"   Current GROUP_ID: {self.GROUP_ID}")
            logger.info("=" * 60)

            # First, add a debug handler to log ALL group messages (this will help us debug)
            async def debug_all_group_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
                if update.message and update.message.chat:
                    logger.info("=" * 60)
                    logger.info("🔍 DEBUG: Received message in group/supergroup")
                    logger.info(f"   Chat ID: {update.message.chat.id}")
                    logger.info(f"   Chat Type: {update.message.chat.type}")
                    logger.info(f"   Chat Title: {update.message.chat.title}")
                    logger.info(f"   User ID: {update.effective_user.id if update.effective_user else 'None'}")
                    logger.info(f"   Username: @{update.effective_user.username if update.effective_user and update.effective_user.username else 'no_username'}")
                    logger.info(f"   Message Text: {update.message.text or 'No text (media/sticker/etc)'}")
                    logger.info(f"   Expected GROUP_ID: {self.GROUP_ID}")
                    logger.info("=" * 60)

            # Add debug handler for all group messages (both group and supergroup)
            application.add_handler(
                MessageHandler(
                    filters.ChatType.GROUP | filters.ChatType.SUPERGROUP,
                    debug_all_group_messages
                ),
                group=-2  # Even higher priority for debugging
            )

            # Add handler for group messages - will check GROUP_ID inside the handler
            # Use a custom filter that accepts all group messages, then check GROUP_ID inside
            async def group_message_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
                # Check if GROUP_ID is resolved, if not try to resolve it
                if isinstance(self.GROUP_ID, str) and not str(self.GROUP_ID).lstrip('-').isdigit():
                    try:
                        username = str(self.GROUP_ID).strip().lstrip('@')
                        chat = await context.bot.get_chat(f"@{username}")
                        self.GROUP_ID = chat.id
                        logger.info(f"✅ Resolved GROUP_ID to chat_id: {self.GROUP_ID}")
                    except Exception as e:
                        logger.error(f"❌ Failed to resolve GROUP_ID: {e}")
                        return

                # Now call the actual handler
                await self.handle_group_message(update, context)

            # Add handler with filter for group messages (not commands) - both group and supergroup
            application.add_handler(
                MessageHandler(
                    (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP) & ~filters.COMMAND,
                    group_message_wrapper
                ),
                group=-1  # Highest priority - check before ALL other handlers (even replies)
            )
            logger.info(f"✅ Group message handler added (priority: -1)")
            logger.info("=" * 60)

            # Add periodic jobs to check and reconcile chat permissions.
            if self.GROUP_ID:
                job_queue = application.job_queue
                if job_queue:
                    # Run immediately on startup
                    job_queue.run_once(
                        self.check_and_restore_chat_permissions,
                        when=5  # Run after 5 seconds (to ensure bot is fully started)
                    )
                    # Check for sheet changes every 30 minutes.
                    job_queue.run_repeating(
                        self.check_and_restore_chat_permissions,
                        interval=1800,
                        first=1805
                    )
                    # Reconcile permissions directly against the sheet once per day.
                    # This repairs permission drift even when the sheet has not changed.
                    job_queue.run_repeating(
                        self.apply_permissions_from_sheet_direct,
                        interval=86400,
                        first=86400
                    )
                    logger.info(f"✅ Chat permissions jobs scheduled: startup check, 30-minute change checks, and daily full reconciliation for group {self.GROUP_ID}")
                else:
                    logger.warning("JobQueue is unavailable; daily fallback reconciliation will run after startup")
            else:
                logger.warning(f"⚠️ GROUP_ID not set: {self.GROUP_ID}. Permission checking jobs will not run.")

            # Add post_init handler to sync group members immediately after bot starts
            async def post_init(application: Application) -> None:
                """Sync group members to sheet immediately after bot starts"""
                # First, try to resolve GROUP_ID if it's a username
                if self.GROUP_ID and isinstance(self.GROUP_ID, str) and not str(self.GROUP_ID).lstrip('-').isdigit():
                    logger.info(f"🔍 GROUP_ID is a username ({self.GROUP_ID}), trying to resolve to chat_id...")
                    try:
                        username = str(self.GROUP_ID).strip().lstrip('@')
                        chat = await application.bot.get_chat(f"@{username}")
                        self.GROUP_ID = chat.id
                        logger.info(f"✅ Resolved GROUP_ID to chat_id: {self.GROUP_ID}")
                    except Exception as e:
                        logger.error(f"❌ Failed to resolve GROUP_ID username: {e}")
                        logger.error("⚠️ Please set GROUP_ID in .env to the actual chat_id number (not username)")
                        return

                # Ensure we have a bot instance and start log forwarder
                try:
                    self.bot = application.bot
                    self._setup_logging_forwarder(application)
                except Exception as e:
                    logger.warning(f"Failed to set up logging forwarder: {e}")

                # Sync group members
                if self.GROUP_ID:
                    logger.info("=" * 50)
                    logger.info("POST_INIT: Syncing group members to sheet on startup...")
                    logger.info(f"   Using GROUP_ID: {self.GROUP_ID}")
                    logger.info("=" * 50)
                    try:
                        # Create a context for sync
                        class DummyContext:
                            def __init__(self, bot):
                                self.bot = bot

                        dummy_context = DummyContext(application.bot)
                        await self.sync_group_members_to_sheet(dummy_context)

                        # Initialize cache after sync
                        logger.info("Initializing sheet cache...")
                        self.refresh_sheet_cache()
                        logger.info("✅ POST_INIT: Startup sync and cache initialization completed")
                    except Exception as e:
                        logger.error(f"❌ POST_INIT: Error in startup sync: {e}")
                else:
                    logger.warning("⚠️ GROUP_ID is None after resolution!")


                # Keep daily reconciliation working without PTB's optional JobQueue.
                if self.GROUP_ID and not application.job_queue:
                    class DailyContext:
                        def __init__(self, bot):
                            self.bot = bot

                    daily_context = DailyContext(application.bot)
                    logger.warning("JobQueue unavailable; using built-in daily permission reconciliation")
                    try:
                        await self.apply_permissions_from_sheet_direct(daily_context)
                    except Exception as e:
                        logger.error(f"Initial permission reconciliation failed: {e}")

                    async def daily_reconciliation_loop():
                        while True:
                            await asyncio.sleep(86400)
                            try:
                                await self.apply_permissions_from_sheet_direct(daily_context)
                            except Exception as e:
                                logger.error(f"Daily permission reconciliation failed: {e}")

                    self.permission_reconciliation_task = asyncio.create_task(daily_reconciliation_loop())
            application.post_init = post_init

            # Start the bot
            logger.info("Starting Enhanced Council Bot...")
            # Start polling with specific offset
            application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES, close_loop=False)

        except KeyboardInterrupt:
            logger.info("Bot stopped by user (Ctrl+C)")
        except Exception as e:
            logger.error(f"Bot stopped due to error: {e}")
        finally:
            # Clean up background tasks
            if self.access_update_task and not self.access_update_task.done():
                self.access_update_task.cancel()
                # Don't wait for the task to finish - it will clean up on cancellation
                # await self.access_update_task  # Removed to fix syntax error

            if getattr(self, 'permission_reconciliation_task', None) and not self.permission_reconciliation_task.done():
                self.permission_reconciliation_task.cancel()

            # Always release the lock when the bot stops
            self.release_lock()

