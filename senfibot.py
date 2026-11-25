import logging
import bot_shared
bot_shared.logger = logging.getLogger(__name__)
from bot_shared import *
from bot_conversations import ConversationHandlers
from bot_runtime import BotRuntime
from bot_access import AccessManagement
from bot_group import GroupHandlers

class EnhancedCouncilBot(ConversationHandlers, BotRuntime, AccessManagement, GroupHandlers):
    def __init__(self):
        self.FIRST_RUN_NO_CACHE = True
        self.db = Database(Config.DATABASE_PATH)
        self.user_states: Dict[int, Dict[str, Any]] = {}
        self.message_thread_map: Dict[int, int] = {}  # Maps telegram message_id to thread_id
        self.user_message_counts: Dict[int, Dict[str, int]] = {}  # Rate limiting: user_id -> {date -> count}
        self.user_last_message: Dict[int, datetime] = {}  # Rate limiting: user_id -> last_message_time

        # Lock file for preventing multiple instances
        self.lock_file_path = "bot.lock"
        self.lock_file = None

        # Channel ID for logging all messages
        self.CHANNEL_ID = Config.CHANNEL_ID  # Get from config

        # Group ID for @shora_sharif - convert to int if it's a string, or resolve username
        group_id = Config.GROUP_ID
        if group_id:
            # Remove @ if present and strip whitespace
            group_id = str(group_id).strip().lstrip('@')

            # Try to convert to int first
            try:
                self.GROUP_ID = int(group_id)
                logger.info(f"GROUP_ID set to integer: {self.GROUP_ID}")
            except (ValueError, TypeError):
                # If it's not a number, it might be a username - we'll resolve it later
                self.GROUP_ID = group_id
                logger.warning(f"GROUP_ID is not a number, treating as username: {group_id}")
                logger.warning("⚠️ GROUP_ID should be a number (chat_id), not a username!")
                logger.warning("⚠️ Please set GROUP_ID in .env to the actual chat_id number")
        else:
            self.GROUP_ID = None
            logger.warning("⚠️ GROUP_ID is not set in config!")

        # Google Sheets connection
        self.sheet = None
        self.init_google_sheets()

        # Initialize cache manager for efficient sheet data caching and change detection
        self.cache_manager = SheetCacheManager(cache_file_path="sheet_cache.json")

        # Legacy cache variables (kept for backward compatibility but now using cache_manager)
        # Format: {user_id: {'first_name': str, 'last_name': str, 'username': str, 'student_number': str, 'is_valid': str}}
        self.sheet_cache: Dict[int, Dict[str, str]] = {}
        self.sheet_cache_last_update: Optional[datetime] = None

        # Log forwarder (for sending logs to Telegram channel)
        self.log_queue: Optional[asyncio.Queue] = None
        self.log_sender_task: Optional[asyncio.Task] = None
        self.log_batch_interval: int = 300  # Send logs every 5 minutes (300 seconds)
        self.log_batch_max: int = 5  # Maximum 5 messages per batch to reduce noise

        # Access updates queue for batching Google Sheet
        batching = Config.ACCESS_UPDATE_BATCHING if hasattr(Config, 'ACCESS_UPDATE_BATCHING') else True
        if batching:
            self.access_updates_queue: List[Tuple[int, str]] = []  # list of (user_id, timestamp_string)
            self.access_update_lock = asyncio.Lock()
            self.access_update_task: Optional[asyncio.Task] = None
        else:
            self.access_updates_queue = None
            self.access_update_lock = None
            self.access_update_task = None

        # Pyrogram client for getting group members
        # Use bot token instead of user account to avoid phone number authentication
        self.pyrogram_client = None
        if PYROGRAM_AVAILABLE and Config.PYROGRAM_API_ID and Config.PYROGRAM_API_HASH and Config.TELEGRAM_BOT_TOKEN:
            try:
                self.pyrogram_client = Client(
                    Config.PYROGRAM_SESSION_NAME,
                    api_id=int(Config.PYROGRAM_API_ID),
                    api_hash=Config.PYROGRAM_API_HASH,
                    bot_token=Config.TELEGRAM_BOT_TOKEN  # Use bot token to avoid phone auth
                )
                logger.info("Pyrogram client initialized with bot token for group member syncing")
            except Exception as e:
                logger.warning(f"Could not initialize Pyrogram client: {e}")
                self.pyrogram_client = None

        # Load message mappings from database on startup
        self.load_message_mappings()

    async def send_to_channel(self, context: Optional[ContextTypes.DEFAULT_TYPE], message: str, parse_mode: str = 'HTML'):
        """Send message to the logging channel (optional - silently fails if channel not available)
        If context is None, a bot instance set on self.bot (in post_init) will be used."""
        try:
            # Determine bot to use
            bot = None
            if context and getattr(context, 'bot', None):
                bot = context.bot
            elif hasattr(self, 'bot') and getattr(self, 'bot', None):
                bot = self.bot

            # Skip channel logging if no bot or no channel configured
            if not bot:
                logger.info("Channel logging disabled - no bot instance available")
                return
            if not hasattr(self, 'CHANNEL_ID') or not self.CHANNEL_ID or self.CHANNEL_ID == -1001234567890:
                logger.info("Channel logging disabled - no valid channel configured")
                return

            # Split long messages into Telegram-safe chunks and escape HTML
            def _chunks(text, size=3800):
                for i in range(0, len(text), size):
                    yield text[i:i+size]

            for chunk in _chunks(message):
                escaped = html.escape(chunk)
                await bot.send_message(
                    chat_id=self.CHANNEL_ID,
                    text=f"<pre>{escaped}</pre>",
                    parse_mode=parse_mode
                )

            logger.info(f"Message sent to channel {self.CHANNEL_ID}")
        except Exception as e:
            # Silently log channel errors - don't interrupt main functionality
            logger.info(f"Channel logging unavailable: {e}")

    async def safe_send_message(self, context, chat_id, text, reply_markup=None, reply_to_message_id=None, parse_mode=None, max_retries=3):
        """Safely send message with fallback for parsing errors and network issues"""
        import asyncio

        for attempt in range(max_retries):
            try:
                # First try with the specified parse mode
                return await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    reply_to_message_id=reply_to_message_id,
                    parse_mode=parse_mode
                )
            except Exception as e:
                if "can't parse entities" in str(e).lower() or "parse" in str(e).lower():
                    # If parsing fails, try without parse mode
                    logger.warning(f"Parsing failed, trying without parse mode: {e}")
                    try:
                        return await context.bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            reply_markup=reply_markup,
                            reply_to_message_id=reply_to_message_id,
                            parse_mode=None
                        )
                    except Exception as e2:
                        logger.error(f"Failed to send message even as plain text: {e2}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt)  # Exponential backoff
                            continue
                        raise e2
                elif "connect" in str(e).lower() or "timeout" in str(e).lower() or "network" in str(e).lower() or "timed out" in str(e).lower():
                    # Network error or timeout - retry with backoff
                    logger.warning(f"Network/timeout error on attempt {attempt + 1}/{max_retries}: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    else:
                        logger.error(f"Network/timeout error after {max_retries} attempts: {e}")
                        raise e
                else:
                    # Other errors
                    logger.error(f"Unexpected error: {e}")
                    raise e

        # This shouldn't be reached, but just in case
        raise Exception("Max retries exceeded")


    def acquire_lock(self) -> bool:
        """Try to acquire a lock to prevent multiple instances"""
        try:
            self.lock_file = open(self.lock_file_path, 'w')
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            # Write current process info to lock file
            import psutil
            current_process = psutil.Process()
            lock_info = f"PID: {current_process.pid}\n"
            lock_info += f"Command: {' '.join(sys.argv)}\n"
            lock_info += f"Started: {datetime.now().isoformat()}\n"
            self.lock_file.write(lock_info)
            self.lock_file.flush()

            logger.info(f"Lock acquired successfully. PID: {current_process.pid}")
            return True
        except (IOError, OSError) as e:
            logger.error(f"Failed to acquire lock: {e}")
            if self.lock_file:
                self.lock_file.close()
                self.lock_file = None
            return False

    def release_lock(self):
        """Release the lock file"""
        if self.lock_file:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                self.lock_file.close()
                os.unlink(self.lock_file_path)
                logger.info("Lock released successfully")
            except (IOError, OSError) as e:
                logger.error(f"Error releasing lock: {e}")
            finally:
                self.lock_file = None

    def init_google_sheets(self):
        """Initialize Google Sheets connection"""
        try:
            SCOPES = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            creds = Credentials.from_service_account_file(
                Config.GOOGLE_SERVICE_ACCOUNT_FILE,
                scopes=SCOPES
            )
            client = gspread.authorize(creds)
            self.sheet = client.open_by_key(Config.SPREADSHEET_ID).sheet1
            logger.info("Google Sheets connection initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing Google Sheets: {e}")
            self.sheet = None


if __name__ == '__main__':
    bot = EnhancedCouncilBot()
    bot.run()


