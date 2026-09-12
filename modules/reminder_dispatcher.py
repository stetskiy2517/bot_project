"""Background delivery of standalone reminders through Web Push."""

from __future__ import annotations

import logging
import threading
from time import sleep

from pywebpush import WebPushException

from config import WEB_PUSH_WORKER_INTERVAL_SECONDS
from core.push_store import (
    delete_push_subscription_by_id,
    list_push_subscriptions,
    list_push_user_ids,
    mark_push_error,
    mark_push_success,
)
from core.reminder_store import claim_due_for_push, complete_push_delivery, release_push_delivery
from integrations.web_push import send_web_push

logger = logging.getLogger(__name__)

_worker_lock = threading.Lock()
_worker_started = False


def _push_status_code(exc: WebPushException) -> int | None:
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def dispatch_due_reminders_once(limit: int = 50) -> dict[str, int]:
    user_ids = list_push_user_ids()
    if not user_ids:
        return {"claimed": 0, "delivered": 0, "released": 0, "subscriptions_removed": 0}

    reminders = claim_due_for_push(user_ids, limit=limit)
    delivered = 0
    released = 0
    removed = 0

    for reminder in reminders:
        subscriptions = list_push_subscriptions(reminder["user_id"])
        if not subscriptions:
            release_push_delivery(reminder["reminder_id"], "No active push subscriptions")
            released += 1
            continue

        transient_failures = 0
        accepted = 0
        last_error = None
        payload = {
            "type": "reminder",
            "reminder_id": reminder["reminder_id"],
            "title": "Напоминание",
            "body": reminder["text"],
            "tag": f"reminder-{reminder['reminder_id']}",
            "url": "/",
        }

        for subscription in subscriptions:
            subscription_id = subscription["subscription_id"]
            try:
                send_web_push(subscription, payload)
            except WebPushException as exc:
                status_code = _push_status_code(exc)
                last_error = f"Web Push error {status_code or 'unknown'}"
                if status_code in {404, 410}:
                    delete_push_subscription_by_id(subscription_id)
                    removed += 1
                    logger.info(
                        "Removed expired Web Push subscription %s for user %s",
                        subscription_id,
                        reminder["user_id"],
                    )
                else:
                    transient_failures += 1
                    mark_push_error(subscription_id, last_error)
                    logger.warning(
                        "Web Push failed for subscription %s user %s: %s",
                        subscription_id,
                        reminder["user_id"],
                        last_error,
                    )
            except Exception as exc:
                transient_failures += 1
                last_error = type(exc).__name__
                mark_push_error(subscription_id, last_error)
                logger.exception(
                    "Unexpected Web Push failure for subscription %s user %s",
                    subscription_id,
                    reminder["user_id"],
                )
            else:
                accepted += 1
                mark_push_success(subscription_id)

        if transient_failures:
            release_push_delivery(reminder["reminder_id"], last_error or "Push delivery failed")
            released += 1
            continue

        if accepted:
            complete_push_delivery(reminder["reminder_id"])
            delivered += 1
            continue

        # Every subscription was permanently expired. Keep the reminder pending so it
        # can still be shown when the user opens the app and subscribes again.
        release_push_delivery(reminder["reminder_id"], "No valid push subscriptions")
        released += 1

    return {
        "claimed": len(reminders),
        "delivered": delivered,
        "released": released,
        "subscriptions_removed": removed,
    }


def _worker_loop() -> None:
    interval = max(2, int(WEB_PUSH_WORKER_INTERVAL_SECONDS))
    while True:
        try:
            stats = dispatch_due_reminders_once()
            if stats["claimed"]:
                logger.info("Reminder Web Push dispatch: %s", stats)
        except Exception:
            logger.exception("Reminder Web Push worker iteration failed")
        sleep(interval)


def start_reminder_push_worker() -> None:
    global _worker_started
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
