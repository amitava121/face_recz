#!/usr/bin/env python3
"""
Fix orphaned viewer accounts by creating student records for them
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app.app import app, db
from src.models.db import User, Student

def fix_orphaned_viewers():
    """Create student records for viewer accounts that don't have one"""
    with app.app_context():
        # Find orphaned viewer accounts
        orphaned_viewers = User.query.filter_by(role='viewer', student_id=None).all()
        
        if not orphaned_viewers:
            print("✅ No orphaned viewer accounts found!")
            return
        
        print(f"Found {len(orphaned_viewers)} orphaned viewer accounts:")
        print()
        
        for viewer in orphaned_viewers:
            print(f"Viewer: {viewer.username} ({viewer.email})")
            
            # Check if there's already a student with this code
            existing_student = Student.query.filter_by(student_code=viewer.username.upper()).first()
            if not existing_student:
                existing_student = Student.query.filter_by(student_code=viewer.username).first()
            
            if existing_student:
                # Link to existing student
                print(f"  → Linking to existing student: {existing_student.name} (ID: {existing_student.id})")
                viewer.student_id = existing_student.id
            else:
                # Create a new student record
                response = input(f"  Create student record for {viewer.username}? (y/n): ")
                if response.lower() == 'y':
                    name = input(f"    Enter student name: ").strip()
                    department = input(f"    Enter department: ").strip()
                    phone = input(f"    Enter phone number (10 digits): ").strip()
                    
                    if len(phone) != 10 or not phone.isdigit():
                        print(f"  ❌ Invalid phone number. Skipping...")
                        continue
                    
                    new_student = Student(
                        student_code=viewer.username.upper(),
                        name=name,
                        department=department,
                        phone_number=phone
                    )
                    db.session.add(new_student)
                    db.session.flush()
                    
                    viewer.student_id = new_student.id
                    print(f"  ✅ Created student and linked: {name} (ID: {new_student.id})")
                else:
                    print(f"  ⏭️  Skipped")
            
            print()
        
        try:
            db.session.commit()
            print("✅ All changes saved successfully!")
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error saving changes: {e}")

if __name__ == '__main__':
    fix_orphaned_viewers()
