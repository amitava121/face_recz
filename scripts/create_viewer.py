#!/usr/bin/env python3
"""
Create Viewer Account Script
This script creates a viewer account and links it to a student record.
Usage: python create_viewer.py
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models.db import db, User, Student
from flask import Flask
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_app():
    """Create Flask app for user creation"""
    load_dotenv()
    
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config['SQLALCHEMY_DATABASE_URI'] = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    db.init_app(app)
    return app

def list_students():
    """List all students in the database"""
    students = Student.query.all()
    if not students:
        logger.info("No students found in database")
        return []
    
    logger.info(f"\n{'='*60}")
    logger.info("Available Students:")
    logger.info(f"{'='*60}")
    for student in students:
        logger.info(f"ID: {student.id} | Code: {student.student_code} | Name: {student.name} | Dept: {student.department}")
    logger.info(f"{'='*60}\n")
    return students

def create_viewer_account():
    """Interactive script to create a viewer account"""
    app = create_app()
    
    with app.app_context():
        logger.info("\n" + "="*60)
        logger.info("CREATE VIEWER ACCOUNT")
        logger.info("="*60 + "\n")
        
        # List available students
        students = list_students()
        if not students:
            logger.error("Cannot create viewer account - no students in database")
            logger.info("Please register students first using the face registration system")
            return
        
        # Get student ID to link
        while True:
            try:
                student_id = int(input("\nEnter Student ID to link to viewer account: "))
                student = Student.query.get(student_id)
                if student:
                    break
                else:
                    logger.error(f"Student ID {student_id} not found. Please try again.")
            except ValueError:
                logger.error("Please enter a valid number")
        
        logger.info(f"\nSelected Student: {student.name} ({student.student_code})")
        
        # Check if student already has a user account
        existing_user = User.query.filter_by(student_id=student_id).first()
        if existing_user:
            logger.warning(f"⚠️  Student already has an account: {existing_user.username}")
            overwrite = input("Do you want to create a new account anyway? (yes/no): ").lower()
            if overwrite != 'yes':
                logger.info("Account creation cancelled")
                return
        
        # Get username
        while True:
            username = input(f"\nEnter username (suggestion: {student.student_code.lower()}): ").strip()
            if not username:
                logger.error("Username cannot be empty")
                continue
            
            # Check if username exists
            if User.query.filter_by(username=username).first():
                logger.error(f"Username '{username}' already exists. Please choose another.")
                continue
            
            break
        
        # Get email
        while True:
            default_email = f"{username}@student.edu"
            email_input = input(f"Enter email (press Enter for {default_email}): ").strip()
            email = email_input if email_input else default_email
            
            # Check if email exists
            if User.query.filter_by(email=email).first():
                logger.error(f"Email '{email}' already exists. Please choose another.")
                continue
            
            break
        
        # Get password
        while True:
            password = input("Enter password (min 6 characters): ").strip()
            if len(password) < 6:
                logger.error("Password must be at least 6 characters")
                continue
            
            password_confirm = input("Confirm password: ").strip()
            if password != password_confirm:
                logger.error("Passwords do not match. Please try again.")
                continue
            
            break
        
        # Create viewer account
        try:
            viewer = User(
                username=username,
                password_hash=generate_password_hash(password),
                email=email,
                role='viewer',
                student_id=student_id,
                is_active=True
            )
            
            db.session.add(viewer)
            db.session.commit()
            
            logger.info("\n" + "="*60)
            logger.info("✅ VIEWER ACCOUNT CREATED SUCCESSFULLY!")
            logger.info("="*60)
            logger.info(f"Username: {username}")
            logger.info(f"Email: {email}")
            logger.info(f"Role: viewer")
            logger.info(f"Linked to: {student.name} ({student.student_code})")
            logger.info(f"Department: {student.department}")
            logger.info("="*60)
            logger.info("\n📝 The viewer can now login at: http://localhost:5001/login")
            logger.info(f"   Username: {username}")
            logger.info(f"   Password: [the password you entered]")
            logger.info("\n🔍 The viewer will have access to:")
            logger.info("   - View their own attendance records")
            logger.info("   - Update their contact information (phone, email)")
            logger.info("   - View their student profile")
            logger.info("\n🚫 The viewer CANNOT:")
            logger.info("   - Modify attendance records")
            logger.info("   - Access other students' data")
            logger.info("   - Change system settings")
            logger.info("   - Delete or add students")
            logger.info("="*60 + "\n")
            
        except Exception as e:
            logger.error(f"❌ Error creating viewer account: {e}")
            import traceback
            logger.error(traceback.format_exc())
            db.session.rollback()

if __name__ == '__main__':
    create_viewer_account()
