import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", "nutrition.db")

DEFAULT_STAPLES = [
    ("Sardines in Tomato Sauce", "1 can", 190, 22.0, 2.0, 10.0),
    ("0% Fat Cottage Cheese", "1 cup (225g)", 160, 26.0, 10.0, 0.0),
    ("Smoked Oysters", "1 can (85g)", 170, 14.0, 6.0, 10.0),
    ("Cheddar 0%", "1 bag (198g)", 315, 63, 14.0, 0.0),
    ("Large Eggs (x2)", "2 eggs", 140, 12.0, 1.0, 10.0),
    ("Powder","1 Scoop", 120,25,3,1.5)
]

def seed_staples_for_user(conn: sqlite3.Connection, user_id: int):
    conn.executemany(
        """INSERT OR IGNORE INTO staples
           (user_id, item_name, serving_size, kcal, protein_g, carbs_g, fat_g)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [(user_id, *s) for s in DEFAULT_STAPLES],
    )

def init_db():
    os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        # Enable WAL mode for better concurrency over file mounts
        conn.execute("PRAGMA journal_mode=WAL;")
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                pin TEXT DEFAULT '',
                sms_gateway TEXT DEFAULT '',
                kcal INTEGER DEFAULT 2200,
                protein_g REAL DEFAULT 180.0,
                carbs_g REAL DEFAULT 180.0,
                fat_g REAL DEFAULT 70.0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                log_date TEXT NOT NULL,
                item_name TEXT,
                servings REAL,
                kcal INTEGER,
                protein_g REAL,
                carbs_g REAL,
                fat_g REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS staples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                serving_size TEXT,
                kcal INTEGER,
                protein_g REAL,
                carbs_g REAL,
                fat_g REAL,
                UNIQUE(user_id, item_name),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sms_status (
                user_id INTEGER NOT NULL,
                log_date TEXT NOT NULL,
                sent INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, log_date),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        
        # Add index on logs(user_id, log_date) to speed up daily and 7-day queries
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_logs_user_date 
            ON logs (user_id, log_date)
        """)

        cur = conn.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            default_gw = os.getenv("SMS_GATEWAY_ADDRESS", "")
            cursor = conn.execute(
                """INSERT INTO users (username, pin, sms_gateway, kcal, protein_g, carbs_g, fat_g)
                   VALUES (?, ?, ?, 2200, 180.0, 180.0, 70.0)""",
                ("Derrick", "", default_gw),
            )
            seed_staples_for_user(conn, cursor.lastrowid)
            print(f"[init_db] Initialized new database at {DB_PATH} and seeded default user.")
        else:
            print(f"[init_db] Verified existing database at {DB_PATH}.")

if __name__ == "__main__":
    init_db()