"""
One-time fix: normalizes any DocumentVersion.file_path values that contain
a Windows backslash (\\) into forward-slash form (/).

Why this is needed: the earlier upload-folder migration (and, before this
fix, new document uploads too) built the stored relative path using
os.path.join(), which produces backslash-separated paths on Windows
(e.g. "factory_2\\doc20_v1_file.pdf"). Flask's send_from_directory()
explicitly rejects any path containing a backslash on Windows as a
security measure, which is why View/Download were returning "404 Not
Found" even though the actual file was sitting in the right folder.

This script does NOT move, rename, or touch any file on disk — the files
are already in the correct place. It only fixes the stored path string so
the application can find them again. Safe to run multiple times.

Run this ONCE after deploying the updated code:
    venv\\Scripts\\python.exe scripts\\fix_backslash_paths.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from app.models import DocumentVersion


def main():
    app = create_app()
    with app.app_context():
        versions = DocumentVersion.query.all()

        fixed = 0
        already_ok = 0
        missing_file = 0

        upload_folder = app.config["UPLOAD_FOLDER"]

        for version in versions:
            if "\\" not in version.file_path:
                already_ok += 1
                continue

            corrected = version.file_path.replace("\\", "/")

            # Verify the file actually exists at the corrected path before
            # committing the fix, using the OS-native join for the disk check.
            disk_check_path = os.path.join(upload_folder, *corrected.split("/"))
            if not os.path.exists(disk_check_path):
                print(f"WARNING: version {version.id} — expected file not found at "
                      f"{disk_check_path}. Left file_path unchanged: {version.file_path}")
                missing_file += 1
                continue

            print(f"Fixing version {version.id}: {version.file_path!r} -> {corrected!r}")
            version.file_path = corrected
            fixed += 1

        db.session.commit()

        print()
        print("=" * 60)
        print(f"Fixed (backslash -> forward-slash):  {fixed}")
        print(f"Already correct:                     {already_ok}")
        print(f"Missing file (left unchanged):        {missing_file}")
        print("=" * 60)
        if missing_file == 0:
            print("All file paths are now correct. View/Download should work for every document.")
        else:
            print("Some versions could not be verified on disk — check the warnings above.")


if __name__ == "__main__":
    main()
