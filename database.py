import sqlite3
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

class Database:
    def __init__(self, db_path: str = "./bot_database.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Roles table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS roles (
                role_id INTEGER PRIMARY KEY AUTOINCREMENT,
                role_name TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Threads table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS threads (
                thread_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                FOREIGN KEY (role_id) REFERENCES roles (role_id)
            )
        ''')
        
        # Messages table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id INTEGER NOT NULL,
                telegram_message_id INTEGER NOT NULL,
                sender_type TEXT NOT NULL, -- 'user' or 'admin'
                message_text TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_read BOOLEAN DEFAULT 0,
                FOREIGN KEY (thread_id) REFERENCES threads (thread_id)
            )
        ''')
        
        # Blocks table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blocks (
                block_id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_user_id INTEGER NOT NULL,
                blocked_user_id INTEGER NOT NULL,
                blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reason TEXT,
                UNIQUE(admin_user_id, blocked_user_id)
            )
        ''')
        
        # Message mappings table for admin reply tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS message_mappings (
                telegram_message_id INTEGER PRIMARY KEY,
                thread_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (thread_id) REFERENCES threads (thread_id)
            )
        ''')
        
        # Always update roles from environment variables
        self.update_roles_from_env()
        
        conn.commit()
        conn.close()
    
    def update_roles_from_env(self):
        """Update roles table from environment variables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Clear existing roles and reset autoincrement
        cursor.execute('DELETE FROM roles')
        cursor.execute('DELETE FROM sqlite_sequence WHERE name = "roles"')
        
        # Insert roles from environment variables
        from config import Config
        
        role_configs = [
            ('دبیر', 'ROLE_SECRETARY_USER_ID', ''),
            ('نائب‌دبیر', 'ROLE_DEPUTY_SECRETARY_USER_ID', ''),
            ('کارگروه سازماندهی', 'ROLE_ORGANIZATION_USER_ID', ''),
            ('کارگروه آموزش', 'ROLE_EDUCATION_USER_ID', ''),
            ('کارگروه حقوقی', 'ROLE_LEGAL_USER_ID', ''),
            ('کارگروه زنان', 'ROLE_WOMEN_USER_ID', ''),
            ('کارگروه تغذیه', 'ROLE_NUTRITION_USER_ID', ''),
            ('کارگروه سلامت', 'ROLE_HEALTH_USER_ID', ''),
            ('کارگروه تاسیسات و خدمات رفاهی', 'ROLE_FACILITIES_USER_ID', ''),
            ('کارگروه خوابگاه‌ها', 'ROLE_DORMITORY_USER_ID', ''),
            ('کارگروه روابط عمومی و رویدادها', 'ROLE_PUBLIC_RELATIONS_USER_ID', ''),
        ]
        
        for role_name, user_id_key, description in role_configs:
            actual_user_id = Config.get_role_user_id(user_id_key)
            if actual_user_id:  # Only insert if user ID is configured
                cursor.execute('''
                    INSERT INTO roles (role_name, user_id, description)
                    VALUES (?, ?, ?)
                ''', (role_name, actual_user_id, description))
        
        conn.commit()
        conn.close()
    
    def add_user(self, user_id: int, username: str = None, first_name: str = None, last_name: str = None):
        """Add or update user information"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name))
        
        conn.commit()
        conn.close()
    
    def get_roles(self) -> List[Dict[str, Any]]:
        """Get all available roles with valid user IDs"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT role_id, role_name, user_id, description FROM roles ORDER BY role_id')
        roles = []
        for row in cursor.fetchall():
            role_id, role_name, user_id, description = row
            
            # Only include roles that have actual user IDs (not placeholder values)
            if user_id and not user_id.startswith('ROLE_') and not user_id.endswith('_USER_ID'):
                roles.append({
                    'role_id': role_id,
                    'role_name': role_name,
                    'user_id': user_id,
                    'description': description
                })
        
        conn.close()
        return roles
    
    def get_role_by_id(self, role_id: int) -> Optional[Dict[str, Any]]:
        """Get role by ID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT role_id, role_name, user_id, description FROM roles WHERE role_id = ?', (role_id,))
        row = cursor.fetchone()
        
        conn.close()
        
        if row:
            return {
                'role_id': row[0],
                'role_name': row[1],
                'user_id': row[2],  # Changed from group_id
                'description': row[3]
            }
        return None
    
    def get_active_thread(self, user_id: int, role_id: int) -> Optional[int]:
        """Get thread for user and role - only one thread per user per role"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT thread_id FROM threads 
            WHERE user_id = ? AND role_id = ?
            ORDER BY last_activity DESC LIMIT 1
        ''', (user_id, role_id))
        
        result = cursor.fetchone()
        conn.close()
        
        return result[0] if result else None
    
    def create_thread(self, user_id: int, role_id: int) -> int:
        """Create a new thread for user and role - only one thread per user per role"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if there's already a thread for this user and role
        cursor.execute('''
            SELECT thread_id FROM threads 
            WHERE user_id = ? AND role_id = ?
        ''', (user_id, role_id))
        
        existing_thread = cursor.fetchone()
        
        if existing_thread:
            # Update existing thread to active and update last activity
            thread_id = existing_thread[0]
            cursor.execute('''
                UPDATE threads 
                SET is_active = 1, last_activity = CURRENT_TIMESTAMP
                WHERE thread_id = ?
            ''', (thread_id,))
        else:
            # Create new thread
            cursor.execute('''
                INSERT INTO threads (user_id, role_id)
                VALUES (?, ?)
            ''', (user_id, role_id))
            thread_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return thread_id
    
    def add_message(self, thread_id: int, telegram_message_id: int, sender_type: str, message_text: str):
        """Add a message to a thread"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO messages (thread_id, telegram_message_id, sender_type, message_text)
            VALUES (?, ?, ?, ?)
        ''', (thread_id, telegram_message_id, sender_type, message_text))
        
        # Update thread last activity
        cursor.execute('''
            UPDATE threads SET last_activity = CURRENT_TIMESTAMP
            WHERE thread_id = ?
        ''', (thread_id,))
        
        conn.commit()
        conn.close()
    
    def get_thread_messages(self, thread_id: int) -> List[Dict[str, Any]]:
        """Get all messages in a thread"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT message_id, telegram_message_id, sender_type, message_text, sent_at, is_read
            FROM messages 
            WHERE thread_id = ?
            ORDER BY sent_at ASC
        ''', (thread_id,))
        
        messages = []
        for row in cursor.fetchall():
            messages.append({
                'message_id': row[0],
                'telegram_message_id': row[1],
                'sender_type': row[2],
                'message_text': row[3],
                'sent_at': row[4],
                'is_read': bool(row[5])
            })
        
        conn.close()
        return messages
    
    def get_user_threads(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all threads for a user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT t.thread_id, t.role_id, r.role_name, t.created_at, t.last_activity, t.is_active
            FROM threads t
            JOIN roles r ON t.role_id = r.role_id
            WHERE t.user_id = ?
            ORDER BY t.last_activity DESC
        ''', (user_id,))
        
        threads = []
        for row in cursor.fetchall():
            threads.append({
                'thread_id': row[0],
                'role_id': row[1],
                'role_name': row[2],
                'created_at': row[3],
                'last_activity': row[4],
                'is_active': bool(row[5])
            })
        
        conn.close()
        return threads
    
    def mark_messages_as_read(self, thread_id: int, sender_type: str = 'admin'):
        """Mark messages as read for a specific sender type in a thread"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE messages SET is_read = 1
            WHERE thread_id = ? AND sender_type = ?
        ''', (thread_id, sender_type))
        
        conn.commit()
        conn.close()
    
    def get_unread_messages_count(self, user_id: int) -> int:
        """Get count of unread messages for a user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT COUNT(*) FROM messages m
            JOIN threads t ON m.thread_id = t.thread_id
            WHERE t.user_id = ? AND m.sender_type = 'admin' AND m.is_read = 0
        ''', (user_id,))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        return count
    
    def block_user(self, admin_user_id: int, blocked_user_id: int, reason: str = None):
        """Block a user by an admin"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO blocks (admin_user_id, blocked_user_id, reason)
            VALUES (?, ?, ?)
        ''', (admin_user_id, blocked_user_id, reason))
        
        conn.commit()
        conn.close()
    
    def unblock_user(self, admin_user_id: int, blocked_user_id: int):
        """Unblock a user by an admin"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            DELETE FROM blocks 
            WHERE admin_user_id = ? AND blocked_user_id = ?
        ''', (admin_user_id, blocked_user_id))
        
        conn.commit()
        conn.close()
    
    def is_user_blocked(self, admin_user_id: int, user_id: int) -> bool:
        """Check if a user is blocked by an admin"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT COUNT(*) FROM blocks 
            WHERE admin_user_id = ? AND blocked_user_id = ?
        ''', (admin_user_id, user_id))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        return count > 0
    
    def get_blocked_users(self, admin_user_id: int) -> List[Dict[str, Any]]:
        """Get list of users blocked by an admin"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT blocked_user_id, blocked_at, reason
            FROM blocks 
            WHERE admin_user_id = ?
            ORDER BY blocked_at DESC
        ''', (admin_user_id,))
        
        blocked_users = []
        for row in cursor.fetchall():
            blocked_users.append({
                'user_id': row[0],
                'blocked_at': row[1],
                'reason': row[2]
            })
        
        conn.close()
        return blocked_users
    
    def get_thread_info(self, thread_id: int) -> Optional[Dict[str, Any]]:
        """Get thread information by thread_id"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT t.thread_id, t.user_id, t.role_id, t.created_at, t.last_activity, t.is_active,
                   r.role_name, r.user_id as role_user_id
            FROM threads t
            JOIN roles r ON t.role_id = r.role_id
            WHERE t.thread_id = ?
        ''', (thread_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                'thread_id': row[0],
                'user_id': row[1],
                'role_id': row[2],
                'created_at': row[3],
                'last_activity': row[4],
                'is_active': bool(row[5]),
                'role_name': row[6],
                'role_user_id': row[7]
            }
        return None 