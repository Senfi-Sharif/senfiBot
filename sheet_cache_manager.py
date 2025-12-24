import json
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class SheetCacheManager:
    """Manages local caching of Google Sheets data with change detection"""
    
    def __init__(self, cache_file_path: str = "sheet_cache.json"):
        """Initialize cache manager
        
        Args:
            cache_file_path: Path to the JSON file for storing cache
        """
        self.cache_file_path = cache_file_path
        self.cache: Dict[int, Dict[str, str]] = {}
        self.last_update: Optional[datetime] = None
        
        # Load existing cache on initialization
        self.load_cache()
    
    def load_cache(self) -> bool:
        """Load cache from disk
        
        Returns:
            True if cache loaded successfully, False otherwise
        """
        try:
            if os.path.exists(self.cache_file_path):
                with open(self.cache_file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                    # Convert user_ids from string back to int
                    self.cache = {int(uid): user_data for uid, user_data in data.get('users', {}).items()}
                    
                    # Parse last update timestamp
                    last_update_str = data.get('last_update')
                    if last_update_str:
                        self.last_update = datetime.fromisoformat(last_update_str)
                    
                    logger.info(f"✅ Loaded cache from disk: {len(self.cache)} users, last update: {self.last_update}")
                    return True
            else:
                logger.info("No existing cache file found, starting fresh")
                return False
                
        except Exception as e:
            logger.error(f"Error loading cache from disk: {e}")
            return False
    
    def save_cache(self) -> bool:
        """Save cache to disk
        
        Returns:
            True if cache saved successfully, False otherwise
        """
        try:
            # Create cache data structure
            data = {
                'users': {str(uid): user_data for uid, user_data in self.cache.items()},
                'last_update': self.last_update.isoformat() if self.last_update else None
            }
            
            # Write to temporary file first, then rename (atomic operation)
            temp_file = f"{self.cache_file_path}.tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            # Rename temporary file to actual cache file
            os.replace(temp_file, self.cache_file_path)
            
            logger.info(f"✅ Saved cache to disk: {len(self.cache)} users")
            return True
            
        except Exception as e:
            logger.error(f"Error saving cache to disk: {e}")
            return False
    
    def update_cache(self, sheet_data: List[List[str]]) -> Dict[str, List[int]]:
        """Update cache with new data from Google Sheets and detect changes
        
        Args:
            sheet_data: Raw data from Google Sheets (list of rows)
        
        Returns:
            Dictionary with change information:
            {
                'added': [list of added user_ids],
                'modified': [list of modified user_ids],
                'removed': [list of removed user_ids],
                'to_restore': [list of user_ids to restore permissions],
                'to_restrict': [list of user_ids to restrict permissions]
            }
        """
        old_cache = self.cache.copy()
        new_cache: Dict[int, Dict[str, str]] = {}
        
        # Parse sheet data
        for idx, row in enumerate(sheet_data, start=1):
            if row and len(row) >= 6:  # At least 6 columns (A-F)
                try:
                    # Column A: Telegram ID, B: First Name, C: Last Name, D: Username, E: Student Number, F: is_valid
                    telegram_id = str(row[0]).strip()
                    if telegram_id:
                        try:
                            user_id = int(telegram_id)
                            new_cache[user_id] = {
                                'first_name': str(row[1]).strip() if len(row) > 1 else "",
                                'last_name': str(row[2]).strip() if len(row) > 2 else "",
                                'username': str(row[3]).strip() if len(row) > 3 else "",
                                'student_number': str(row[4]).strip() if len(row) > 4 else "",
                                'is_valid': str(row[5]).strip() if len(row) > 5 else "0"
                            }
                        except ValueError:
                            continue
                except (ValueError, IndexError) as e:
                    logger.debug(f"Error parsing row {idx}: {e}")
                    continue
        
        # Detect changes
        changes = self._detect_changes(old_cache, new_cache)
        
        # Update cache
        self.cache = new_cache
        self.last_update = datetime.now()
        
        # Save to disk
        self.save_cache()
        
        return changes
    
    def _detect_changes(self, old_cache: Dict[int, Dict[str, str]], 
                        new_cache: Dict[int, Dict[str, str]]) -> Dict[str, List[int]]:
        """Detect changes between old and new cache
        
        Args:
            old_cache: Previous cache state
            new_cache: New cache state
        
        Returns:
            Dictionary with change information
        """
        changes = {
            'added': [],
            'modified': [],
            'removed': [],
            'to_restore': [],
            'to_restrict': []
        }
        
        # Find added and modified users
        for user_id, user_data in new_cache.items():
            if user_id not in old_cache:
                # New user added
                changes['added'].append(user_id)
                
                # Check if permissions need to be changed
                if user_data.get('is_valid') == "1":
                    changes['to_restore'].append(user_id)
                else:
                    changes['to_restrict'].append(user_id)
            else:
                # Existing user - check if data changed
                old_data = old_cache[user_id]
                
                # Check if is_valid status changed
                old_is_valid = old_data.get('is_valid', '0')
                new_is_valid = user_data.get('is_valid', '0')
                
                if old_is_valid != new_is_valid:
                    changes['modified'].append(user_id)
                    
                    # Determine permission change
                    if new_is_valid == "1":
                        changes['to_restore'].append(user_id)
                    else:
                        changes['to_restrict'].append(user_id)
                elif old_data != user_data:
                    # Other data changed (name, username, student number, etc.)
                    changes['modified'].append(user_id)
        
        # Find removed users
        for user_id in old_cache:
            if user_id not in new_cache:
                changes['removed'].append(user_id)
                # Restrict permissions for removed users
                changes['to_restrict'].append(user_id)
        
        return changes
    
    def get_cache(self) -> Dict[int, Dict[str, str]]:
        """Get current cache
        
        Returns:
            Current cache dictionary
        """
        return self.cache.copy()
    
    def get_user(self, user_id: int) -> Optional[Dict[str, str]]:
        """Get user data from cache
        
        Args:
            user_id: Telegram user ID
        
        Returns:
            User data dictionary or None if not found
        """
        return self.cache.get(user_id)
    
    def is_user_valid(self, user_id: int) -> bool:
        """Check if user is valid (is_valid=1) in cache
        
        Args:
            user_id: Telegram user ID
        
        Returns:
            True if user is valid, False otherwise
        """
        user_data = self.cache.get(user_id)
        if user_data:
            return user_data.get('is_valid') == "1"
        return False
    
    def is_user_in_cache(self, user_id: int) -> bool:
        """Check if user exists in cache
        
        Args:
            user_id: Telegram user ID
        
        Returns:
            True if user exists in cache, False otherwise
        """
        return user_id in self.cache
    
    def get_valid_users(self) -> List[int]:
        """Get list of valid user IDs (is_valid=1)
        
        Returns:
            List of valid user IDs
        """
        return [uid for uid, data in self.cache.items() if data.get('is_valid') == "1"]
    
    def get_invalid_users(self) -> List[int]:
        """Get list of invalid user IDs (is_valid=0)
        
        Returns:
            List of invalid user IDs
        """
        return [uid for uid, data in self.cache.items() if data.get('is_valid') != "1"]
    
    def get_cache_age(self) -> Optional[float]:
        """Get age of cache in seconds
        
        Returns:
            Age in seconds or None if no last_update
        """
        if self.last_update:
            return (datetime.now() - self.last_update).total_seconds()
        return None
    
    def clear_cache(self) -> bool:
        """Clear cache from memory and disk
        
        Returns:
            True if cleared successfully, False otherwise
        """
        try:
            self.cache = {}
            self.last_update = None
            
            if os.path.exists(self.cache_file_path):
                os.remove(self.cache_file_path)
                logger.info("✅ Cache cleared from memory and disk")
            
            return True
            
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
            return False
