import re

def migrate_to_postgres(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Imports
    content = content.replace("import sqlite3", "import psycopg2\nimport psycopg2.extras\nfrom psycopg2 import sql\nimport os")

    # 2. Connection and DB Wrapper
    db_wrapper = """
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
"""
    # Replace get_db
    content = re.sub(
        r"def get_db\(\):.*?return conn",
        db_wrapper.strip(),
        content,
        flags=re.DOTALL
    )

    # 3. Schema changes in init_db
    content = content.replace("id INTEGER PRIMARY KEY AUTOINCREMENT", "id SERIAL PRIMARY KEY")
    
    # Pragma is sqlite specific
    content = re.sub(r"cursor\.execute\(\"PRAGMA table_info\(licenses\)\"\).*?if \"last_activated_at\" not in columns:\n.*?conn\.execute\(\"ALTER TABLE licenses ADD COLUMN last_activated_at TEXT\"\)",
                     """
        # Note: Postgres schema migration should ideally be handled via a migration tool (like Alembic).
        # We will assume columns exist for fresh installs.
                     """, content, flags=re.DOTALL)
    
    # 4. Integrity Error exception
    content = content.replace("sqlite3.IntegrityError", "psycopg2.errors.UniqueViolation")

    # 5. Queries syntax updates
    # datetime(expires_at) -> expires_at::timestamp
    content = content.replace("datetime(expires_at) >=", "expires_at::timestamp >=")
    content = content.replace("datetime(expires_at) <", "expires_at::timestamp <")
    content = content.replace("datetime(?)", "%s::timestamp")
    
    # date(created_at) -> created_at::date
    content = content.replace("date(created_at) =", "created_at::date =")
    
    # strftime('%Y-%m', created_at) -> to_char(created_at::timestamp, 'YYYY-MM')
    content = content.replace("strftime('%Y-%m', created_at)", "to_char(created_at::timestamp, 'YYYY-MM')")

    # INSERT OR REPLACE INTO -> INSERT INTO ... ON CONFLICT
    # Example: conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('dashboard_title', ?)", (title,))
    # In postgres: INSERT INTO settings (key, value) VALUES ('dashboard_title', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    content = re.sub(
        r"INSERT OR REPLACE INTO settings \(key, value\) VALUES \((.*?)\)",
        r"INSERT INTO settings (key, value) VALUES (\1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        content
    )

    # 6. Disable legacy migration and backups since they rely on sqlite files
    content = re.sub(r"def migrate_legacy_db\(\):.*?migrate_legacy_db\(\)", "", content, flags=re.DOTALL)
    
    # In settings_view for backups
    content = re.sub(r"def settings_view\(\):.*?return render_template\(\"settings\.html\", active_page=\"settings\", backups=backups\)", 
                     "def settings_view():\n    return render_template(\"settings.html\", active_page=\"settings\", backups=[])", content, flags=re.DOTALL)
                     
    # backup create/restore routes
    content = re.sub(r"@app\.route\(\"/settings/backup/create\"\).*?return redirect\(url_for\(\"settings_view\"\)\)",
                     "@app.route(\"/settings/backup/create\")\n@admin_login_required\ndef settings_create_backup():\n    flash(\"Backups are disabled in PostgreSQL mode. Please use your database provider's backup features.\", \"warning\")\n    return redirect(url_for(\"settings_view\"))", content, flags=re.DOTALL)
    
    content = re.sub(r"@app\.route\(\"/settings/backup/restore/<filename>\"\).*?return redirect\(url_for\(\"settings_view\"\)\)",
                     "@app.route(\"/settings/backup/restore/<filename>\")\n@admin_login_required\ndef settings_restore_backup(filename):\n    flash(\"Restore is disabled in PostgreSQL mode.\", \"warning\")\n    return redirect(url_for(\"settings_view\"))", content, flags=re.DOTALL)
    
    content = re.sub(r"@app\.route\(\"/settings/backup/delete/<filename>\"\).*?return redirect\(url_for\(\"settings_view\"\)\)",
                     "@app.route(\"/settings/backup/delete/<filename>\")\n@admin_login_required\ndef settings_delete_backup(filename):\n    return redirect(url_for(\"settings_view\"))", content, flags=re.DOTALL)


    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == "__main__":
    migrate_to_postgres("licensing_server.py")
    print("Migration script completed.")
