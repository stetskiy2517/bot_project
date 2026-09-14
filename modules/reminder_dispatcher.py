"""Background delivery of standalone reminders through Web Push."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from time import sleep

from pywebpush import WebPushException

from config import BASE_URL, WEB_PUSH_ENABLED, WEB_PUSH_WORKER_INTERVAL_SECONDS
from core.push_store import (
    delete_push_subscription_by_id,
    list_push_subscriptions,
    list_push_user_ids,
    mark_push_error,
    mark_push_success,
)
from core.reminder_store import claim_due_for_push, complete_push_delivery, release_push_delivery
from integrations.web_push import send_web_push, web_push_error_details

logger = logging.getLogger(__name__)

_worker_lock = threading.Lock()
_worker_started = False


def _push_error_message(exc: WebPushException) -> tuple[int | None, str]:
    status_code, body = web_push_error_details(exc)
    label = f"Web Push error {status_code or 'unknown'}"
    if body:
        label = f"{label}: {body}"
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


def send_test_push_for_user(user_id: int) -> dict:
    """Send an immediate diagnostic notification without touching reminder state."""
    subscriptions = list_push_subscriptions(user_id)
    if not subscriptions:
        return {
            "ok": False,
            "subscriptions": 0,
            "accepted": 0,
            "failed": 0,
            "errors": ["На этом устройстве нет активной push-подписки."],
        }

    accepted = 0
    failed = 0
    removed = 0
    errors: list[str] = []
    payload = _notification_payload(
        title="Уведомления работают",
        body="Тестовый push от Личного секретаря.",
        tag=f"push-test-{int(datetime.now(timezone.utc).timestamp())}",
    )

    for subscription in subscriptions:
        subscription_id = subscription["subscription_id"]
        try:
            send_web_push(subscription, payload)
        except WebPushException as exc:
            status_code, message = _push_error_message(exc)
            if status_code in {404, 410}:
                delete_push_subscription_by_id(subscription_id)
                removed += 1
            else:
                mark_push_error(subscription_id, message)
            failed += 1
            errors.append(message)
            logger.warning(
                "Web Push test failed for subscription %s user %s: %s",
                subscription_id,
                user_id,
                message,
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {str(exc)[:400]}"
            mark_push_error(subscription_id, message)
            failed += 1
            errors.append(message)
            logger.exception(
                "Unexpected Web Push test failure for subscription %s user %s",
                subscription_id,
                user_id,
            )
        else:
            accepted += 1
            mark_push_success(subscription_id)

    return {
        "ok": accepted > 0,
        "subscriptions": len(subscriptions),
        "accepted": accepted,
        "failed": failed,
        "removed": removed,
        "errors": errors[:3],
    }


def _dispatch_due_user(user_ids: list[int], limit: int) -> dict[str, int]:
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
        payload = _notification_payload(
            title="Напоминание",
            body=reminder["text"],
            tag=f"reminder-{reminder['reminder_id']}",
            reminder_id=reminder["reminder_id"],
        )

        for subscription in subscriptions:
            subscription_id = subscription["subscription_id"]
            try:
                send_web_push(subscription, payload)
            except WebPushException as exc:
                status_code, last_error = _push_error_message(exc)
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
                last_error = f"{type(exc).__name__}: {str(exc)[:400]}"
                mark_push_error(subscription_id, last_error)
                logger.exception(
                    "Unexpected Web Push failure for subscription %s user %s",
                    subscription_id,
                    reminder["user_id"],
                )
            else:
                accepted += 1
                mark_push_success(subscription_id)

        if accepted:
            complete_push_delivery(reminder["reminder_id"])
            delivered += 1
            continue

        if transient_failures:
            release_push_delivery(reminder["reminder_id"], last_error or "Push delivery failed")
            released += 1
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



def dispatch_due_reminders_once(limit: int = 50) -> dict[str, int]:
    from core.user_operations import UserBusyError, user_operation
    from modules.assistant_commands import quiet_now

    total = {"claimed": 0, "delivered": 0, "released": 0, "subscriptions_removed": 0}
    for user_id in list_push_user_ids():
        if total["claimed"] >= limit:
            break
        try:
            with user_operation(user_id, timeout=0):
                if quiet_now(user_id):
                    continue
                stats = _dispatch_due_user([user_id], limit - total["claimed"])
                for name, value in stats.items():
                    total[name] += value
        except UserBusyError:
            continue
    return total


def _worker_loop() -> None:
    interval = max(2, int(WEB_PUSH_WORKER_INTERVAL_SECONDS))
    while True:
        try:
            stats = dispatch_due_reminders_once()
            from modules.assistant_notifications import dispatch_assistant_notifications

            dispatch_assistant_notifications()
            if stats["claimed"]:
                logger.info("Reminder Web Push dispatch: %s", stats)
        except Exception:
            logger.exception("Reminder Web Push worker iteration failed")
        sleep(interval)


def start_reminder_push_worker() -> None:
    global _worker_started
    if not WEB_PUSH_ENABLED:
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
