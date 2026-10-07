"""Shared fulfillment effects for task/reminder storage transitions."""
from datetime import datetime, timezone


def sync_completion(connection, user_id, kind, target_id, completed):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='commitment_actions'").fetchone():
        return
    now = datetime.now(timezone.utc).isoformat()
    connection.execute("UPDATE sales_commitments SET status=?,completed_at=?,updated_at=? WHERE user_id=? AND commitment_id IN (SELECT commitment_id FROM commitment_actions WHERE user_id=? AND kind=? AND target_id=?)", ("done" if completed else "open", now if completed else None, now, user_id, user_id, kind, target_id))


def remove_link(connection, user_id, kind, target_id):
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='commitment_actions'").fetchone():
        connection.execute("DELETE FROM commitment_actions WHERE user_id=? AND kind=? AND target_id=?", (user_id, kind, target_id))
