import logging
import os
import sqlite3
import fcntl
import sys
import asyncio
import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

from database import Database
from config import Config
from sheet_cache_manager import SheetCacheManager
import gspread
from google.oauth2.service_account import Credentials

# Pyrogram for getting group members
try:
    from pyrogram import Client
    PYROGRAM_AVAILABLE = True
except ImportError:
    PYROGRAM_AVAILABLE = False

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
CHOOSING_ROLE, WAITING_FOR_MESSAGE, WAITING_FOR_STUDENT_NUMBER = range(3)

class EnhancedCouncilBot:
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

        # Log forwarder (for sending all logs to Telegram channel)
        self.log_queue: Optional[asyncio.Queue] = None
        self.log_sender_task: Optional[asyncio.Task] = None
        self.log_batch_interval: int = 10  # seconds between sending batches
        self.log_batch_max: int = 50  # max messages per batch
        
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
                "service_account.json",
                scopes=SCOPES
            )
            client = gspread.authorize(creds)
            self.sheet = client.open_by_key("1S0qznFEtw31fhaGXNOsggg0nGvM6g74Rp3A8PVwGFsI").sheet1
            logger.info("Google Sheets connection initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing Google Sheets: {e}")
            self.sheet = None
        
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
            
            application = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).request(request).build()
            
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
            
            # Add periodic job to check and restore chat permissions for valid users (every hour)
            if self.GROUP_ID:
                job_queue = application.job_queue
                if job_queue:
                    # Run immediately on startup
                    job_queue.run_once(
                        self.check_and_restore_chat_permissions,
                        when=5  # Run after 5 seconds (to ensure bot is fully started)
                    )
                    # Then run every hour
                    job_queue.run_repeating(
                        self.check_and_restore_chat_permissions,
                        interval=1800,  # 1 hour in seconds (changed from 24 hours)
                        first=1805  # Start after first run + 5 seconds
                    )
                    logger.info(f"✅ Chat permissions job scheduled: immediate run + every 1 hour for group {self.GROUP_ID}")
                else:
                    logger.error("❌ JobQueue is not available! Make sure python-telegram-bot[job-queue] is installed.")
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
            # Always release the lock when the bot stops
            self.release_lock()
    
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
        """Write Iran time to column J for a user when access is opened"""
        if not self.sheet:
            logger.warning("Google Sheets not initialized, cannot mark access opened")
            return False
        try:
            all_values = self.sheet.get_all_values()
            for idx, row in enumerate(all_values, start=1):
                if row and len(row) > 0 and str(row[0]).strip() == str(user_id):
                    iran_now = datetime.now(ZoneInfo('Asia/Tehran'))
                    ts = iran_now.strftime("%Y-%m-%d %H:%M:%S")
                    # Column J is index 10
                    self.sheet.update_cell(idx, 10, ts)
                    logger.info(f"Marked access opened for user {user_id} in sheet at {ts}")
                    try:
                        self.refresh_sheet_cache()
                    except Exception:
                        logger.warning("Failed to refresh cache after marking access opened")
                    # Notify channel about opened access
                    try:
                        msg = f"🔓 <b>Access opened</b>\n🕒 {ts}\n👤 User: {user_id}"
                        await self.send_to_channel(context, msg, parse_mode='HTML')
                    except Exception as e:
                        logger.warning(f"Failed to send access-opened notification to channel: {e}")
                    return True
            logger.warning(f"User {user_id} not found in sheet to mark access opened")
            return False
        except Exception as e:
            logger.error(f"Error marking access opened for user {user_id}: {e}")
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
        
        # Skip bot messages
        if update.effective_user.is_bot:
            logger.info("⏭️ handle_group_message: Skipping bot message")
            return
        
        user = update.effective_user
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

if __name__ == '__main__':
    bot = EnhancedCouncilBot()
    bot.run() 