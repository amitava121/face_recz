#!/usr/bin/env python3
"""
Database Migration Script: Add Users table with role-based access control
This script creates the 'users' table and migrates existing admin to the new system
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models.db import db, User, Admin, ensure_database_exists
from flask import Flask
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_app():
    """Create Flask app for migration"""
    load_dotenv()
    
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config['SQLALCHEMY_DATABASE_URI'] = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    db.init_app(app)
    return app

def migrate_users_table():
    """Add users table and migrate existing admin"""
    app = create_app()
    
    with app.app_context():
        logger.info("🔄 Starting migration: Adding users table with role-based access")
        
        try:
            # Create users table
            logger.info("Creating users table...")
            db.create_all()
            logger.info("✅ Users table created successfully")
            
            # Check if admin user already exists in users table
            existing_admin = User.query.filter_by(role='admin').first()
            
            if existing_admin:
                logger.info(f"✅ Admin user already exists: {existing_admin.username}")
            else:
                # Migrate existing admin from Admin table to User table
                old_admin = Admin.query.first()
                
                if old_admin:
                    logger.info(f"🔄 Migrating admin '{old_admin.username}' to users table...")
                    
                    # Create admin user in new table
                    admin_user = User(
                        username=old_admin.username,
                        password_hash=old_admin.password_hash,
                        email=old_admin.email,
                        role='admin',
                        student_id=None,
                        created_at=old_admin.created_at,
                        last_login=old_admin.last_login,
                        is_active=True
                    )
                    
                    db.session.add(admin_user)
                    db.session.commit()
                    logger.info(f"✅ Admin user migrated successfully: {admin_user.username}")
                else:
                    logger.warning("⚠️ No existing admin found in Admin table")
                    logger.info("💡 You can create an admin user manually or through the registration page")
            
            logger.info("\n" + "="*60)
            logger.info("✅ Migration completed successfully!")
            logger.info("="*60)
            logger.info("\nUsers table structure:")
            logger.info("  - username (unique)")
            logger.info("  - password_hash")
            logger.info("  - email (unique)")
            logger.info("  - role ('admin' or 'viewer')")
            logger.info("  - student_id (for viewers only)")
            logger.info("  - is_active")
            logger.info("  - created_at, last_login")
            logger.info("\n" + "="*60)
            
            # Show existing users
            users = User.query.all()
            if users:
                logger.info(f"\n📊 Existing users ({len(users)}):")
                for user in users:
                    logger.info(f"  - {user.username} ({user.role}) - {user.email}")
            
        except Exception as e:
            logger.error(f"❌ Migration failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            sys.exit(1)

if __name__ == '__main__':
    migrate_users_table()
