import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import DB_PATH

DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_WORK_START = "09:00"
DEFAULT_WORK_END = "18:00"
DEFAULT_WORK_DAYS = [0, 1, 2, 3, 4]
DEFAULT_BUFFER_MINUTES = 15
DEFAULT_CATEGORY_COLORS = {
    "work": "3",
    "health": "6",
    "rest": "10",
    "travel": "7",
    "family": "4",
    "personal": "5",
    "other": None,
}
GOOGLE_EVENT_COLOR_IDS = {str(value) for value in range(1, 12)}
TASK_PRIORITIES = {"low", "normal", "high"}
TASK_STATUSES = {"open", "done"}
OAUTH_STATE_TTL_MINUTES = 15

_db_dir = os.path.dirname(os.path.abspath(DB_PATH))
os.makedirs(_db_dir, exist_ok=True)
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
db_lock = threading.RLock()


def init_db():
    with db_lock:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT, google_token TEXT, timezone TEXT, work_start TEXT, work_end TEXT, work_days TEXT, buffer_minutes INTEGER, category_colors TEXT)""")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        for column, sql_type in {"timezone":"TEXT","work_start":"TEXT","work_end":"TEXT","work_days":"TEXT","buffer_minutes":"INTEGER","category_colors":"TEXT"}.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column} {sql_type}")
        conn.execute("""CREATE TABLE IF NOT EXISTS oauth_states (state TEXT PRIMARY KEY, user_id INTEGER, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS google_accounts (user_id INTEGER PRIMARY KEY AUTOINCREMENT, google_sub TEXT NOT NULL UNIQUE, email TEXT NOT NULL, name TEXT, created_at TEXT NOT NULL)""")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                due_at TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                priority TEXT NOT NULL DEFAULT 'normal',
                created_at TEXT NOT NULL,
                completed_at TEXT
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_due ON tasks(user_id, due_at)")
        conn.commit()


def ensure_user(user_id: int, name: str | None = None) -> None:
    with db_lock:
        conn.execute("INSERT OR IGNORE INTO users (user_id,name) VALUES (?,?)",(user_id,name))
        if name:
            conn.execute("UPDATE users SET name=? WHERE user_id=?",(name,user_id))
        conn.commit()


def get_or_create_google_user(google_sub: str, email: str, name: str | None) -> int:
    if not google_sub or not email:
        raise ValueError("Google не вернул идентификатор пользователя или email")
    with db_lock:
        row=conn.execute("SELECT user_id FROM google_accounts WHERE google_sub=?",(google_sub,)).fetchone()
        if row:
            user_id=int(row[0]); conn.execute("UPDATE google_accounts SET email=?,name=? WHERE user_id=?",(email,name,user_id))
        else:
            cur=conn.execute("INSERT INTO google_accounts (google_sub,email,name,created_at) VALUES (?,?,?,?)",(google_sub,email,name,datetime.now(timezone.utc).isoformat())); user_id=int(cur.lastrowid)
        conn.execute("INSERT OR IGNORE INTO users (user_id,name) VALUES (?,?)",(user_id,name))
        if name:
            conn.execute("UPDATE users SET name=? WHERE user_id=?",(name,user_id))
        conn.commit()
    return user_id


def get_google_account(user_id:int)->dict|None:
    with db_lock:
        row=conn.execute("SELECT user_id,google_sub,email,name FROM google_accounts WHERE user_id=?",(user_id,)).fetchone()
    return {"user_id":int(row[0]),"google_sub":row[1],"email":row[2],"name":row[3]} if row else None


def save_google_token(user_id, token_dict):
    with db_lock:
        conn.execute("INSERT INTO users (user_id,google_token) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET google_token=excluded.google_token",(user_id,json.dumps(token_dict)))
        conn.commit()


def get_google_token(user_id):
    with db_lock:
        row=conn.execute("SELECT google_token FROM users WHERE user_id=?",(user_id,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def clear_google_token(user_id:int)->None:
    with db_lock:
        conn.execute("UPDATE users SET google_token=NULL WHERE user_id=?",(user_id,))
        conn.commit()


def save_oauth_state(state:str,user_id:int|None=None)->None:
    now=datetime.now(timezone.utc).isoformat()
    with db_lock:
        if user_id is not None:
            conn.execute("DELETE FROM oauth_states WHERE user_id=?",(user_id,))
        conn.execute("INSERT OR REPLACE INTO oauth_states (state,user_id,created_at) VALUES (?,?,?)",(state,user_id,now))
        conn.commit()


def consume_oauth_state(state: str) -> int | None:
    with db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT user_id,created_at FROM oauth_states WHERE state=?", (state,)).fetchone()
            conn.execute("DELETE FROM oauth_states WHERE state=?", (state,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    if not row:
        return None
    try:
        created = datetime.fromisoformat(row[1])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - created
    except (TypeError, ValueError):
        return None
    if age < timedelta(0) or age > timedelta(minutes=OAUTH_STATE_TTL_MINUTES):
        return None
    return int(row[0]) if row[0] is not None else 0


def _zone(name: str):
    try:
        return ZoneInfo(str(name))
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Неизвестный часовой пояс") from exc


def _table_exists(name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone() is not None


def _rebase_wall_clock_iso(value: object, old_timezone: str, new_timezone: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    old_zone = _zone(old_timezone)
    new_zone = _zone(new_timezone)
    local = parsed.astimezone(old_zone)
    moved = datetime(
        local.year,
        local.month,
        local.day,
        local.hour,
        local.minute,
        local.second,
        local.microsecond,
        tzinfo=new_zone,
        fold=local.fold,
    )
    return moved.astimezone(timezone.utc).isoformat()


def _rebase_user_floating_times(user_id: int, old_timezone: str, new_timezone: str) -> None:
    """Keep task/reminder wall-clock times stable when the user changes timezone.

    Calendar events are intentionally excluded: they represent real instants and
    may carry their own event timezone. Standalone tasks and reminders are
    floating personal times, so 23:00 must stay 23:00 after travel.
    """
    if old_timezone == new_timezone:
        return

    _zone(old_timezone)
    _zone(new_timezone)

    if _table_exists("reminders"):
        rows = conn.execute(
            "SELECT reminder_id,remind_at,next_remind_at,status,repeat_rule,repeat_timezone "
            "FROM reminders WHERE user_id=? AND deleted_at IS NULL AND ("
            "status IN ('pending','delivering') OR "
            "(repeat_rule IS NOT NULL AND next_remind_at IS NOT NULL)"
            ")",
            (int(user_id),),
        ).fetchall()
        for reminder_id, remind_at, next_remind_at, status, repeat_rule, repeat_timezone in rows:
            follows_user_timezone = (
                not repeat_rule
                or not repeat_timezone
                or str(repeat_timezone) == str(old_timezone)
            )
            if not follows_user_timezone:
                continue

            move_current = status in {"pending", "delivering"}
            moved_remind = (
                _rebase_wall_clock_iso(remind_at, old_timezone, new_timezone)
                if move_current
                else str(remind_at or "").strip() or None
            )
            moved_next = _rebase_wall_clock_iso(next_remind_at, old_timezone, new_timezone)
            if move_current and moved_remind is None:
                continue
            conn.execute(
                "UPDATE reminders SET remind_at=?,next_remind_at=?,repeat_timezone=? "
                "WHERE user_id=? AND reminder_id=?",
                (
                    moved_remind,
                    moved_next,
                    new_timezone if repeat_rule else repeat_timezone,
                    int(user_id),
                    int(reminder_id),
                ),
            )

    if _table_exists("tasks"):
        rows = conn.execute(
            "SELECT task_id,due_at FROM tasks "
            "WHERE user_id=? AND status='open' AND due_at IS NOT NULL",
            (int(user_id),),
        ).fetchall()
        for task_id, due_at in rows:
            moved_due = _rebase_wall_clock_iso(due_at, old_timezone, new_timezone)
            if moved_due is None:
                continue
            conn.execute(
                "UPDATE tasks SET due_at=? WHERE user_id=? AND task_id=?",
                (moved_due, int(user_id), int(task_id)),
            )


def _repair_stale_user_timezone_items(user_id: int, timezone_name: str) -> None:
    """Repair legacy active items whose own schedule timezone is older than the user timezone."""
    if not _table_exists("reminders"):
        return

    rows = conn.execute(
        "SELECT reminder_id,remind_at,next_remind_at,status,repeat_timezone "
        "FROM reminders WHERE user_id=? AND deleted_at IS NULL "
        "AND repeat_rule IS NOT NULL AND next_remind_at IS NOT NULL "
        "AND repeat_timezone IS NOT NULL AND repeat_timezone<>?",
        (int(user_id), timezone_name),
    ).fetchall()
    stale_zones = {str(row[4]) for row in rows if row[4]}

    for reminder_id, remind_at, next_remind_at, status, source_timezone in rows:
        try:
            _zone(source_timezone)
        except ValueError:
            continue
        moved_remind = (
            _rebase_wall_clock_iso(remind_at, source_timezone, timezone_name)
            if status in {"pending", "delivering"}
            else str(remind_at or "").strip() or None
        )
        moved_next = _rebase_wall_clock_iso(next_remind_at, source_timezone, timezone_name)
        conn.execute(
            "UPDATE reminders SET remind_at=?,next_remind_at=?,repeat_timezone=? "
            "WHERE user_id=? AND reminder_id=?",
            (
                moved_remind,
                moved_next,
                timezone_name,
                int(user_id),
                int(reminder_id),
            ),
        )

    # Legacy tasks did not store their creation timezone. If all stale recurring
    # reminders agree on one former user timezone, use that as the migration hint.
    if len(stale_zones) == 1 and _table_exists("tasks"):
        source_timezone = next(iter(stale_zones))
        if source_timezone != timezone_name:
            rows = conn.execute(
                "SELECT task_id,due_at FROM tasks "
                "WHERE user_id=? AND status='open' AND due_at IS NOT NULL",
                (int(user_id),),
            ).fetchall()
            for task_id, due_at in rows:
                moved_due = _rebase_wall_clock_iso(due_at, source_timezone, timezone_name)
                if moved_due is not None:
                    conn.execute(
                        "UPDATE tasks SET due_at=? WHERE user_id=? AND task_id=?",
                        (moved_due, int(user_id), int(task_id)),
                    )


def save_user_timezone(user_id:int,timezone:str,*,commit:bool=True)->None:
    timezone = str(timezone or "").strip()
    _zone(timezone)
    with db_lock:
        row = conn.execute("SELECT timezone FROM users WHERE user_id=?", (int(user_id),)).fetchone()
        old_timezone = str(row[0] or DEFAULT_TIMEZONE) if row else DEFAULT_TIMEZONE
        if old_timezone != timezone:
            try:
                _rebase_user_floating_times(int(user_id), old_timezone, timezone)
            except ValueError:
                if old_timezone != DEFAULT_TIMEZONE:
                    _rebase_user_floating_times(int(user_id), DEFAULT_TIMEZONE, timezone)
                else:
                    raise

        # This also runs when the account already has the new timezone. It repairs
        # rows created before timezone rebasing existed (e.g. Moscow -> Saratov).
        _repair_stale_user_timezone_items(int(user_id), timezone)

        conn.execute(
            "INSERT INTO users (user_id,timezone) VALUES (?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET timezone=excluded.timezone",
            (int(user_id), timezone),
        )
        if commit:
            conn.commit()


def get_user_timezone(user_id:int,default:str|None=DEFAULT_TIMEZONE)->str|None:
    with db_lock:
        row=conn.execute("SELECT timezone FROM users WHERE user_id=?",(user_id,)).fetchone()
    return row[0] if row and row[0] else default


def save_calendar_preferences(user_id:int,*,work_start:str|None=None,work_end:str|None=None,work_days:list[int]|None=None,buffer_minutes:int|None=None,category_colors:dict[str,str|None]|None=None,commit:bool=True)->None:
    with db_lock:
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)",(user_id,));updates=[];values=[]
        if work_start is not None:updates.append("work_start=?");values.append(work_start)
        if work_end is not None:updates.append("work_end=?");values.append(work_end)
        if work_days is not None:updates.append("work_days=?");values.append(json.dumps(sorted(set(work_days))))
        if buffer_minutes is not None:updates.append("buffer_minutes=?");values.append(int(buffer_minutes))
        if category_colors is not None:updates.append("category_colors=?");values.append(json.dumps(category_colors,sort_keys=True))
        if not updates:return
        values.append(user_id);conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id=?",values)
        if commit: conn.commit()


def get_calendar_preferences(user_id:int)->dict:
    with db_lock:
        row=conn.execute("SELECT work_start,work_end,work_days,buffer_minutes,category_colors FROM users WHERE user_id=?",(user_id,)).fetchone()
    work_start=row[0] if row and row[0] else DEFAULT_WORK_START;work_end=row[1] if row and row[1] else DEFAULT_WORK_END
    try:work_days=[int(day) for day in json.loads(row[2])] if row and row[2] else DEFAULT_WORK_DAYS.copy()
    except (TypeError,ValueError,json.JSONDecodeError):work_days=DEFAULT_WORK_DAYS.copy()
    buffer=row[3] if row and row[3] is not None else DEFAULT_BUFFER_MINUTES
    category_colors=DEFAULT_CATEGORY_COLORS.copy()
    try:
        stored_colors=json.loads(row[4]) if row and row[4] else {}
        if isinstance(stored_colors,dict):
            for category,color_id in stored_colors.items():
                if category in category_colors and (color_id is None or str(color_id) in GOOGLE_EVENT_COLOR_IDS):
                    category_colors[category]=str(color_id) if color_id is not None else None
    except (TypeError,ValueError,json.JSONDecodeError):
        pass
    return {"work_start":work_start,"work_end":work_end,"work_days":work_days,"buffer_minutes":int(buffer),"category_colors":category_colors}


def get_category_colors(user_id:int)->dict[str,str|None]:
    return get_calendar_preferences(user_id)["category_colors"]


def get_onboarding_status(user_id:int)->dict:
    return {"google_connected":get_google_token(user_id) is not None,"timezone_set":get_user_timezone(user_id,default=None) is not None,"preferences":get_calendar_preferences(user_id)}


def _task_from_row(row) -> dict:
    return {
        "task_id": int(row[0]),
        "user_id": int(row[1]),
        "title": row[2],
        "due_at": row[3],
        "status": row[4],
        "priority": row[5],
        "created_at": row[6],
        "completed_at": row[7],
    }


def create_task(
    user_id: int,
    title: str,
    *,
    due_at: datetime | None = None,
    priority: str = "normal",
) -> dict:
    title = " ".join(title.split()).strip()
    if not title:
        raise ValueError("Task title is required")
    if priority not in TASK_PRIORITIES:
        raise ValueError("Unknown task priority")
    due_value = due_at.isoformat() if due_at else None
    created_at = datetime.now(timezone.utc).isoformat()
    with db_lock:
        cur = conn.execute(
            "INSERT INTO tasks (user_id,title,due_at,status,priority,created_at) VALUES (?,?,?,?,?,?)",
            (int(user_id), title, due_value, "open", priority, created_at),
        )
        conn.commit()
        row = conn.execute(
            "SELECT task_id,user_id,title,due_at,status,priority,created_at,completed_at FROM tasks WHERE task_id=?",
            (cur.lastrowid,),
        ).fetchone()
    return _task_from_row(row)


def list_tasks(
    user_id: int,
    *,
    status: str = "open",
    due_start: datetime | None = None,
    due_end: datetime | None = None,
    limit: int = 100,
) -> list[dict]:
    if status not in TASK_STATUSES:
        raise ValueError("Unknown task status")
    clauses = ["user_id=?", "status=?"]
    values: list[object] = [int(user_id), status]
    if due_start is not None:
        clauses.append("due_at IS NOT NULL AND due_at>=?")
        values.append(due_start.isoformat())
    if due_end is not None:
        clauses.append("due_at IS NOT NULL AND due_at<?")
        values.append(due_end.isoformat())
    values.append(max(1, min(int(limit), 500)))
    sql = (
        "SELECT task_id,user_id,title,due_at,status,priority,created_at,completed_at "
        "FROM tasks WHERE " + " AND ".join(clauses) +
        " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, "
        "CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, due_at, task_id LIMIT ?"
    )
    with db_lock:
        rows = conn.execute(sql, values).fetchall()
    return [_task_from_row(row) for row in rows]


def get_task(user_id: int, task_id: int) -> dict | None:
    with db_lock:
        row = conn.execute(
            "SELECT task_id,user_id,title,due_at,status,priority,created_at,completed_at "
            "FROM tasks WHERE user_id=? AND task_id=?",
            (int(user_id), int(task_id)),
        ).fetchone()
    return _task_from_row(row) if row else None


def set_task_completed(user_id: int, task_id: int, completed: bool = True) -> dict | None:
    status = "done" if completed else "open"
    completed_at = datetime.now(timezone.utc).isoformat() if completed else None
    with db_lock:
        cur = conn.execute(
            "UPDATE tasks SET status=?,completed_at=? WHERE user_id=? AND task_id=?",
            (status, completed_at, int(user_id), int(task_id)),
        )
        if cur.rowcount == 0:
            return None
        conn.commit()
    return get_task(user_id, task_id)


def delete_task(user_id: int, task_id: int) -> bool:
    with db_lock:
        cur = conn.execute(
            "DELETE FROM tasks WHERE user_id=? AND task_id=?",
            (int(user_id), int(task_id)),
        )
        conn.commit()
    return cur.rowcount > 0


init_db()