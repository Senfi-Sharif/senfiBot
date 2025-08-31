import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Config:
    # Telegram Bot Configuration
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    # Database Configuration
    DATABASE_PATH = os.getenv('DATABASE_PATH', './bot_database.db')
    
    # Admin Configuration
    ADMIN_USER_ID = os.getenv('ADMIN_USER_ID')  # Changed from ADMIN_GROUP_ID
    
    # Role Configuration - User IDs for each role (individual accounts)
    ROLE_USERS = {
        'ROLE_SECRETARY_USER_ID': os.getenv('ROLE_SECRETARY_USER_ID'),                    # دبیر: فرزاد رحمانی
        'ROLE_DEPUTY_SECRETARY_USER_ID': os.getenv('ROLE_DEPUTY_SECRETARY_USER_ID'),      # نائب‌دبیر: میعاد رضایی
        'ROLE_ORGANIZATION_USER_ID': os.getenv('ROLE_ORGANIZATION_USER_ID'),              # کارگروه سازماندهی: عرفان بیدمشکی
        'ROLE_EDUCATION_USER_ID': os.getenv('ROLE_EDUCATION_USER_ID'),                    # کارگروه آموزش: علیرضا ناصری
        'ROLE_LEGAL_USER_ID': os.getenv('ROLE_LEGAL_USER_ID'),                            # کارگروه حقوقی: حسن براتی
        'ROLE_WOMEN_USER_ID': os.getenv('ROLE_WOMEN_USER_ID'),                            # کارگروه زنان: نرگس کاری
        'ROLE_NUTRITION_USER_ID': os.getenv('ROLE_NUTRITION_USER_ID'),                    # کارگروه تغذیه: آرین شباهنگ
        'ROLE_HEALTH_USER_ID': os.getenv('ROLE_HEALTH_USER_ID'),                          # کارگروه سلامت: بهاره حیدری
        'ROLE_FACILITIES_USER_ID': os.getenv('ROLE_FACILITIES_USER_ID'),                  # کارگروه تاسیسات و خدمات رفاهی: سید رضا رضی
        'ROLE_DORMITORY_USER_ID': os.getenv('ROLE_DORMITORY_USER_ID'),                    # کارگروه خوابگاه‌ها: مجید محمدی
        'ROLE_PUBLIC_RELATIONS_USER_ID': os.getenv('ROLE_PUBLIC_RELATIONS_USER_ID'),      # کارگروه روابط عمومی و رویدادها: آرشام برقی
        'ROLE_PUBLICATION_USER_ID': os.getenv('ROLE_PUBLICATION_USER_ID'),                # نشریه
    }
    
    # Bot Settings
    MAX_MESSAGE_LENGTH = 4096
    MAX_MESSAGES_PER_10_MINUTES = 5  # Limit messages per user per 10 minutes per role
    
    # Channel Configuration
    CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
    
    @classmethod
    def validate_config(cls):
        """Validate that all required configuration is present"""
        missing_configs = []
        
        if not cls.TELEGRAM_BOT_TOKEN:
            missing_configs.append('TELEGRAM_BOT_TOKEN')
        
        if not cls.ADMIN_USER_ID:
            missing_configs.append('ADMIN_USER_ID')
        
        # Check if at least one role user is configured
        role_users_configured = any(cls.ROLE_USERS.values())
        if not role_users_configured:
            missing_configs.append('At least one role user ID')
        
        if missing_configs:
            raise ValueError(f"Missing required configuration: {', '.join(missing_configs)}")
        
        return True
    
    @classmethod
    def get_role_user_id(cls, role_user_key: str) -> str:
        """Get the actual user ID for a role"""
        return cls.ROLE_USERS.get(role_user_key) 