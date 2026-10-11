from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user

from app import db
from app.forms import LoginForm
from app.models import User
from app.utils import log_event

auth_bp = Blueprint("auth", __name__)

MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 10


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()

    if form.validate_on_submit():
        username = form.username.data.strip()
        user = User.query.filter_by(username=username).first()
        now = datetime.utcnow()

        # Persistent lockout check (survives restarts).
        if user and user.locked_until and user.locked_until > now:
            wait_minutes = max(1, int((user.locked_until - now).total_seconds() / 60) + 1)
            flash(
                f"Too many failed login attempts for this account. "
                f"Please wait about {wait_minutes} minute(s) and try again.",
                "danger",
            )
            log_event("ACCOUNT_SECURITY_EVENT",
                      f"Login attempt on locked account '{username}'.",
                      level="WARNING", username_or_process=username)
            return render_template("login.html", form=form)

        if user and user.check_password(form.password.data) and user.is_active:
            user.failed_login_attempts = 0
            user.locked_until = None
            db.session.commit()
            login_user(user)
            log_event("LOGIN_SUCCESS", f"User '{username}' logged in.",
                      username_or_process=username)
            flash("Login successful.", "success")
            return redirect(url_for("main.dashboard"))

        # Failure path.
        if user:
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= MAX_ATTEMPTS:
                user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                user.failed_login_attempts = 0
                log_event("ACCOUNT_SECURITY_EVENT",
                          f"Account '{username}' locked for {LOCKOUT_MINUTES} minutes after repeated failures.",
                          level="WARNING", username_or_process=username)
            db.session.commit()

        log_event("LOGIN_FAILURE", f"Failed login for '{username}'.",
                  level="WARNING", username_or_process=username)
        flash("Invalid username or password.", "danger")

    return render_template("login.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    username = current_user.username
    logout_user()
    log_event("LOGIN_SUCCESS", f"User '{username}' logged out.", username_or_process=username)
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
