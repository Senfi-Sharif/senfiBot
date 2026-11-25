import logging
import os
import sqlite3
import fcntl
import sys
import asyncio
import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, List, Tuple

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

# Reduce httpx logging to avoid flood control issues with Telegram
logging.getLogger('httpx').setLevel(logging.ERROR)
logging.getLogger('httpx').propagate = False
# Also reduce apscheduler logging
logging.getLogger('apscheduler').setLevel(logging.WARNING)
logging.getLogger('apscheduler').propagate = False

# Conversation states
CHOOSING_ROLE, WAITING_FOR_MESSAGE, WAITING_FOR_STUDENT_NUMBER = range(3)

