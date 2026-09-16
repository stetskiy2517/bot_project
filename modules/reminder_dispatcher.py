"""Background delivery of standalone reminders and important attention signals through Web Push."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from time import sleep

from pywebpush import WebPushException

from config import BASE_URL, WEB_PUSH_WORKER_INTERVAL_SECONDS, WEB_PUSH_WORKER_ENABLED
from core.attention_store import mark_attention_push_result, pending_attention_pushes
from core.push_store import (
    delete_push_subscription_by_id,
    list_push_subscriptions,
    list_push_user_ids,
    mark_push_error,
    mark_push_success,
)
from core.reminder_store import claim_due_for_push, complete_push_delivery, release_push_delivery
from core.db import get_google_account
from core.library_store import get_saved_reminder
from core.command_store import user_operation, UserBusyError
from core.assistant_preferences import get_assistant_preferences, quiet_until
from core.notification_policy import activate_repeats, claim_repeat_attempts, postpone_transport
from modules.attention import sync_attention_context
from modules.daily_review import deliver_reviews_for_user
from integrations.web_push import send_web_push, web_push_error_details

logger = logging.getLogger(__name__)

_worker_lock = threading.Lock()
_worker_started = False
_attention_sync_at: dict[int, float] = {}
ATTENTION_SYNC_SECONDS = 5 * 60


def _push_error_message(exc: WebPushException) -> tuple[int | None, str]:
    status_code, body = web_push_error_details(exc)
    label = f"Web Push error {status_code or 'unknown'}"
    if body and "BadJwtToken" in body:
        label += ": BadJwtToken"
    return status_code, label[:1000]


def _push_navigate_url(path: str = "/") -> str:
    """Return an absolute same-origin URL required by Declarative Web Push."""
    base = str(BASE_URL or "").strip().rstrip("/")
    suffix = str(path or "/").strip() or "/"
    if not suffix.startswith("/"):
        suffix = f"/{suffix}"
    return f"{base}{suffix}" if base else suffix


def _reminder_action_path(action: str, reminder_id: int) -> str:
    return f"/?push_action={action}&reminder_id={int(reminder_id)}"


def _notification_payload(
    *,
    title: str,
    body: str,
    tag: str,
    url: str = "/",
    reminder_id: int | None = None,
) -> dict:
    """Build one payload that works declaratively on Apple and via SW elsewhere."""
    data = {"url": url}
    actions: list[dict[str, str]] = []
    if reminder_id is not None:
        reminder_id = int(reminder_id)
        data["reminder_id"] = reminder_id
        complete_path = _reminder_action_path("complete", reminder_id)
        reschedule_path = _reminder_action_path("reschedule", reminder_id)
        data["action_urls"] = {
            "complete": complete_path,
            "reschedule": reschedule_path,
        }
        actions = [
            {
                "action": "complete",
                "title": "Выполнено",
                "navigate": _push_navigate_url(complete_path),
            },
            {
                "action": "reschedule",
                "title": "Отложить",
                "navigate": _push_navigate_url(reschedule_path),
            },
        ]

    notification = {
        "title": title,
        "lang": "ru-RU",
        "dir": "ltr",
        "body": body,
        "navigate": _push_navigate_url(url),
        "silent": False,
        "tag": tag,
        "data": data,
    }
    if actions:
        notification["actions"] = actions

    return {
        "web_push": 8030,
        "notification": notification,
    }


def _deliver_payload(user_id: int, payload: dict) -> dict:
    subscriptions = list_push_subscriptions(user_id)
    result = {"ok": False, "subscriptions": len(subscriptions), "accepted": 0,
              "failed": 0, "removed": 0, "errors": []}
    for subscription in subscriptions:
        subscription_id = subscription["subscription_id"]
        try:
            send_web_push(subscription, payload)
        except WebPushException as exc:
            status_code, message = _push_error_message(exc)
            if status_code in {404, 410}:
                delete_push_subscription_by_id(subscription_id)
                result["removed"] += 1
            else:
                mark_push_error(subscription_id, message)
            result["failed"] += 1
            result["errors"].append(message)
            logger.warning("Push failed for user %s subscription %s: %s", user_id, subscription_id, message)
        except ValueError:
            delete_push_subscription_by_id(subscription_id)
            result["removed"] += 1
            result["failed"] += 1
            result["errors"].append("Некорректная push-подписка отключена. Подключи уведомления заново.")
            logger.warning("Invalid push subscription %s removed for user %s", subscription_id, user_id)
        except Exception as exc:
            message = "Push transport error: " + type(exc).__name__
            mark_push_error(subscription_id, message)
            result["failed"] += 1
            result["errors"].append(message)
            logger.warning("Push transport failed for user %s subscription %s (%s)", user_id, subscription_id, type(exc).__name__)
        else:
            result["accepted"] += 1
            mark_push_success(subscription_id)
    result["ok"] = result["accepted"] > 0
    result["errors"] = result["errors"][:3]
    return result


def send_test_push_for_user(user_id: int) -> dict:
    with user_operation(user_id):
        return _deliver_payload(user_id, _notification_payload(
            title="Уведомления работают",
            body="Тестовый push от Личного секретаря.",
            tag=f"push-test-{int(datetime.now(timezone.utc).timestamp())}",
        ))


def _send_review(user_id: int, text: str, tag: str) -> bool:
    return _deliver_payload(user_id, _notification_payload(
        title="Личный секретарь", body=text[:1600], tag=tag,
        url="/?review=evening" if "evening" in tag else "/?review=morning",
    ))["ok"]


def _sync_attention_if_due(user_id: int, now: datetime) -> None:
    stamp = now.timestamp()
    last = _attention_sync_at.get(int(user_id), 0.0)
    if stamp - last < ATTENTION_SYNC_SECONDS:
        return
    sync_attention_context(user_id, now=now)
    _attention_sync_at[int(user_id)] = stamp


def _dispatch_attention_pushes(user_id: int, now: datetime) -> tuple[int, int]:
    if not get_assistant_preferences(user_id).get("attention_push_enabled", False):
        return 0, 0
    _sync_attention_if_due(user_id, now)
    delivered = failed = 0
    for item in pending_attention_pushes(user_id, limit=2):
        title = "Требует внимания" if item.get("priority") == "high" else "Личный секретарь"
        body = str(item.get("title") or "")
        detail = str(item.get("body") or "").strip()
        if detail:
            body = f"{body}\n{detail}"
        payload = _notification_payload(
            title=title,
            body=body[:1600],
            tag=f"attention-{int(item['attention_id'])}",
            url=f"/?view=today&attention={int(item['attention_id'])}",
        )
        result = _deliver_payload(user_id, payload)
        if result["accepted"]:
            mark_attention_push_result(user_id, item["attention_id"], success=True)
            delivered += 1
        else:
            error = (result["errors"] or ["No active push subscriptions"])[0]
            mark_attention_push_result(user_id, item["attention_id"], success=False, error=error)
            failed += 1
    return delivered, failed


def dispatch_due_reminders_once(limit: int = 50) -> dict[str, int]:
    stats = {"claimed": 0, "delivered": 0, "released": 0, "subscriptions_removed": 0,
             "repeated": 0, "reviews": 0, "attention_pushed": 0, "attention_failed": 0}
    now = datetime.now(timezone.utc)
    for user_id in list_push_user_ids():
        if stats["claimed"] >= limit:
            break
        try:
            with user_operation(user_id, blocking=False):
                if not get_google_account(user_id) or quiet_until(user_id, now):
                    continue
                reminders = claim_due_for_push([user_id], limit=limit - stats["claimed"])
                stats["claimed"] += len(reminders)
                for claimed in reminders:
                    reminder = get_saved_reminder(user_id, claimed["reminder_id"])
                    if not reminder or reminder["status"] != "delivering":
                        continue
                    payload = _notification_payload(
                        title="Напоминание", body=reminder["text"][:1600],
                        tag=f"reminder-{reminder['reminder_id']}",
                        reminder_id=reminder["reminder_id"],
                    )
                    result = _deliver_payload(user_id, payload)
                    stats["subscriptions_removed"] += result["removed"]
                    if result["accepted"]:
                        if complete_push_delivery(reminder["reminder_id"]):
                            activate_repeats(reminder, now)
                            stats["delivered"] += 1
                    else:
                        error = (result["errors"] or ["No active push subscriptions"])[0]
                        if result["subscriptions"] > result["removed"]:
                            postpone_transport(reminder, now)
                        release_push_delivery(reminder["reminder_id"], error)
                        stats["released"] += 1
                for attempt in claim_repeat_attempts(user_id, now):
                    reminder = get_saved_reminder(user_id, attempt["reminder_id"])
                    if not reminder or reminder["status"] != "delivered":
                        continue
                    _deliver_payload(user_id, _notification_payload(
                        title=f"Напоминание · повтор {attempt['attempt']}",
                        body=reminder["text"][:1600], tag=f"reminder-{reminder['reminder_id']}",
                        reminder_id=reminder["reminder_id"],
                    ))
                    stats["repeated"] += 1
                stats["reviews"] += deliver_reviews_for_user(user_id, _send_review, now=now)
                attention_delivered, attention_failed = _dispatch_attention_pushes(user_id, now)
                stats["attention_pushed"] += attention_delivered
                stats["attention_failed"] += attention_failed
        except UserBusyError:
            continue
        except Exception as exc:
            logger.error("Reminder dispatch failed for user %s (%s)", user_id, type(exc).__name__)
    return stats


def _worker_loop() -> None:
    interval = max(2, int(WEB_PUSH_WORKER_INTERVAL_SECONDS))
    while True:
        try:
            stats = dispatch_due_reminders_once()
            if stats["claimed"] or stats["attention_pushed"]:
                logger.info("Reminder/attention Web Push dispatch: %s", stats)
        except Exception:
            logger.exception("Reminder Web Push worker iteration failed")
        sleep(interval)


def start_reminder_push_worker() -> None:
    global _worker_started
    if not WEB_PUSH_WORKER_ENABLED:
        return
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(
            target=_worker_loop,
            name="reminder-web-push",
            daemon=True,
        )
        thread.start()
        _worker_started = True
