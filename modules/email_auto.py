"""Bounded background analysis of new email without repeated AI calls."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from core.db import conn, db_lock, get_user_timezone
from core.email_auto_store import (
    account_is_initialized,
    cleanup_email_auto_store,
    email_auto_enabled,
    initialize_account,
    mark_message_processed,
    message_fingerprint,
    message_was_processed,
    record_auto_run,
    touch_account_scan,
)
from core.email_store import list_email_accounts
from core.feature_access import has_ai_access
from integrations.ai import AIError, AIProviderError, complete, complete_structured, is_ai_available
from modules.email import DATE_OR_TIME_RE, PLANNING_SIGNAL_RE, _message_body, _read_account
from modules.email_actions import (
    EMAIL_PLAN_SCHEMA,
    SYSTEM_PROMPT,
    _merge_actions,
    _normalize_plan,
    _parse_json_object,
    _prompt,
)
from modules.email_actions_api import apply_high_confidence_attachment_actions
from modules.email_attachments import _attachment_type, analyze_email_attachments

logger = logging.getLogger(__name__)

SCAN_LIMIT_PER_ACCOUNT = 20
MAX_CANDIDATE_MESSAGES = 8
DEFAULT_INTERVAL_SECONDS = 15 * 60
ACTION_SIGNAL_RE = re.compile(
    r"\b(?:прошу|нужно|необходимо|согласова\w*|подписа\w*|отправ\w*|пришл\w*|"
    r"подтверд\w*|оплат\w*|ответ\w*|заполн\w*|дедлайн\w*|срок\w*|встреч\w*|"
    r"созвон\w*|брон\w*|рейс\w*|вылет\w*|поезд\w*|достав\w*)\b",
    re.IGNORECASE,
)

_worker_lock = threading.Lock()
_worker_started = False


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no", "disabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def email_auto_worker_enabled() -> bool:
    return _env_bool("EMAIL_AUTO_WORKER_ENABLED", True)


def _has_supported_attachment(message: dict) -> bool:
    for attachment in message.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        if _attachment_type(attachment.get("filename"), attachment.get("mime_type")) is not None:
            return True
    return False


def _has_text_planning_signal(message: dict) -> bool:
    subject = str(message.get("subject") or "")
    body = _message_body(message)
    text = f"{subject}\n{body}"[:7000]
    if not text.strip():
        return False
    if PLANNING_SIGNAL_RE.search(text) or ACTION_SIGNAL_RE.search(text):
        return True
    return bool(DATE_OR_TIME_RE.search(text) and re.search(r"\b(?:встреч|брон|рейс|поезд|срок|до|оплат|ответ|достав)\w*\b", text, re.IGNORECASE))


def _is_candidate(message: dict) -> tuple[bool, bool, bool]:
    attachment = _has_supported_attachment(message)
    text_signal = _has_text_planning_signal(message)
    return attachment or text_signal, attachment, text_signal


def _analyze_candidates(user_id: int, messages: list[tuple[dict, dict]], *, analyze_text: bool) -> tuple[dict, int]:
    timezone_name = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    attachment_result = {"actions": [], "warnings": [], "analyzed": 0, "detected": 0, "supported_found": 0}
    try:
        attachment_result = analyze_email_attachments(
            user_id,
            messages,
            user_timezone=timezone_name,
        )
    except Exception:
        logger.exception("Automatic email attachment analysis failed for user %s", user_id)
        attachment_result["warnings"] = ["Не удалось автоматически проверить часть вложений."]

    text_plan = {"summary": "", "actions": [], "draft_reply": None}
    ai_text_calls = 0
    if analyze_text:
        prompt = _prompt(
            user_id,
            "Автоматически проверь только эти новые письма. Извлеки реальные действия и сроки; ничего не выполняй сам.",
            messages[:6],
        )
        try:
            ai_text_calls = 1
            try:
                raw = complete_structured(
                    [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
                    EMAIL_PLAN_SCHEMA,
                    max_tokens=1000,
                )
            except AIProviderError:
                raw = _parse_json_object(
                    complete(
                        [
                            {"role": "system", "content": SYSTEM_PROMPT + " Верни только JSON, без Markdown."},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=1000,
                        temperature=0.05,
                    )
                )
            text_plan = _normalize_plan(raw, messages[:6])
        except (AIError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Automatic email text planning failed for user %s: %s", user_id, exc)
            text_plan = {"summary": "", "actions": [], "draft_reply": None}

    attachment_actions = list(attachment_result.get("actions") or [])
    actions = _merge_actions(list(text_plan.get("actions") or []), attachment_actions)
    summary = str(text_plan.get("summary") or "").strip()
    if not summary and attachment_actions:
        summary = "Новые вложения автоматически проверены на события и поездки."
    if not summary:
        summary = "Новых действий, которые можно уверенно предложить, не найдено."

    plan = {
        "summary": summary[:1400],
        "actions": actions,
        "draft_reply": None,
        "ai_used": bool(ai_text_calls or int(attachment_result.get("analyzed") or 0)),
        "attachment_analysis": {
            "enabled": True,
            "detected": int(attachment_result.get("detected") or 0),
            "analyzed": int(attachment_result.get("analyzed") or 0),
            "supported_found": int(attachment_result.get("supported_found") or 0),
            "warnings": [str(item)[:300] for item in (attachment_result.get("warnings") or [])],
        },
    }
    apply_high_confidence_attachment_actions(user_id, plan)
    return plan, ai_text_calls


def evaluate_user_email_auto(user_id: int) -> dict:
    metrics = {
        "new_messages": 0,
        "candidates": 0,
        "ai_text_calls": 0,
        "attachments_analyzed": 0,
        "auto_created": 0,
        "already_present": 0,
        "failed": 0,
    }
    if not email_auto_enabled(user_id) or not has_ai_access(user_id) or not is_ai_available():
        return metrics

    accounts = [item for item in list_email_accounts(user_id) if item.get("enabled")]
    candidates: list[tuple[dict, dict, str, bool]] = []

    for account in accounts:
        account_id = int(account["account_id"])
        try:
            messages = _read_account(user_id, account, query=None, unread_only=False, limit=SCAN_LIMIT_PER_ACCOUNT)
        except Exception as exc:
            touch_account_scan(user_id, account_id, error=type(exc).__name__)
            logger.warning("Automatic email scan failed user=%s account=%s (%s)", user_id, account_id, type(exc).__name__)
            continue

        if not account_is_initialized(user_id, account_id):
            initialize_account(user_id, account, messages)
            continue

        touch_account_scan(user_id, account_id)
        for message in messages:
            fingerprint = message_fingerprint(account, message)
            if message_was_processed(user_id, account_id, fingerprint):
                continue
            metrics["new_messages"] += 1
            candidate, has_attachment, text_signal = _is_candidate(message)
            if not candidate:
                mark_message_processed(
                    user_id,
                    account_id,
                    fingerprint,
                    message.get("provider_message_id"),
                    "ignored",
                    "Нет детерминированных признаков действия или поддерживаемого вложения.",
                )
                continue
            if len(candidates) >= MAX_CANDIDATE_MESSAGES:
                # Leave the message unseen by this subsystem so a later cycle can process it.
                continue
            candidates.append((account, message, fingerprint, text_signal))

    metrics["candidates"] = len(candidates)
    if not candidates:
        if metrics["new_messages"]:
            record_auto_run(user_id, metrics)
        return metrics

    message_pairs = [(account, message) for account, message, _, _ in candidates]
    analyze_text = any(text_signal for _, _, _, text_signal in candidates)
    try:
        plan, ai_text_calls = _analyze_candidates(user_id, message_pairs, analyze_text=analyze_text)
        metrics["ai_text_calls"] = ai_text_calls
        metrics["attachments_analyzed"] = int((plan.get("attachment_analysis") or {}).get("analyzed") or 0)
        auto = plan.get("auto_calendar") if isinstance(plan.get("auto_calendar"), dict) else {}
        metrics["auto_created"] = int(auto.get("created") or 0)
        metrics["already_present"] = int(auto.get("already_present") or 0)
        metrics["failed"] = int(auto.get("failed") or 0)
        for account, message, fingerprint, _ in candidates:
            mark_message_processed(
                user_id,
                int(account["account_id"]),
                fingerprint,
                message.get("provider_message_id"),
                "analyzed",
                "Новый кандидат проверен автоматическим почтовым анализом.",
            )
        record_auto_run(user_id, metrics, plan)
        return metrics
    except Exception as exc:
        logger.exception("Automatic email analysis failed for user %s", user_id)
        metrics["failed"] += len(candidates)
        # At-most-once by default: do not spend tokens on the same message forever after provider errors.
        for account, message, fingerprint, _ in candidates:
            mark_message_processed(
                user_id,
                int(account["account_id"]),
                fingerprint,
                message.get("provider_message_id"),
                "failed",
                f"Авторазбор не завершён: {type(exc).__name__}. Повтор возможен вручную.",
            )
        record_auto_run(user_id, metrics)
        return metrics


def _enabled_user_ids() -> list[int]:
    with db_lock:
        rows = conn.execute(
            """SELECT DISTINCT p.user_id
               FROM email_auto_preferences p
               JOIN email_accounts a ON a.user_id=p.user_id AND a.enabled=1
               WHERE p.enabled=1
               ORDER BY p.user_id
               LIMIT 1000"""
        ).fetchall()
    result = []
    for row in rows:
        user_id = int(row[0])
        try:
            if has_ai_access(user_id):
                result.append(user_id)
        except Exception:
            logger.exception("Could not check AI access for automatic email user %s", user_id)
    return result


def run_email_auto_cycle() -> dict:
    totals = {
        "users": 0,
        "new_messages": 0,
        "candidates": 0,
        "ai_text_calls": 0,
        "attachments_analyzed": 0,
        "auto_created": 0,
        "already_present": 0,
        "failed": 0,
    }
    try:
        cleanup_email_auto_store()
    except Exception:
        logger.exception("Automatic email cleanup failed")
    for user_id in _enabled_user_ids():
        try:
            result = evaluate_user_email_auto(user_id)
        except Exception:
            logger.exception("Automatic email cycle failed for user %s", user_id)
            continue
        totals["users"] += 1
        for key in totals:
            if key == "users":
                continue
            totals[key] += int(result.get(key, 0))
    return totals


def _worker_loop() -> None:
    interval = _env_int("EMAIL_AUTO_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS, 300, 3600)
    while True:
        try:
            run_email_auto_cycle()
        except Exception:
            logger.exception("Automatic email worker iteration failed")
        time.sleep(interval)


def start_email_auto_worker() -> None:
    global _worker_started
    if not email_auto_worker_enabled():
        logger.info("Automatic email worker disabled")
        return
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
        thread = threading.Thread(target=_worker_loop, name="email-auto-worker", daemon=True)
        thread.start()
        logger.info("Automatic email worker started")


__all__ = [
    "evaluate_user_email_auto",
    "run_email_auto_cycle",
    "start_email_auto_worker",
]
