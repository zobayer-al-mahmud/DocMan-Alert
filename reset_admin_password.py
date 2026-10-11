"""
Emergency Super Admin password reset script.

SECURITY NOTE: Unlike the old version of this script, the new password is
NEVER written in this file. It is typed in at run time (hidden, like a
terminal sudo prompt) and only ever touches memory + the database.

Usage (from the project folder, with venv activated):
    python reset_admin_password.py
"""
import getpass
import re

from app import create_app, db
from app.models import User

MIN_LENGTH = 10


def password_is_reasonable(pw: str) -> str | None:
    """Return an error message if the password is too weak, else None."""
    if len(pw) < MIN_LENGTH:
        return f"Password must be at least {MIN_LENGTH} characters."
    if not re.search(r"[A-Z]", pw):
        return "Password must contain at least one uppercase letter."
    if not re.search(r"[a-z]", pw):
        return "Password must contain at least one lowercase letter."
    if not re.search(r"[0-9]", pw):
        return "Password must contain at least one digit."
    return None


def main():
    app = create_app()

    with app.app_context():
        admin_user = User.query.filter_by(username="admin").first()

        if not admin_user:
            print("Admin user not found. Nothing to do.")
            return

        print("=== Super Admin Password Reset ===")
        print(f"Target account: {admin_user.username} ({admin_user.full_name})")
        print("The password you type will NOT be shown on screen.\n")

        while True:
            new_password = getpass.getpass("New password: ")
            error = password_is_reasonable(new_password)
            if error:
                print(f"  Rejected: {error}\n")
                continue

            confirm_password = getpass.getpass("Confirm new password: ")
            if new_password != confirm_password:
                print("  Passwords did not match. Try again.\n")
                continue

            break

        confirm = input(
            f"\nType YES to permanently reset the password for '{admin_user.username}': "
        )
        if confirm.strip() != "YES":
            print("Aborted. Password was NOT changed.")
            return

        admin_user.set_password(new_password)
        db.session.commit()
        print("\nSuper Admin password has been reset successfully.")
        print("Store the new password in your organization's password manager")
        print("(not in this script, not in a README, not in plain text anywhere).")


if __name__ == "__main__":
    main()
