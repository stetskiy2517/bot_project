"""Shared safeguards for conversational routing and pending interactions.

This module keeps high-level dialogue policy independent from product modules:
- factual first-person statements are not treated as implicit calendar writes;
- an unfinished prompt only captures replies that plausibly belong to it;
- a clearly new command can interrupt stale pending state.
"""

from __future__ import annotations

import re


FIRST_PERSON_STATEMENT_RE = re.compile(
    r"^\s*(?:я|мы|у\s+меня|у\s+нас|мне|нам)\b",
    re.IGNORECASE,
)

CLEAR_NEW_COMMAND_RE = re.compile(
    r"^\s*(?:"
    r"покажи\s+(?:календар\w*|расписан\w*|напоминани\w*|заметк\w*|задач\w*)|"
    r"что\s+у\s+меня\b|когда\s+у\s+меня\b|во\s+сколько\b|"
    r"когда\s+(?:я\s+)?свобод\w*\b|(?:найди|найти)\s+(?:встреч\w*|событ\w*|созвон\w*|"
    r"окн\w*|время|заметк\w*)|"
    r"(?:создай|создать|добавь|добавить|поставь|поставить|запланируй|запланировать|назначь|назначить)\s+"
    r"(?:встреч\w*|событ\w*|созвон\w*|напоминани\w*|заметк\w*|задач\w*)|"
    r"(?:напомни|напомнить|не\s+забудь)\b|"
    r"(?:удали|удалить|убери|убрать|отмени|отменить|измени|изменить|поменяй|поменять|"
    r"перенеси|перенести|переименуй|переименовать)\s+"
    r"(?:встреч\w*|событ\w*|созвон\w*|напоминани\w*|заметк\w*|задач\w*)|"
    r"(?:задач\w*|заметк\w*)\s*[:\-]"
    r")",
    re.IGNORECASE,
)

CONTROL_REPLIES = {
    "да", "ага", "подтверждаю", "подтвердить", "создавай", "удаляй", "меняй", "ок", "окей",
    "нет", "не надо", "отмена", "отменить", "стоп",
}

ORDINAL_RE = re.compile(
    r"^(?:перв\w*|втор\w*|трет\w*|четверт\w*|пят\w*)$",
    re.IGNORECASE,
)

REFERENCE_RE = re.compile(
    r"^(?:это|его|ее|её|эту|этот|последн\w*|перв\w*|втор\w*|трет\w*|четверт\w*|пят\w*)$",
    re.IGNORECASE,
)

SCOPE_RE = re.compile(
    r"^(?:только\s+(?:это|эту|одну|один)|(?:это|эту)\s+и\s+все\s+будущ\w*|"
    r"все\s+будущ\w*|всю\s+серию|весь\s+цикл|все\s+повторени\w*)$",
    re.IGNORECASE,
)

TIME_OR_DATE_RE = re.compile(
    r"\b(?:сегодня|завтра|послезавтра|понедельник\w*|вторник\w*|сред\w*|четверг\w*|"
    r"пятниц\w*|суббот\w*|воскресень\w*|через\b|полдень|полночь|"
    r"(?:в|к)\s*(?:[01]?\d|2[0-3])(?:(?::|\.)[0-5]\d)?|"
    r"\d{1,2}[./-]\d{1,2})\b",
    re.IGNORECASE,
)

SELECTION_PENDING_TYPES = {
    "select_delete", "select_update", "free_slot_choice",
    "reminder_select_edit", "reminder_select_delete",
    "note_select_delete", "note_select_append",
    "task_select_complete", "task_select_delete",
}

CONFIRM_PENDING_TYPES = {
    "confirm_create_conflict", "confirm_delete", "confirm_delete_many",
    "confirm_update", "confirm_update_future", "confirm_free_slot",
}

SCOPE_PENDING_TYPES = {
    "select_recurring_delete_scope", "select_recurring_update_scope",
}

TIME_PENDING_TYPES = {"create_time", "reminder_time", "template_when"}

# These flows intentionally accept normal prose as the requested payload. We still
# allow an unmistakably new product command to interrupt them.
FREEFORM_PENDING_TYPES = {
    "free_slot_title", "note_text", "note_append_target", "note_append_text",
    "reminder_edit", "reminder_delete_query",
}


def _normalise(text: str) -> str:
    return " ".join(str(text or "").casefold().replace("ё", "е").split()).strip(" .,!?:;«»\"'")


def is_declarative_statement(text: str) -> bool:
    """Return True for common first-person factual statements.

    Explicit command verbs are handled by the router before this guard. This
    function only prevents the fallback date/time heuristics from turning facts
    such as «я принимаю таблетки завтра в 23» into calendar writes.
    """
    return bool(FIRST_PERSON_STATEMENT_RE.match(str(text or "")))


def is_clear_new_command(text: str) -> bool:
    return bool(CLEAR_NEW_COMMAND_RE.match(str(text or "")))


def should_resume_pending(pending: dict | None, text: str) -> bool:
    """Decide whether ``text`` belongs to the active pending interaction.

    Pending state is deliberately conservative: ambiguous prose stays inside the
    current flow, while an unmistakable new command interrupts it. This prevents
    confirmations and numbered selections from swallowing unrelated requests.
    """
    if not isinstance(pending, dict):
        return False

    pending_type = str(pending.get("type") or "")
    normal = _normalise(text)
    if not normal:
        return True

    if normal in CONTROL_REPLIES or normal.isdigit() or ORDINAL_RE.fullmatch(normal):
        return True

    if pending_type in SCOPE_PENDING_TYPES:
        if SCOPE_RE.fullmatch(normal):
            return True
        return not is_clear_new_command(text)

    if pending_type in TIME_PENDING_TYPES:
        if TIME_OR_DATE_RE.search(normal):
            return True
        return not is_clear_new_command(text)

    if pending_type in SELECTION_PENDING_TYPES:
        if REFERENCE_RE.fullmatch(normal):
            return True
        return not is_clear_new_command(text)

    if pending_type in CONFIRM_PENDING_TYPES:
        return not is_clear_new_command(text)

    if pending_type in FREEFORM_PENDING_TYPES:
        return not is_clear_new_command(text)

    # Unknown pending types keep their previous behaviour. A new feature must opt
    # into stricter interruption semantics explicitly rather than being broken by
    # a generic router rule.
    return True
