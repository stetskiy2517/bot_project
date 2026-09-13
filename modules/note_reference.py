"""Contextual resolver for read-only references to existing notes.

This module is deliberately separate from calendar routing. It extracts a likely
note topic from ordinary speech and confirms generic references against the
current user's stored notes before the router diverts the message from the
calendar.
"""

from __future__ import annotations

import re

from core.note_store import search_notes


_EXPLICIT_PATTERNS = (
    re.compile(
        r"^\s*что\s+(?:у\s+меня\s+)?(?:записано|сохранено)\s+"
        r"(?:(?:в|по|про|о|об)\s+)?(?P<query>.+?)\s*[?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*что\s+(?:я\s+)?(?:записал(?:а)?|записывал(?:а)?|сохранил(?:а)?|сохранял(?:а)?)\s+"
        r"(?:(?:в|по|про|о|об)\s+)?(?P<query>.+?)\s*[?.!]*$",
        re.IGNORECASE,
    ),
)

_GENERIC_PATTERNS = (
    re.compile(
        r"^\s*(?:что|чего)\s+(?:у\s+меня\s+)?(?:есть\s+)?(?:в|по|про)\s+"
        r"(?P<query>.+?)\s*[?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:покажи|открой|найди)\s+(?:мне\s+)?"
        r"(?:(?:мой|мою|мое|моё|мои|моём|моем)\s+)?(?P<query>.+?)\s*[?.!]*$",
        re.IGNORECASE,
    ),
)

_LEADING_FILLER_RE = re.compile(
    r"^\s*(?:(?:мой|мою|мое|моё|мои|моём|моем)\s+|(?:заметке|заметку|заметка|записи|запись)\s+)+",
    re.IGNORECASE,
)


def _clean_query(value: str) -> str:
    query = value.strip(" \t\r\n.,!?;:…\"'«»")
    query = _LEADING_FILLER_RE.sub("", query).strip(" \t\r\n.,!?;:…\"'«»")
    query = re.sub(r"\s+", " ", query)
    return query


def extract_note_reference(text: str) -> tuple[str, bool] | None:
    """Return ``(query, explicit_note_language)`` for a read-like phrase."""
    for pattern in _EXPLICIT_PATTERNS:
        match = pattern.search(text)
        if match:
            query = _clean_query(match.group("query"))
            return (query, True) if query else None

    for pattern in _GENERIC_PATTERNS:
        match = pattern.search(text)
        if match:
            query = _clean_query(match.group("query"))
            return (query, False) if query else None
    return None


def resolve_note_reference(user_id: int, text: str, *, allow_generic: bool = True) -> str | None:
    """Resolve a contextual read request to a note-search query.

    Explicit storage language (``что я записывал...``) always belongs to notes.
    Generic language (``что у меня по проекту Альфа``) is accepted only when
    ``allow_generic`` is true and a matching note exists for this user.
    """
    extracted = extract_note_reference(text)
    if not extracted:
        return None
    query, explicit = extracted
    if explicit:
        return query
    if not allow_generic:
        return None
    return query if search_notes(user_id, query, limit=1) else None
