#!/bin/bash

# Update .env file with correct configuration
sudo tee /opt/councilbot/.env > /dev/null <<EOF
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=xxxx

# Database Configuration
DATABASE_PATH=./bot_database.db

# Admin Configuration
ADMIN_USER_ID=xxxx

# Role Configuration - Individual User IDs
ROLE_SECRETARY_USER_ID=your_secretary_user_id_here                    # دبیر: فرزاد رحمانی
ROLE_DEPUTY_SECRETARY_USER_ID=your_deputy_secretary_user_id_here      # نائب‌دبیر: میعاد رضایی
ROLE_ORGANIZATION_USER_ID=your_organization_user_id_here              # کارگروه سازماندهی: عرفان بیدمشکی
ROLE_EDUCATION_USER_ID=your_education_user_id_here                    # کارگروه آموزش: علیرضا ناصری
ROLE_LEGAL_USER_ID=your_legal_user_id_here                            # کارگروه حقوقی: حسن براتی
ROLE_WOMEN_USER_ID=your_women_user_id_here                            # کارگروه زنان: نرگس کاری
ROLE_NUTRITION_USER_ID=your_nutrition_user_id_here                    # کارگروه تغذیه: آرین شباهنگ
ROLE_HEALTH_USER_ID=your_health_user_id_here                          # کارگروه سلامت: بهاره حیدری
ROLE_FACILITIES_USER_ID=your_facilities_user_id_here                  # کارگروه تاسیسات و خدمات رفاهی: سید رضا رضی
ROLE_DORMITORY_USER_ID=your_dormitory_user_id_here                    # کارگروه خوابگاه‌ها: مجید محمدی
ROLE_PUBLIC_RELATIONS_USER_ID=your_public_relations_user_id_here      # کارگروه روابط عمومی و رویدادها: آرشام برقی
ROLE_PUBLICATION_USER_ID=your_publication_user_id_here                # نشریه
EOF

# Set correct permissions
sudo chown councilbot:councilbot /opt/councilbot/.env
sudo chmod 600 /opt/councilbot/.env

echo "✅ .env file updated successfully!"
echo ""
echo "📝 Next steps:"
echo "1. Update the user IDs in /opt/councilbot/.env"
echo "2. Restart the bot: sudo systemctl restart councilbot"
echo "3. Check status: sudo systemctl status councilbot" 