"""
HBX Marketing - PRO Licensing Server Backend & Admin Web Dashboard
-------------------------------------------------------------------
Version: 2.0.0 (Upgraded Premium Edition)
Author: HBX Marketing / SRF Team
Description: Fully modularized, highly secure, premium dark-themed license management suite.
             Supports multiple admin accounts, CSV bulk actions, detailed audit logging,
             Chart.js analytics, database snapshots, rate-limiting, and CSRF protection.
"""

import os
import sys
import uuid
import psycopg2
import psycopg2.extras
from psycopg2 import sql
import os
import hashlib
import time
import csv
import shutil
import secrets
from datetime import datetime, timedelta
from collections import defaultdict

# --- Automated Dependency Installer ---
try:
    from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash, send_file
    from werkzeug.security import generate_password_hash, check_password_hash
    from werkzeug.utils import secure_filename
except ImportError:
    print("[SYSTEM] Dependencies not found. Installing now...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "Flask"], check=True)
    from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash, send_file
    from werkzeug.security import generate_password_hash, check_password_hash
    from werkzeug.utils import secure_filename

# --- Configuration ---
DB_DIR = "database"
DB_FILE = os.path.join(DB_DIR, "licenses.db")
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASS = "AAfifaAfi128"  # Legacy plain password, hashed on database write

app = Flask(__name__)

# --- Directory Initialization ---
def init_directories():
    os.makedirs(DB_DIR, exist_ok=True)
    os.makedirs("backups", exist_ok=True)
    os.makedirs("exports", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    os.makedirs(os.path.join("static", "css"), exist_ok=True)
    os.makedirs(os.path.join("static", "js"), exist_ok=True)
    os.makedirs(os.path.join("static", "img"), exist_ok=True)

init_directories()

# --- Legacy DB Migration Check ---


# --- Database Core Setup ---
# --- Postgres DB Wrapper ---
class PostgresCursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, query, params=None):
        # Convert ? to %s
        query = query.replace('?', '%s')
        if params:
            self.cursor.execute(query, params)
        else:
            self.cursor.execute(query)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    @property
    def rowcount(self):
        return self.cursor.rowcount

class PostgresDBWrapper:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, query, params=None):
        cursor = self.conn.cursor()
        query = query.replace('?', '%s')
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        return PostgresCursorWrapper(cursor)

    def commit(self):
        self.conn.commit()

    def cursor(self):
        return PostgresCursorWrapper(self.conn.cursor())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
        self.conn.close()

def get_db():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("[WARNING] DATABASE_URL not set. Please set it to a PostgreSQL connection string.")
        db_url = "postgresql://postgres:password@localhost:5432/licenses"
    conn = psycopg2.connect(db_url)
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return PostgresDBWrapper(conn)

def hash_sha256(data: str) -> str:
    return hashlib.sha256(data.encode('utf-8')).hexdigest()

def verify_password(stored_hash: str, password: str) -> bool:
    # 1. Check legacy plain SHA256 hash first
    legacy = hash_sha256(password)
    if stored_hash == legacy:
        return True
    # 2. Check Werkzeug password hash
    try:
        return check_password_hash(stored_hash, password)
    except Exception:
        return False

def init_db():
    with get_db() as conn:
        # Create licenses table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS licenses (
                id SERIAL PRIMARY KEY,
                key_string TEXT UNIQUE NOT NULL,
                hwid TEXT,
                expires_at TEXT NOT NULL, -- YYYY-MM-DD HH:MM:SS
                is_active INTEGER DEFAULT 1,
                user_note TEXT,
                created_at TEXT NOT NULL,
                last_activated_at TEXT
            )
        """)
        
        # Verify schema upgrades for legacy licenses table
        cursor = conn.cursor()
        
        # Note: Postgres schema migration should ideally be handled via a migration tool (like Alembic).
        # We will assume columns exist for fresh installs.
                     

        # Create admin table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_at TEXT
            )
        """)

        # Create settings table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Create activity_logs table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_logs (
                id SERIAL PRIMARY KEY,
                username TEXT,
                action TEXT NOT NULL,
                ip_address TEXT,
                created_at TEXT NOT NULL
            )
        """)

        # Populate default admin
        cursor.execute("SELECT COUNT(*) FROM admins")
        if cursor.fetchone()[0] == 0:
            legacy_hash = hash_sha256(DEFAULT_ADMIN_PASS)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("INSERT INTO admins (username, password_hash, created_at) VALUES (?, ?, ?)",
                         (DEFAULT_ADMIN_USER, legacy_hash, now_str))
            print(f"[DATABASE] Default admin account set (User: {DEFAULT_ADMIN_USER} | Pass: {DEFAULT_ADMIN_PASS})")

        # Populate default settings
        default_settings = {
            "dashboard_title": "SRF License Manager",
            "dashboard_logo": "🔐",
            "api_key": secrets.token_hex(32)
        }
        for k, v in default_settings.items():
            cursor.execute("SELECT COUNT(*) FROM settings WHERE key = ?", (k,))
            if cursor.fetchone()[0] == 0:
                conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (k, v))

        conn.commit()

init_db()

# --- Persistent App Secret Key Setup ---
def load_app_secret_key():
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'secret_key'")
            row = cursor.fetchone()
            if row:
                return row["value"]
            else:
                key = secrets.token_hex(24)
                conn.execute("INSERT INTO settings (key, value) VALUES ('secret_key', ?)", (key,))
                conn.commit()
                return key
    except Exception:
        return secrets.token_hex(24)

app.secret_key = load_app_secret_key()

# --- Logging & Security Helpers ---
def log_activity(username, action, ip_address=None):
    if not ip_address:
        # Check if request exists
        try:
            ip_address = request.remote_addr
        except RuntimeError:
            ip_address = "127.0.0.1"
    
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO activity_logs (username, action, ip_address, created_at)
                VALUES (?, ?, ?, ?)
            """, (username, action, ip_address, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
    except Exception as e:
        print(f"[SYSTEM ERROR] Failed to log activity: {e}")

# --- Simple In-Memory Rate Limiting ---
RATE_LIMIT_STORE = defaultdict(list)

def rate_limit(limit=30, window=60):
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def wrapper(*args, **kwargs):
            ip = request.remote_addr
            now = time.time()
            # Prune old logs
            RATE_LIMIT_STORE[ip] = [t for t in RATE_LIMIT_STORE[ip] if now - t < window]
            if len(RATE_LIMIT_STORE[ip]) >= limit:
                log_activity(None, f"Security: Rate limit triggered (IP: {ip})", ip)
                return jsonify({"success": False, "message": "Rate limit exceeded. Please wait and try again."}), 429
            RATE_LIMIT_STORE[ip].append(now)
            return f(*args, **kwargs)
        return wrapper
    return decorator

# --- CSRF Protection Layer ---
@app.before_request
def csrf_protect():
    if request.method == "POST":
        # Disable CSRF checks for client API calls
        if request.path.startswith("/api/"):
            return
        
        csrf_token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not csrf_token or csrf_token != session.get("csrf_token"):
            log_activity(session.get("username"), "CSRF verification failed")
            return "CSRF verification failed. Request blocked.", 400

@app.context_processor
def inject_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return dict(csrf_token=session["csrf_token"])

# --- Custom Jinja Filters & Injections ---
app.jinja_env.filters['min_value'] = min

@app.context_processor
def inject_global_appearance():
    title = "SRF License Manager"
    logo = "🔐"
    api_key = ""
    
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'dashboard_title'")
            r = cursor.fetchone()
            if r: title = r["value"]
            
            cursor.execute("SELECT value FROM settings WHERE key = 'dashboard_logo'")
            r = cursor.fetchone()
            if r: logo = r["value"]
            
            cursor.execute("SELECT value FROM settings WHERE key = 'api_key'")
            r = cursor.fetchone()
            if r: api_key = r["value"]
    except Exception:
        pass
    
    # Check if a custom logo image has been uploaded
    custom_logo_path = None
    logo_file = os.path.join("static", "img", "logo.png")
    if os.path.exists(logo_file):
        # Cache buster using current timestamp
        custom_logo_path = "/static/img/logo.png?v=" + str(int(time.time()))
        
    return {
        "dashboard_title": title,
        "dashboard_logo": logo,
        "custom_logo_path": custom_logo_path,
        "api_key": api_key
    }

# --- Auth Check Decorator ---
def admin_login_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper

# --- UI Views & Authentication ---

@app.route("/", methods=["GET", "POST"])
@rate_limit(limit=15, window=60) # Protect login against brute force
def index():
    if session.get("admin_logged_in"):
        return redirect(url_for("dashboard"))
        
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM admins WHERE username = ?", (username,))
            admin = cursor.fetchone()
            
            if admin and verify_password(admin["password_hash"], password):
                session["admin_logged_in"] = True
                session["username"] = admin["username"]
                
                # Automatically upgrade legacy MD5/SHA256 to modern Werkzeug hash if matched
                if not admin["password_hash"].startswith("pbkdf2:sha256"):
                    modern_hash = generate_password_hash(password)
                    conn.execute("UPDATE admins SET password_hash = ? WHERE username = ?", (modern_hash, username))
                    conn.commit()
                    print(f"[SECURITY] Upgraded legacy password hash format for '{username}'")
                
                log_activity(username, "Login: Admin signed in successfully")
                return redirect(url_for("dashboard"))
                
        log_activity(None, f"Login Failure: Unsuccessful attempt for user '{username}'")
        return render_template("login.html", error="Invalid admin credentials or account")
        
    return render_template("login.html", error=None)

@app.route("/logout")
def logout():
    username = session.get("username", "admin")
    log_activity(username, "Logout: Admin signed out")
    session.pop("admin_logged_in", None)
    session.pop("username", None)
    return redirect(url_for("index"))

@app.route("/dashboard")
@admin_login_required
def dashboard():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Summary metrics
        cursor.execute("SELECT COUNT(*) FROM licenses")
        total = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM licenses WHERE is_active = 1 AND expires_at::timestamp >= %s::timestamp", (now_str,))
        active = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM licenses WHERE expires_at::timestamp < %s::timestamp", (now_str,))
        expired = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM licenses WHERE is_active = 0")
        blocked = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM licenses WHERE hwid IS NOT NULL")
        bound = cursor.fetchone()[0]
        
        # Today's activations
        today_date = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT COUNT(*) FROM activity_logs 
            WHERE action LIKE 'Verification Success%' AND created_at::date = ?
        """, (today_date,))
        today_activations = cursor.fetchone()[0]
        
        # Feed for recent activity logs
        cursor.execute("SELECT * FROM activity_logs ORDER BY id DESC LIMIT 6")
        recent_activity = cursor.fetchall()
        
        # Build Analytics Charts Data (last 7 days)
        daily_labels = []
        daily_data = []
        for i in range(6, -1, -1):
            day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            daily_labels.append((datetime.now() - timedelta(days=i)).strftime("%a %d"))
            cursor.execute("""
                SELECT COUNT(*) FROM activity_logs 
                WHERE action LIKE 'Verification Success%' AND created_at::date = ?
            """, (day,))
            daily_data.append(cursor.fetchone()[0])
            
        # Build Analytics Charts Data (last 6 months)
        monthly_labels = []
        monthly_data = []
        for i in range(5, -1, -1):
            # Midpoint approximation of month start
            target_date = datetime.now() - timedelta(days=i*30)
            month_str = target_date.strftime("%Y-%m")
            monthly_labels.append(target_date.strftime("%b"))
            cursor.execute("""
                SELECT COUNT(*) FROM activity_logs 
                WHERE action LIKE 'Verification Success%' AND to_char(created_at::timestamp, 'YYYY-MM') = ?
            """, (month_str,))
            monthly_data.append(cursor.fetchone()[0])
            
    stats = {
        "total": total,
        "active": active,
        "expired": expired,
        "blocked": blocked,
        "bound": bound,
        "today_activations": today_activations
    }
    
    chart_data = {
        "daily_labels": daily_labels,
        "daily_data": daily_data,
        "monthly_labels": monthly_labels,
        "monthly_data": monthly_data
    }
    
    return render_template("dashboard.html", active_page="dashboard", stats=stats, recent_activity=recent_activity, chart_data=chart_data)

@app.route("/licenses")
@admin_login_required
def licenses_view():
    search_query = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "all")
    sort_filter = request.args.get("sort", "expiry_desc")
    page = int(request.args.get("page", 1))
    per_page = 20
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Construct base query
    query = "SELECT * FROM licenses WHERE 1=1"
    params = []
    
    if search_query:
        query += " AND (key_string LIKE ? OR user_note LIKE ?)"
        params.extend([f"%{search_query}%", f"%{search_query}%"])
        
    if status_filter == "active":
        query += " AND is_active = 1 AND expires_at::timestamp >= %s::timestamp"
        params.append(now_str)
    elif status_filter == "blocked":
        query += " AND is_active = 0"
    elif status_filter == "expired":
        query += " AND expires_at::timestamp < %s::timestamp"
        params.append(now_str)
        
    # Sort filter
    if sort_filter == "expiry_asc":
        query += " ORDER BY datetime(expires_at) ASC"
    elif sort_filter == "expiry_desc":
        query += " ORDER BY datetime(expires_at) DESC"
    elif sort_filter == "created_asc":
        query += " ORDER BY id ASC"
    else:
        query += " ORDER BY id DESC" # default created_desc
        
    # Get total count before pagination limits
    count_query = f"SELECT COUNT(*) FROM ({query})"
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()[0]
        
        # Paginate
        query += " LIMIT ? OFFSET ?"
        offset = (page - 1) * per_page
        params.extend([per_page, offset])
        
        cursor.execute(query, params)
        licenses_raw = cursor.fetchall()
        
    # Process attributes (is_expired helper)
    licenses = []
    for lic in licenses_raw:
        lic_dict = dict(lic)
        expiry_dt = datetime.strptime(lic["expires_at"], "%Y-%m-%d %H:%M:%S")
        lic_dict["is_expired"] = datetime.now() > expiry_dt
        licenses.append(lic_dict)
        
    total_pages = (total_count + per_page - 1) // per_page
    
    return render_template(
        "licenses.html",
        active_page="licenses",
        licenses=licenses,
        search_query=search_query,
        status_filter=status_filter,
        sort_filter=sort_filter,
        page=page,
        per_page=per_page,
        total_count=total_count,
        total_pages=total_pages
    )

# --- License Action Routes ---

@app.route("/licenses/generate", methods=["POST"])
@admin_login_required
def bulk_generate_licenses():
    duration = int(request.form.get("duration", 30))
    quantity = int(request.form.get("quantity", 1))
    custom_key = request.form.get("custom_key", "").strip()
    user_note = request.form.get("user_note", "").strip()
    
    created_keys = []
    with get_db() as conn:
        for i in range(quantity):
            if custom_key:
                key = f"{custom_key}-{i+1}" if quantity > 1 else custom_key
            else:
                key = f"SRF-PRO-{uuid.uuid4().hex[:12].upper()}"
                
            expires_at = (datetime.now() + timedelta(days=duration)).strftime("%Y-%m-%d %H:%M:%S")
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            try:
                conn.execute("INSERT INTO licenses (key_string, expires_at, user_note, created_at) VALUES (?, ?, ?, ?)", 
                             (key, expires_at, user_note, created_at))
                created_keys.append(key)
            except psycopg2.errors.UniqueViolation:
                key = f"{key}-{secrets.token_hex(3).upper()}"
                conn.execute("INSERT INTO licenses (key_string, expires_at, user_note, created_at) VALUES (?, ?, ?, ?)", 
                             (key, expires_at, user_note, created_at))
                created_keys.append(key)
        conn.commit()
        
    log_activity(session["username"], f"Licenses: Bulk generated {quantity} keys")
    flash(f"Successfully generated {quantity} licensing keys!", "success")
    return redirect(url_for("licenses_view"))

@app.route("/licenses/edit", methods=["POST"])
@admin_login_required
def edit_license():
    lic_id = request.form.get("id")
    user_note = request.form.get("user_note", "").strip()
    expires_at = request.form.get("expires_at", "").strip()
    
    # Expiry date validator
    try:
        datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        flash("Failed to update: Invalid expiration date format. Must be 'YYYY-MM-DD HH:MM:SS'.", "danger")
        return redirect(url_for("licenses_view"))
        
    with get_db() as conn:
        conn.execute("UPDATE licenses SET user_note = ?, expires_at = ? WHERE id = ?", (user_note, expires_at, lic_id))
        conn.commit()
        
    log_activity(session["username"], f"Licenses: Updated license ID {lic_id}")
    flash("License key configurations saved!", "success")
    return redirect(url_for("licenses_view"))

@app.route("/licenses/toggle/<int:id>")
@admin_login_required
def action_toggle_status(id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_active, key_string FROM licenses WHERE id = ?", (id,))
        row = cursor.fetchone()
        if row:
            new_status = 0 if row["is_active"] == 1 else 1
            conn.execute("UPDATE licenses SET is_active = ? WHERE id = ?", (new_status, id))
            conn.commit()
            
            action_desc = "Blocked" if new_status == 0 else "Unblocked"
            log_activity(session["username"], f"Licenses: {action_desc} key '{row['key_string']}'")
            flash(f"License is now {action_desc.lower()}!", "success")
            
    return redirect(request.referrer or url_for("licenses_view"))

@app.route("/licenses/reset_hwid/<int:id>")
@admin_login_required
def action_reset_hwid(id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key_string FROM licenses WHERE id = ?", (id,))
        row = cursor.fetchone()
        if row:
            conn.execute("UPDATE licenses SET hwid = NULL WHERE id = ?", (id,))
            conn.commit()
            log_activity(session["username"], f"Licenses: Reset bound HWID for key '{row['key_string']}'")
            flash("Hardware binder reset successfully!", "success")
    return redirect(request.referrer or url_for("licenses_view"))

@app.route("/licenses/delete/<int:id>")
@admin_login_required
def action_delete_key(id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key_string FROM licenses WHERE id = ?", (id,))
        row = cursor.fetchone()
        if row:
            conn.execute("DELETE FROM licenses WHERE id = ?", (id,))
            conn.commit()
            log_activity(session["username"], f"Licenses: Deleted key '{row['key_string']}'")
            flash("License key deleted from records.", "success")
    return redirect(request.referrer or url_for("licenses_view"))

@app.route("/licenses/bulk_delete", methods=["POST"])
@admin_login_required
def bulk_delete_licenses():
    license_ids = request.form.getlist("license_ids")
    if not license_ids:
        flash("No keys selected.", "warning")
        return redirect(url_for("licenses_view"))
        
    with get_db() as conn:
        conn.execute(f"DELETE FROM licenses WHERE id IN ({','.join(['?']*len(license_ids))})", license_ids)
        conn.commit()
        
    log_activity(session["username"], f"Licenses: Bulk deleted {len(license_ids)} keys")
    flash(f"Successfully deleted {len(license_ids)} license keys.", "success")
    return redirect(url_for("licenses_view"))

@app.route("/licenses/export")
@admin_login_required
def export_csv():
    export_path = os.path.join("exports", f"licenses_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key_string, expires_at, hwid, is_active, user_note, created_at FROM licenses")
        rows = cursor.fetchall()
        
    with open(export_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['key_string', 'expires_at', 'hwid', 'is_active', 'user_note', 'created_at'])
        for row in rows:
            writer.writerow(list(row))
            
    log_activity(session["username"], "Exports: Exported licenses database to CSV")
    return send_file(export_path, as_attachment=True)

@app.route("/licenses/import", methods=["POST"])
@admin_login_required
def import_csv():
    if 'csv_file' not in request.files:
        flash("No file upload object found.", "danger")
        return redirect(url_for("licenses_view"))
        
    file = request.files['csv_file']
    if file.filename == '':
        flash("No file selected for upload.", "danger")
        return redirect(url_for("licenses_view"))
        
    if file and file.filename.endswith('.csv'):
        filename = secure_filename(file.filename)
        upload_path = os.path.join("exports", f"uploaded_{filename}")
        file.save(upload_path)
        
        imported_count = 0
        try:
            with open(upload_path, mode='r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                
                # Check for critical headers
                if 'key_string' not in reader.fieldnames or 'expires_at' not in reader.fieldnames:
                    flash("Import failed: CSV missing required column headers ('key_string', 'expires_at').", "danger")
                    return redirect(url_for("licenses_view"))
                    
                with get_db() as conn:
                    for row in reader:
                        key_string = row['key_string'].strip()
                        expires_at = row['expires_at'].strip()
                        hwid = row.get('hwid', '').strip() or None
                        is_active = int(row.get('is_active', 1))
                        user_note = row.get('user_note', '').strip() or None
                        created_at = row.get('created_at', '').strip() or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        try:
                            conn.execute("""
                                INSERT INTO licenses (key_string, expires_at, hwid, is_active, user_note, created_at)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (key_string, expires_at, hwid, is_active, user_note, created_at))
                            imported_count += 1
                        except psycopg2.errors.UniqueViolation:
                            # Skip keys that already exist in DB
                            continue
                    conn.commit()
            
            log_activity(session["username"], f"Imports: Imported {imported_count} keys via CSV")
            flash(f"Successfully imported {imported_count} licenses!", "success")
        except Exception as e:
            flash(f"An error occurred during CSV parsing: {e}", "danger")
            
    return redirect(url_for("licenses_view"))

# --- Users Views ---

@app.route("/users")
@admin_login_required
def users_view():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 
                COALESCE(user_note, 'Unassigned / Anonymous') as name,
                COUNT(*) as total,
                SUM(CASE WHEN is_active = 1 AND expires_at::timestamp >= %s::timestamp THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN expires_at::timestamp < %s::timestamp THEN 1 ELSE 0 END) as expired,
                group_concat(COALESCE(hwid, '')) as hwids,
                MAX(last_activated_at) as last_active
            FROM licenses 
            WHERE user_note IS NOT NULL AND trim(user_note) != ''
            GROUP BY user_note
            ORDER BY name ASC
        ''', (now_str, now_str))
        users_data = cursor.fetchall()
        
    return render_template("users.html", active_page="users", users=users_data)

# --- Security Views ---

@app.route("/security")
@admin_login_required
def security_view():
    page = int(request.args.get("page", 1))
    per_page = 20
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Load Admin lists
        cursor.execute("SELECT username, created_at FROM admins ORDER BY username ASC")
        admins = cursor.fetchall()
        
        # Load audit logs
        cursor.execute("SELECT COUNT(*) FROM activity_logs")
        total_count = cursor.fetchone()[0]
        
        offset = (page - 1) * per_page
        cursor.execute("SELECT * FROM activity_logs ORDER BY id DESC LIMIT ? OFFSET ?", (per_page, offset))
        logs = cursor.fetchall()
        
    total_pages = (total_count + per_page - 1) // per_page
    
    return render_template(
        "security.html",
        active_page="security",
        admins=admins,
        logs=logs,
        page=page,
        per_page=per_page,
        total_count=total_count,
        total_pages=total_pages
    )

@app.route("/security/change_password", methods=["POST"])
@admin_login_required
def security_change_password():
    current_pass = request.form.get("current_password")
    new_pass = request.form.get("new_password")
    confirm_pass = request.form.get("confirm_password")
    
    if new_pass != confirm_pass:
        flash("Password validation error: New password matches confirmation failed.", "danger")
        return redirect(url_for("security_view"))
        
    username = session["username"]
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM admins WHERE username = ?", (username,))
        row = cursor.fetchone()
        if row and verify_password(row["password_hash"], current_pass):
            modern_hash = generate_password_hash(new_pass)
            conn.execute("UPDATE admins SET password_hash = ? WHERE username = ?", (modern_hash, username))
            conn.commit()
            log_activity(username, "Security: Admin updated account password")
            flash("Admin password has been changed successfully!", "success")
        else:
            flash("Security error: Current password input incorrect.", "danger")
            
    return redirect(url_for("security_view"))

@app.route("/security/add_admin", methods=["POST"])
@admin_login_required
def security_add_admin():
    username = request.form.get("username", "").strip()
    password = request.form.get("password")
    
    if not username or not password:
        flash("Please fill in all account fields.", "warning")
        return redirect(url_for("security_view"))
        
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM admins WHERE username = ?", (username,))
        if cursor.fetchone()[0] > 0:
            flash(f"Admin username '{username}' is already in use.", "danger")
            return redirect(url_for("security_view"))
            
        password_hash = generate_password_hash(password)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO admins (username, password_hash, created_at) VALUES (?, ?, ?)", (username, password_hash, now_str))
        conn.commit()
        
    log_activity(session["username"], f"Security: Created admin account '{username}'")
    flash(f"Admin account '{username}' successfully registered!", "success")
    return redirect(url_for("security_view"))

@app.route("/security/delete_admin/<username>")
@admin_login_required
def security_delete_admin(username):
    # Prevent deleting yourself or the main admin
    if username == session["username"] or username == "admin":
        flash("Security Error: Cannot delete currently signed in admin or default root account.", "danger")
        return redirect(url_for("security_view"))
        
    with get_db() as conn:
        conn.execute("DELETE FROM admins WHERE username = ?", (username,))
        conn.commit()
        
    log_activity(session["username"], f"Security: Deleted admin account '{username}'")
    flash(f"Admin account '{username}' deleted.", "success")
    return redirect(url_for("security_view"))

# --- Settings & Database Backups ---

@app.route("/settings")
@admin_login_required
def settings_view():
    return render_template("settings.html", active_page="settings", backups=[])

@app.route("/settings/appearance", methods=["POST"])
@admin_login_required
def settings_update_appearance():
    title = request.form.get("dashboard_title", "").strip()
    logo = request.form.get("dashboard_logo", "").strip()
    
    with get_db() as conn:
        if title:
            conn.execute("INSERT INTO settings (key, value) VALUES ('dashboard_title', ?) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (title,))
        if logo:
            conn.execute("INSERT INTO settings (key, value) VALUES ('dashboard_logo', ?) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (logo,))
        conn.commit()
        
    # Check for custom image upload
    if 'logo_image' in request.files:
        file = request.files['logo_image']
        if file.filename != '':
            filename = secure_filename(file.filename)
            ext = os.path.splitext(filename)[1].lower()
            if ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
                # Overwrite static/img/logo.png
                logo_save_path = os.path.join("static", "img", "logo.png")
                file.save(logo_save_path)
                log_activity(session["username"], "Settings: Uploaded custom logo image file")
                
    log_activity(session["username"], "Settings: Updated dashboard title & logo icon settings")
    flash("Appearance configurations updated successfully!", "success")
    return redirect(url_for("settings_view"))

@app.route("/settings/regenerate_api", methods=["POST"])
@admin_login_required
def settings_regenerate_api():
    new_key = secrets.token_hex(32)
    with get_db() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('api_key', ?) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (new_key,))
        conn.commit()
        
    log_activity(session["username"], "Settings: Regenerated developer REST API authentication key")
    flash("Developer API key regenerated successfully!", "success")
    return redirect(url_for("settings_view"))

@app.route("/settings/backup/create")
@admin_login_required
def settings_create_backup():
    flash("Backups are disabled in PostgreSQL mode. Please use your database provider's backup features.", "warning")
    return redirect(url_for("settings_view"))

@app.route("/settings/backup/restore/<filename>")
@admin_login_required
def settings_restore_backup(filename):
    flash("Restore is disabled in PostgreSQL mode.", "warning")
    return redirect(url_for("settings_view"))
        
    try:
        shutil.copy(backup_path, DB_FILE)
        log_activity(session["username"], f"Database: Restored database state from '{filename}'")
        flash("Database state successfully restored from snapshot backup!", "success")
    except Exception as e:
        flash(f"Database restoration failed: {e}", "danger")
        
    return redirect(url_for("settings_view"))

@app.route("/settings/backup/delete/<filename>")
@admin_login_required
def settings_delete_backup(filename):
    return redirect(url_for("settings_view"))

# --- Client-Facing Verification API ---
@app.route("/api/verify", methods=["POST"])
@rate_limit(limit=30, window=60) # Protect verifications from brute force
def verify_license():
    data = request.get_json(force=True, silent=True)
    if not data or "key" not in data or "hwid" not in data:
        log_activity(None, "Verification Fail: Invalid payload payload arguments")
        return jsonify({"success": False, "message": "Missing key or hwid payload arguments"}), 400
        
    key = data["key"].strip()
    hwid = data["hwid"].strip()
    ip = request.remote_addr
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM licenses WHERE key_string = ?", (key,))
        license_row = cursor.fetchone()
        
        if not license_row:
            log_activity(None, f"Verification Fail: Invalid key '{key}'", ip)
            return jsonify({"success": False, "message": "Invalid license key"}), 403
            
        # Check active status
        if license_row["is_active"] != 1:
            log_activity(key, "Verification Fail: Key status blocked", ip)
            return jsonify({"success": False, "message": "This license key has been BLOCKED"}), 403
            
        # Expiration Check
        expiry_dt = datetime.strptime(license_row["expires_at"], "%Y-%m-%d %H:%M:%S")
        if datetime.now() > expiry_dt:
            log_activity(key, f"Verification Fail: Key expired on {license_row['expires_at']}", ip)
            return jsonify({"success": False, "message": f"This license key expired on {license_row['expires_at']}"}), 403
            
        # HWID Lock logic
        current_lock = license_row["hwid"]
        if not current_lock:
            # Bind the key to the hardware on first verification
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE licenses SET hwid = ?, last_activated_at = ? WHERE id = ?", (hwid, now_str, license_row["id"]))
            conn.commit()
            log_activity(key, "Verification Success: First binding lock successful", ip)
            return jsonify({
                "success": True, 
                "message": "License locked to hardware successfully", 
                "expires_at": license_row["expires_at"]
            }), 200
        elif current_lock != hwid:
            # Hardware mismatch
            log_activity(key, "Verification Fail: Hardware ID lock mismatch", ip)
            return jsonify({"success": False, "message": "HWID verification failed! Key is locked to another hardware"}), 403
            
        # Fully verified successfully
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE licenses SET last_activated_at = ? WHERE id = ?", (now_str, license_row["id"]))
        conn.commit()
        log_activity(key, "Verification Success: Session authenticated", ip)
        return jsonify({
            "success": True,
            "message": "Verification Successful",
            "expires_at": license_row["expires_at"]
        }), 200

# --- Authenticated REST APIs for CRUD Management ---

def check_api_auth():
    # Allow session authentication (dashboard AJAX calls bypass key restriction)
    if session.get("admin_logged_in"):
        return True
        
    key = request.headers.get("X-API-Key") or request.args.get("api_key")
    if not key:
        return False
        
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'api_key'")
            row = cursor.fetchone()
            if row and row["value"] == key:
                return True
    except Exception:
        pass
    return False

def api_auth_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not check_api_auth():
            return jsonify({"success": False, "message": "Unauthorized REST API access. Missing/Invalid API key header."}), 401
        return f(*args, **kwargs)
    return wrapper

@app.route("/api/v1/licenses", methods=["GET"])
@api_auth_required
def api_list_licenses():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "all")
    sort = request.args.get("sort", "expiry_desc")
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 50))
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    query = "SELECT * FROM licenses WHERE 1=1"
    params = []
    
    if search:
        query += " AND (key_string LIKE ? OR user_note LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
        
    if status == "active":
        query += " AND is_active = 1 AND expires_at::timestamp >= %s::timestamp"
        params.append(now_str)
    elif status == "blocked":
        query += " AND is_active = 0"
    elif status == "expired":
        query += " AND expires_at::timestamp < %s::timestamp"
        params.append(now_str)
        
    if sort == "expiry_asc":
        query += " ORDER BY datetime(expires_at) ASC"
    elif sort == "expiry_desc":
        query += " ORDER BY datetime(expires_at) DESC"
    elif sort == "created_asc":
        query += " ORDER BY id ASC"
    else:
        query += " ORDER BY id DESC"
        
    # Get total count
    count_query = f"SELECT COUNT(*) FROM ({query})"
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()[0]
        
        # Paginate
        query += " LIMIT ? OFFSET ?"
        offset = (page - 1) * limit
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
    licenses = []
    for r in rows:
        lic = dict(r)
        expiry_dt = datetime.strptime(r["expires_at"], "%Y-%m-%d %H:%M:%S")
        lic["is_expired"] = datetime.now() > expiry_dt
        licenses.append(lic)
        
    total_pages = (total_count + limit - 1) // limit
    
    return jsonify({
        "success": True,
        "licenses": licenses,
        "total_count": total_count,
        "total_pages": total_pages,
        "page": page,
        "limit": limit
    })

@app.route("/api/v1/licenses", methods=["POST"])
@api_auth_required
def api_create_licenses():
    data = request.get_json(force=True, silent=True) or {}
    
    duration = int(data.get("duration", 30))
    quantity = int(data.get("quantity", 1))
    custom_key = data.get("custom_key", "").strip()
    user_note = data.get("user_note", "").strip()
    
    created_keys = []
    with get_db() as conn:
        for i in range(quantity):
            if custom_key:
                key = f"{custom_key}-{i+1}" if quantity > 1 else custom_key
            else:
                key = f"SRF-PRO-{uuid.uuid4().hex[:12].upper()}"
                
            expires_at = (datetime.now() + timedelta(days=duration)).strftime("%Y-%m-%d %H:%M:%S")
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            try:
                conn.execute("INSERT INTO licenses (key_string, expires_at, user_note, created_at) VALUES (?, ?, ?, ?)", 
                             (key, expires_at, user_note, created_at))
                created_keys.append(key)
            except psycopg2.errors.UniqueViolation:
                key = f"{key}-{secrets.token_hex(3).upper()}"
                conn.execute("INSERT INTO licenses (key_string, expires_at, user_note, created_at) VALUES (?, ?, ?, ?)", 
                             (key, expires_at, user_note, created_at))
                created_keys.append(key)
        conn.commit()
        
    log_activity("REST-API", f"API: Generated {quantity} keys")
    return jsonify({
        "success": True,
        "message": f"Generated {quantity} licensing keys successfully",
        "keys": created_keys
    }), 201

@app.route("/api/v1/licenses", methods=["PUT"])
@api_auth_required
def api_update_license():
    data = request.get_json(force=True, silent=True) or {}
    lic_id = data.get("id")
    user_note = data.get("user_note")
    expires_at = data.get("expires_at")
    
    if not lic_id:
        return jsonify({"success": False, "message": "Missing 'id' parameter"}), 400
        
    updates = []
    params = []
    
    if user_note is not None:
        updates.append("user_note = ?")
        params.append(user_note.strip())
        
    if expires_at is not None:
        try:
            datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
            updates.append("expires_at = ?")
            params.append(expires_at.strip())
        except ValueError:
            return jsonify({"success": False, "message": "Invalid date format. Must be YYYY-MM-DD HH:MM:SS"}), 400
            
    if not updates:
        return jsonify({"success": False, "message": "Nothing to update"}), 400
        
    query = f"UPDATE licenses SET {', '.join(updates)} WHERE id = ?"
    params.append(lic_id)
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        if cursor.rowcount == 0:
            return jsonify({"success": False, "message": "License key matching ID not found"}), 404
            
    log_activity("REST-API", f"API: Updated license ID {lic_id}")
    return jsonify({"success": True, "message": "License configuration updated successfully"})

@app.route("/api/v1/licenses", methods=["DELETE"])
@api_auth_required
def api_delete_license():
    data = request.get_json(force=True, silent=True) or {}
    lic_id = data.get("id") or request.args.get("id")
    
    if not lic_id:
        return jsonify({"success": False, "message": "Missing 'id' parameter"}), 400
        
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key_string FROM licenses WHERE id = ?", (lic_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"success": False, "message": "License key matching ID not found"}), 404
            
        conn.execute("DELETE FROM licenses WHERE id = ?", (lic_id,))
        conn.commit()
        
    log_activity("REST-API", f"API: Deleted key '{row['key_string']}'")
    return jsonify({"success": True, "message": f"License key '{row['key_string']}' deleted successfully"})


if __name__ == "__main__":
    print("[SYSTEM] Launching SRF PRO License Manager Suite...")
    print("[SYSTEM] Client API Endpoint active: http://127.0.0.1:5000/api/verify")
    print("[SYSTEM] REST API Documentation: http://127.0.0.1:5000/api/v1/licenses")
    print("[SYSTEM] Admin Dashboard: http://127.0.0.1:5000/")
    
    # Launch self-hosted licensing server on port 5000
    app.run(host="0.0.0.0", port=5000, debug=False)
