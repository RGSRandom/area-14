from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, PasswordField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Length
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
import json
import os
from datetime import datetime, timedelta
from functools import wraps
import time

app = Flask(__name__)
app.secret_key = "a14mail_secret_key_change_this_in_production_12345"

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
    WTF_CSRF_ENABLED=True,
)

csrf = CSRFProtect(app)

login_attempts = {}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

USERS_FILE = os.path.join(BASE_DIR, "users.json")
EMAILS_FILE = os.path.join(BASE_DIR, "emails.json")
AUDIT_LOG = os.path.join(BASE_DIR, "audit.log")
ENCRYPTION_KEY_FILE = os.path.join(BASE_DIR, "secret.key")

# ========== Encryption ==========
def load_or_create_key():
    if not os.path.exists(ENCRYPTION_KEY_FILE):
        key = Fernet.generate_key()
        with open(ENCRYPTION_KEY_FILE, "wb") as f:
            f.write(key)
        return key
    with open(ENCRYPTION_KEY_FILE, "rb") as f:
        return f.read()

fernet = Fernet(load_or_create_key())

def encrypt_text(text: str) -> str:
    return fernet.encrypt(text.encode()).decode()

def decrypt_text(token: str) -> str:
    try:
        return fernet.decrypt(token.encode()).decode()
    except Exception:
        return "[Decryption failed]"

# ========== Helpers ==========

def load_emails():
    if not os.path.exists(EMAILS_FILE):
        return []
    with open(EMAILS_FILE, "r") as f:
        return json.load(f)

def save_emails(emails):
    with open(EMAILS_FILE, "w") as f:
        json.dump(emails, f, indent=4)

def load_users():
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=4)

def find_user(email):
    email = email.lower().strip()
    for user in load_users():
        if user["email"].lower() == email:
            return user
    return None

def audit(action: str, detail: str = ""):
    user = session.get("email", "anonymous")
    entry = f"{datetime.now().isoformat()} | {user} | {action} | {detail}\n"
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(entry)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "email" not in session:
            flash("Please log in first")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        role = session.get("role")
        if role not in ["admin", "super_admin"]:
            flash("Admin access required")
            return redirect(url_for("inbox"))
        return f(*args, **kwargs)
    return decorated

def super_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "super_admin":
            flash("Super Admin access required")
            return redirect(url_for("inbox"))
        return f(*args, **kwargs)
    return decorated

def raisa_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") not in ["raisa", "super_admin"]:
            flash("RAISA or Super Admin access required")
            return redirect(url_for("inbox"))
        return f(*args, **kwargs)
    return decorated

# ========== Forms ==========
class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Length(max=120)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=128)])
    submit = SubmitField("Login")

class ComposeForm(FlaskForm):
    to = StringField("To", validators=[DataRequired(), Length(max=500)])
    subject = StringField("Subject", validators=[DataRequired(), Length(max=200)])
    body = TextAreaField("Message", validators=[DataRequired(), Length(max=10000)])
    submit = SubmitField("Send Email")

# ========== Routes ==========
@app.route("/")
def home():
    if "email" in session:
        return redirect(url_for("inbox"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if "email" in session:
        return redirect(url_for("inbox"))

    form = LoginForm()
    client_ip = request.remote_addr or "unknown"

    attempts = login_attempts.get(client_ip, {"count": 0, "last": 0})
    if attempts["count"] >= 7 and time.time() - attempts["last"] < 300:
        flash("Too many failed attempts. Wait 5 minutes.")
        return render_template("login.html", form=form)

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        password = form.password.data

        user = find_user(email)

        if user and check_password_hash(user["password"], password):
            session.clear()
            session["email"] = user["email"]
            session["name"] = user.get("name", user["email"])
            session["role"] = user.get("role", "user")
            session.permanent = True

            login_attempts[client_ip] = {"count": 0, "last": time.time()}
            audit("LOGIN_SUCCESS", f"from {client_ip}")
            flash("Login successful")
            return redirect(url_for("inbox"))
        else:
            attempts["count"] += 1
            attempts["last"] = time.time()
            login_attempts[client_ip] = attempts
            audit("LOGIN_FAILED", f"email={email} from {client_ip}")
            flash("Wrong email or password")

    return render_template("login.html", form=form)

@app.route("/logout")
@login_required
def logout():
    audit("LOGOUT")
    session.clear()
    return redirect(url_for("login"))

@app.route("/inbox")
@login_required
def inbox():
    emails = load_emails()
    my_emails = []
    
    if session.get("role") in ["admin", "super_admin"]:
        for e in reversed(emails):
            my_emails.append({
                "id": e["id"],
                "from": e["from"],
                "subject": decrypt_text(e["subject"]),
                "timestamp": e["timestamp"],
                "read_by": e.get("read_by", []),
                "clearance": e.get("clearance", "cl0")
            })
    else:
        for e in reversed(emails):
            if session["email"] in e.get("to", []):
                my_emails.append({
                    "id": e["id"],
                    "from": e["from"],
                    "subject": decrypt_text(e["subject"]),
                    "timestamp": e["timestamp"],
                    "read_by": e.get("read_by", []),
                    "clearance": e.get("clearance", "cl0")
                })
    return render_template("inbox.html", emails=my_emails, user=session["name"], role=session.get("role"))

@app.route("/sent")
@login_required
def sent():
    emails = load_emails()
    my_emails = []
    for e in reversed(emails):
        if e.get("from") == session["email"]:
            my_emails.append({
                "id": e["id"],
                "to": e.get("to", []),
                "subject": decrypt_text(e["subject"]),
                "timestamp": e["timestamp"],
                "clearance": e.get("clearance", "cl0")
            })
    return render_template("sent.html", emails=my_emails, user=session["name"], role=session.get("role"))

@app.route("/compose", methods=["GET", "POST"])
@login_required
def compose():
    form = ComposeForm()
    if form.validate_on_submit():
        to_raw = form.to.data.strip()
        subject = form.subject.data.strip()
        body = form.body.data.strip()

        to_list = [addr.strip().lower() for addr in to_raw.split(",") if addr.strip()]

        for receiver in to_list:
            if not find_user(receiver):
                flash(f"User does not exist: {receiver}")
                return render_template("compose.html", form=form, user=session["name"], role=session.get("role"))

        emails = load_emails()
        new_id = 1 if not emails else max(e["id"] for e in emails) + 1

        new_email = {
            "id": new_id,
            "from": session["email"],
            "to": to_list,
            "subject": encrypt_text(subject),
            "body": encrypt_text(body),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "read_by": [],
            "clearance": "cl0"
        }

        emails.append(new_email)
        save_emails(emails)
        audit("EMAIL_SENT", f"to={to_list}")
        flash("Email sent successfully!")
        return redirect(url_for("inbox"))

    return render_template("compose.html", form=form, user=session["name"], role=session.get("role"))

@app.route("/view/<int:email_id>")
@login_required
def view(email_id):
    emails = load_emails()
    email = next((e for e in emails if e["id"] == email_id), None)

    if not email:
        return "Email not found", 404

    if session.get("role") not in ["admin", "super_admin"]:
        if session["email"] != email["from"] and session["email"] not in email.get("to", []):
            audit("UNAUTHORIZED_VIEW_ATTEMPT", f"email_id={email_id}")
            return "Not allowed", 403

    if session["email"] in email.get("to", []) and session["email"] not in email.get("read_by", []):
        email.setdefault("read_by", []).append(session["email"])
        save_emails(emails)

    decrypted = {
        "id": email["id"],
        "from": email["from"],
        "to": email.get("to", []),
        "subject": decrypt_text(email["subject"]),
        "body": decrypt_text(email["body"]),
        "timestamp": email["timestamp"],
        "clearance": email.get("clearance", "cl0")
    }

    audit("EMAIL_VIEWED", f"email_id={email_id}")
    return render_template("view.html", email=decrypted, user=session["name"], role=session.get("role"))

# ========== ADMIN / MANAGEMENT ROUTES ==========

@app.route("/admin/users")
@login_required
@admin_required
def admin_users():
    users = load_users()
    current_role = session.get("role")
    safe_users = []
    
    for u in users:
        user_role = u.get("role", "user")
        if current_role == "super_admin" or (current_role == "admin" and user_role == "user"):
            safe_users.append({
                "email": u["email"],
                "name": u.get("name", ""),
                "role": user_role
            })
    
    return render_template("admin_users.html", users=safe_users, user=session["name"], current_role=current_role)

@app.route("/admin/change-password", methods=["POST"])
@login_required
@admin_required
def admin_change_password():
    target_email = request.form.get("email", "").strip().lower()
    new_password = request.form.get("new_password", "").strip()
    
    current_role = session.get("role")
    audit("PASSWORD_CHANGE_ATTEMPT", f"by_role={current_role} target={target_email}")

    if not target_email or not new_password:
        flash("Email and new password are required")
        return redirect(url_for("admin_users"))

    if len(new_password) < 4:
        flash("Password is too short (minimum 4 characters)")
        return redirect(url_for("admin_users"))

    users = load_users()
    target_user = find_user(target_email)

    if not target_user:
        flash("User not found")
        return redirect(url_for("admin_users"))

    target_role = target_user.get("role", "user")

    if current_role == "admin" and target_role != "user":
        flash(f"Admins can only change passwords for regular users, not {target_role}s")
        audit("PASSWORD_CHANGE_DENIED", f"by={session['email']} target={target_email}")
        return redirect(url_for("admin_users"))

    for user in users:
        if user["email"].lower() == target_email:
            user["password"] = generate_password_hash(new_password)
            break

    save_users(users)
    audit("PASSWORD_CHANGED_BY_ADMIN", f"changer_role={current_role} target={target_email}")
    flash(f"Password for {target_email} changed successfully")
    return redirect(url_for("admin_users"))

@app.route("/admin/add-user", methods=["POST"])
@login_required
@admin_required
def admin_add_user():
    current_role = session.get("role")
    
    if current_role == "admin":
        new_role = "user"
    elif current_role == "super_admin":
        new_role = request.form.get("role", "user").strip().lower()
        if new_role not in ["user", "admin", "super_admin", "raisa"]:
            new_role = "user"
    else:
        flash("Insufficient privileges")
        return redirect(url_for("admin_users"))

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "").strip()
    name = request.form.get("name", "").strip()

    if not email or not password or not name:
        flash("Email, password, and name are required")
        return redirect(url_for("admin_users"))

    if len(password) < 4:
        flash("Password must be at least 4 characters")
        return redirect(url_for("admin_users"))

    users = load_users()
    
    if find_user(email):
        flash(f"User {email} already exists")
        return redirect(url_for("admin_users"))

    new_user = {
        "email": email,
        "password": generate_password_hash(password),
        "name": name,
        "role": new_role
    }

    users.append(new_user)
    save_users(users)
    audit("USER_ADDED", f"by_role={current_role} new_user={email} role={new_role}")
    flash(f"User {email} added successfully with role '{new_role}'")
    return redirect(url_for("admin_users"))

@app.route("/admin/remove-user", methods=["POST"])
@login_required
@admin_required
def admin_remove_user():
    current_role = session.get("role")
    target_email = request.form.get("email", "").strip().lower()

    if not target_email:
        flash("Email is required")
        return redirect(url_for("admin_users"))

    if target_email == session["email"]:
        flash("You cannot delete your own account")
        return redirect(url_for("admin_users"))

    users = load_users()
    target_user = find_user(target_email)

    if not target_user:
        flash("User not found")
        return redirect(url_for("admin_users"))

    target_role = target_user.get("role", "user")

    if current_role == "admin" and target_role != "user":
        flash(f"Admins can only remove regular users")
        return redirect(url_for("admin_users"))
    elif current_role == "super_admin" and target_role == "super_admin":
        flash("Super Admins cannot remove other Super Admins")
        return redirect(url_for("admin_users"))

    users = [u for u in users if u["email"].lower() != target_email]
    save_users(users)
    audit("USER_REMOVED", f"by_role={current_role} target={target_email}")
    flash(f"User {target_email} removed successfully")
    return redirect(url_for("admin_users"))

@app.route("/admin/edit-user", methods=["POST"])
@login_required
@admin_required
def admin_edit_user():
    current_role = session.get("role")
    target_email = request.form.get("email", "").strip().lower()
    new_name = request.form.get("name", "").strip()
    new_role = request.form.get("role", "").strip().lower()
    new_email = request.form.get("new_email", "").strip().lower()

    if not target_email:
        flash("Email is required")
        return redirect(url_for("admin_users"))

    users = load_users()
    target_user = find_user(target_email)

    if not target_user:
        flash("User not found")
        return redirect(url_for("admin_users"))

    target_role = target_user.get("role", "user")

    if current_role == "admin" and target_role != "user":
        flash("Admins can only edit regular users")
        return redirect(url_for("admin_users"))

    for user in users:
        if user["email"].lower() == target_email:
            if new_name:
                user["name"] = new_name
            if new_email and new_email != target_email:
                if find_user(new_email):
                    flash("New email already exists")
                    return redirect(url_for("admin_users"))
                user["email"] = new_email
            if new_role and current_role == "super_admin":
                if new_role in ["user", "admin", "super_admin", "raisa"]:
                    user["role"] = new_role
            break

    save_users(users)
    audit("USER_EDITED", f"by_role={current_role} target={target_email}")
    flash("User information updated successfully")
    return redirect(url_for("admin_users"))

# ========== CLEARANCE MANAGEMENT ==========

@app.route("/admin/set-clearance/<int:email_id>", methods=["POST"])
@login_required
@admin_required
def admin_set_clearance(email_id):
    clearance = request.form.get("clearance", "cl0").strip().lower()
    valid_clearances = ["cl0", "cl1", "cl2", "cl3", "cl4"]
    
    if clearance not in valid_clearances:
        return jsonify({"error": "Invalid clearance level"}), 400

    emails = load_emails()
    email = next((e for e in emails if e["id"] == email_id), None)

    if not email:
        return jsonify({"error": "Email not found"}), 404

    email["clearance"] = clearance
    save_emails(emails)
    audit("CLEARANCE_SET", f"email_id={email_id} clearance={clearance}")
    return jsonify({"success": True, "clearance": clearance})

@app.route("/raisa/change-clearance/<int:email_id>", methods=["POST"])
@login_required
@raisa_required
def raisa_change_clearance(email_id):
    clearance = request.form.get("clearance", "cl0").strip().lower()
    valid_clearances = ["cl0", "cl1", "cl2", "cl3", "cl4"]
    
    if clearance not in valid_clearances:
        flash("Invalid clearance level")
        return redirect(url_for("inbox"))

    emails = load_emails()
    email = next((e for e in emails if e["id"] == email_id), None)

    if not email:
        flash("Email not found")
        return redirect(url_for("inbox"))

    old_clearance = email.get("clearance", "cl0")
    email["clearance"] = clearance
    save_emails(emails)
    audit("CLEARANCE_CHANGED_BY_RAISA", f"email_id={email_id} old={old_clearance} new={clearance}")
    flash(f"Clearance updated from {old_clearance.upper()} to {clearance.upper()}")
    return redirect(url_for("inbox"))

# ========== PROFILE ==========

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        new_name = request.form.get("name", "").strip()
        new_password = request.form.get("new_password", "").strip()
        new_email = request.form.get("email", "").strip().lower()

        users = load_users()
        current_user = next((u for u in users if u["email"].lower() == session["email"]), None)

        if not current_user:
            flash("User not found")
            return redirect(url_for("inbox"))

        if new_name:
            current_user["name"] = new_name
            session["name"] = new_name

        if new_email and new_email != session["email"]:
            if find_user(new_email):
                flash("Email already in use")
                return redirect(url_for("profile"))
            current_user["email"] = new_email
            session["email"] = new_email

        if new_password:
            if len(new_password) < 4:
                flash("Password must be at least 4 characters")
                return redirect(url_for("profile"))
            current_user["password"] = generate_password_hash(new_password)

        save_users(users)
        audit("PROFILE_UPDATED", f"email={session['email']}")
        flash("Profile updated successfully")
        return redirect(url_for("profile"))

    user = find_user(session["email"])
    return render_template("profile.html", user=user, session_role=session.get("role"))

if __name__ == "__main__":
    app.run(debug=True)