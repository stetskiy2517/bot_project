import sqlite3
from datetime import datetime

conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

def init_db():
    # Таблица пользователей
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        google_calendar_id TEXT,
        google_credentials TEXT
    )
    """)
    # Таблица событий
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        category TEXT,
        start DATETIME,
        end DATETIME,
        priority INTEGER,
        recurring TEXT,
        google_event_id TEXT
    )
    """)
    # Таблица финансов
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS finance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        type TEXT,
        category TEXT,
        amount REAL,
        date DATETIME
    )
    """)
    conn.commit()

def add_transaction(user_id, t_type, category, amount):
    cursor.execute("""
        INSERT INTO finance (user_id, type, category, amount, date)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, t_type, category, amount, datetime.now()))
    conn.commit()
    return "✅ Записано"

def get_balance(user_id):
    cursor.execute("""
        SELECT SUM(CASE WHEN type='income' THEN amount ELSE -amount END)
        FROM finance WHERE user_id=?
    """, (user_id,))
    return cursor.fetchone()[0] or 0
