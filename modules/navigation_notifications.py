"""Web Push notifications for navigation updates."""

from __future__ import annotations

import logging

from pywebpush import WebPushException

from config import BASE_URL
from core.attention_store import mark_attention_push_result, upsert_attention_item
from core.push_store import (
    delete_push_subscription_by_id,
    list_push_subscriptions,
    mark_push_error,
    mark_push_success,
)
from integrations.web_push import send_web_push, web_push_error_details

logger = logging.getLogger(__name__)


def _navigate_url(path: str = "/") -> str:
    base = str(BASE_URL or "").strip().rstrip("/")
    suffix = str(path or "/").strip() or "/"
    if not suffix.startswith("/"):
        suffix = f"/{suffix}"
    return f"{base}{suffix}" if base else suffix


def _payload(title: str, body: str, tag: str, *, path: str = "/") -> dict:
    return {
        "web_push": 8030,
        "notification": {
            "title": title,
            "lang": "ru-RU",
            "dir": "ltr",
            "body": body,
            "navigate": _navigate_url(path),
            "silent": False,
            "tag": tag,
            "data": {"url": path},
        },
    }


def _attention_for_navigation_alert(user_id: int, title: str, body: str, tag: str) -> dict | None:
    if title == "Пора выезжать":
        source_type = "navigation_leave_now"
        priority = "high"
    elif title == "Маршрут изменился":
        source_type = "navigation_route_change"
        priority = "normal"
    else:
        return None
    source_key = str(tag or "").removeprefix("navigation-").strip() or str(tag or "navigation")
    return upsert_attention_item(
        user_id,
        source_type=source_type,
        source_key=source_key,
        category="navigation",
        priority=priority,
        title=title,
        body=body,
        action_type="none",
    )


def send_navigation_push_for_user(user_id: int, *, title: str, body: str, tag: str) -> bool:
    attention_item = None
    try:
        attention_item = _attention_for_navigation_alert(user_id, title, body, tag)
    except Exception:
        logger.exception("Failed to persist navigation attention for user %s", user_id)

    subscriptions = list_push_subscriptions(user_id)
    if not subscriptions:
        return False

    path = "/"
    if attention_item and attention_item.get("attention_id"):
        path = f"/?view=today&attention={int(attention_item['attention_id'])}"
    accepted = 0
    payload = _payload(title, body, tag, path=path)
    for subscription in subscriptions:
        subscription_id = subscription["subscription_id"]
        try:
            send_web_push(subscription, payload)
        except WebPushException as exc:
            status_code, details = web_push_error_details(exc)
            message = f"Web Push error {status_code or 'unknown'}"
            if details:
                message = f"{message}: {details}"
            if status_code in {404, 410}:
                delete_push_subscription_by_id(subscription_id)
            else:
                mark_push_error(subscription_id, message[:1000])
            logger.warning(
                "Navigation push failed for subscription %s user %s: %s",
                subscription_id,
                user_id,
                message,
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {str(exc)[:400]}"
            mark_push_error(subscription_id, message)
            logger.exception(
                "Unexpected navigation push failure for subscription %s user %s",
                subscription_id,
                user_id,
            )
        else:
            accepted += 1
            mark_push_success(subscription_id)
    if accepted and attention_item and attention_item.get("attention_id"):
        try:
            mark_attention_push_result(
                user_id,
                int(attention_item["attention_id"]),
                success=True,
            )
        except Exception:
            logger.exception("Failed to mark navigation attention push for user %s", user_id)
    return accepted > 0
