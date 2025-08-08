from app import create_app, db
from models import Branch
from datetime import datetime

def list_and_cleanup_soft_deleted_branches():
    app = create_app()
    with app.app_context():
        # Query soft deleted branches
        soft_deleted_branches = Branch.query.filter(Branch.deleted_at.isnot(None)).all()

        if not soft_deleted_branches:
            print("No soft deleted branches found.")
            return

        print(f"Found {len(soft_deleted_branches)} soft deleted branches:")
        for branch in soft_deleted_branches:
            print(f"ID: {branch.id} | Name: {branch.name} | Deleted At: {branch.deleted_at}")

        confirm = input("Do you want to permanently delete these branches? (yes/no): ").strip().lower()
        if confirm == 'yes':
            for branch in soft_deleted_branches:
                db.session.delete(branch)
            db.session.commit()
            print(f"Permanently deleted {len(soft_deleted_branches)} branches.")
        else:
            print("No branches were deleted.")

if __name__ == "__main__":
    list_and_cleanup_soft_deleted_branches()
