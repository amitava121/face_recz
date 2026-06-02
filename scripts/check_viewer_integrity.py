#!/usr/bin/env python3
"""
Check database integrity for viewer accounts
Detects and reports orphaned viewer accounts
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app.app import app, db
from src.models.db import User, Student

def check_viewer_integrity():
    """Check all viewer accounts for proper student linkage"""
    with app.app_context():
        print("\n" + "="*70)
        print("VIEWER ACCOUNT INTEGRITY CHECK")
        print("="*70 + "\n")
        
        viewers = User.query.filter_by(role='viewer').all()
        print(f"Total viewer accounts: {len(viewers)}")
        
        # Check for orphaned accounts
        orphaned = []
        linked = []
        broken = []
        
        for viewer in viewers:
            if viewer.student_id is None:
                orphaned.append(viewer)
            elif viewer.student is None:
                broken.append(viewer)
            else:
                linked.append(viewer)
        
        print(f"\n✅ Properly linked: {len(linked)}")
        print(f"⚠️  Orphaned (no student_id): {len(orphaned)}")
        print(f"❌ Broken (student_id set but student not found): {len(broken)}")
        
        if linked:
            print("\n" + "-"*70)
            print("PROPERLY LINKED ACCOUNTS:")
            print("-"*70)
            for v in linked:
                print(f"  {v.username:15} → {v.student.name:20} ({v.student.student_code})")
        
        if orphaned:
            print("\n" + "-"*70)
            print("⚠️  ORPHANED ACCOUNTS (Need Manual Fix):")
            print("-"*70)
            for v in orphaned:
                print(f"  Username: {v.username}")
                print(f"  Email: {v.email}")
                print(f"  Created: {v.created_at}")
                print()
        
        if broken:
            print("\n" + "-"*70)
            print("❌ BROKEN ACCOUNTS (Database Corruption):")
            print("-"*70)
            for v in broken:
                print(f"  Username: {v.username}")
                print(f"  student_id: {v.student_id} (MISSING)")
                print()
        
        # Summary
        print("\n" + "="*70)
        if not orphaned and not broken:
            print("✅ All viewer accounts are properly linked!")
        else:
            print("⚠️  Issues detected. Run fix_orphaned_viewers.py to resolve.")
        print("="*70 + "\n")
        
        return len(orphaned) == 0 and len(broken) == 0

if __name__ == '__main__':
    success = check_viewer_integrity()
    sys.exit(0 if success else 1)
