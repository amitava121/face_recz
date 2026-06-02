#!/usr/bin/env python3
"""
List all users in the system (both Admin and User tables)
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models.db import db, User, Admin, Student
from flask import Flask
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_app():
    """Create Flask app"""
    load_dotenv()
    
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config['SQLALCHEMY_DATABASE_URI'] = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    db.init_app(app)
    return app

def list_all_users():
    """List all users in the system"""
    app = create_app()
    
    with app.app_context():
        logger.info("\n" + "="*80)
        logger.info("SYSTEM USERS REPORT")
        logger.info("="*80 + "\n")
        
        # List users from new User table
        users = User.query.all()
        if users:
            logger.info(f"📋 Users Table ({len(users)} users):")
            logger.info("-" * 80)
            logger.info(f"{'ID':<5} {'Username':<20} {'Email':<30} {'Role':<10} {'Active':<8} {'Student Link'}")
            logger.info("-" * 80)
            
            for user in users:
                student_info = ""
                if user.role == 'viewer' and user.student:
                    student_info = f"{user.student.name} ({user.student.student_code})"
                elif user.role == 'viewer':
                    student_info = "⚠️  No student linked"
                
                active_status = "✅ Yes" if user.is_active else "❌ No"
                
                logger.info(
                    f"{user.id:<5} {user.username:<20} {user.email:<30} "
                    f"{user.role:<10} {active_status:<8} {student_info}"
                )
            logger.info("-" * 80)
        else:
            logger.info("📋 Users Table: No users found")
        
        # List users from legacy Admin table
        logger.info("\n")
        admins = Admin.query.all()
        if admins:
            logger.info(f"👑 Admin Table (Legacy) - ({len(admins)} admins):")
            logger.info("-" * 80)
            logger.info(f"{'ID':<5} {'Username':<20} {'Email':<30} {'Last Login'}")
            logger.info("-" * 80)
            
            for admin in admins:
                last_login = admin.last_login.strftime('%Y-%m-%d %H:%M') if admin.last_login else 'Never'
                logger.info(
                    f"{admin.id:<5} {admin.username:<20} {admin.email:<30} {last_login}"
                )
            logger.info("-" * 80)
        else:
            logger.info("👑 Admin Table (Legacy): No admins found")
        
        # Summary statistics
        logger.info("\n" + "="*80)
        logger.info("SUMMARY")
        logger.info("="*80)
        
        total_users = User.query.count()
        total_admins_new = User.query.filter_by(role='admin').count()
        total_viewers = User.query.filter_by(role='viewer').count()
        active_users = User.query.filter_by(is_active=True).count()
        inactive_users = User.query.filter_by(is_active=False).count()
        
        viewers_with_students = User.query.filter(
            User.role == 'viewer',
            User.student_id.isnot(None)
        ).count()
        viewers_without_students = total_viewers - viewers_with_students
        
        legacy_admins = Admin.query.count()
        
        logger.info(f"Total Users (New System): {total_users}")
        logger.info(f"  - Admins: {total_admins_new}")
        logger.info(f"  - Viewers: {total_viewers}")
        logger.info(f"    • With student link: {viewers_with_students}")
        logger.info(f"    • Without student link: {viewers_without_students}")
        logger.info(f"  - Active: {active_users}")
        logger.info(f"  - Inactive: {inactive_users}")
        logger.info(f"\nLegacy Admin Table: {legacy_admins} admin(s)")
        logger.info("="*80 + "\n")
        
        # Warnings
        if viewers_without_students > 0:
            logger.warning("⚠️  WARNING: Some viewers are not linked to student records!")
            unlinked_viewers = User.query.filter(
                User.role == 'viewer',
                User.student_id.is_(None)
            ).all()
            logger.warning("   Unlinked viewers:")
            for viewer in unlinked_viewers:
                logger.warning(f"   - {viewer.username} ({viewer.email})")
            logger.info("\n   Fix: Use create_viewer.py or manually link student_id\n")

if __name__ == '__main__':
    list_all_users()
