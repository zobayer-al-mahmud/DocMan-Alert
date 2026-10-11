"""
Read-only diagnostic: lists any document that has more than one
DocumentVersion row with the same version_no (which the new unique
constraint in add_performance_indexes.py would otherwise reject).

Only needed if add_performance_indexes.py printed a warning about this.
Makes no changes to the database.

Run:
    venv\\Scripts\\python.exe scripts\\find_duplicate_versions.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from app.models import DocumentVersion
from sqlalchemy import func


def main():
    app = create_app()
    with app.app_context():
        duplicates = (
            db.session.query(DocumentVersion.document_id, DocumentVersion.version_no, func.count())
            .group_by(DocumentVersion.document_id, DocumentVersion.version_no)
            .having(func.count() > 1)
            .all()
        )

        if not duplicates:
            print("No duplicate (document_id, version_no) pairs found. Safe to re-run the index migration.")
            return

        print("Found duplicate version numbers for these documents:")
        for document_id, version_no, count in duplicates:
            print(f"  document_id={document_id}, version_no={version_no}, occurrences={count}")
            versions = DocumentVersion.query.filter_by(
                document_id=document_id, version_no=version_no
            ).order_by(DocumentVersion.uploaded_at).all()
            for v in versions:
                print(f"    - version id={v.id}, file={v.file_path}, uploaded_at={v.uploaded_at}")
        print()
        print("To fix: manually renumber the newer duplicate(s) to the next free "
              "version number for that document, then re-run add_performance_indexes.py.")


if __name__ == "__main__":
    main()
