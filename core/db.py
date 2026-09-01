import sqlite3, json

conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

def init_db():
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        google_token TEXT
    )
    """)
    conn.commit()

def save_google_token(user_id, token_dict):
    cursor.execute("""
    INSERT OR REPLACE INTO users (user_id, google_token)
    VALUES (?, ?)
    """, (user_id, json.dumps(token_dict)))
    conn.commit()

def get_google_token(user_id):
    cursor.execute("SELECT google_token FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return json.loads(row[0]) if row else None
