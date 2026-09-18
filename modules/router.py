"""Bilingual entrypoint for the central planner router.

The established Russian deterministic router stays unchanged in router_core.
English user input is normalized once here and then follows the same execution
path, validation and safety checks as Russian input.
"""

from __future__ import annotations

import logging

from core.category_store import install_dynamic_category_support
from modules.category_runtime import install_category_detector_guard
from modules.language_support import (
    canonicalize_english,
    detect_input_language,
    install_english_category_support,
)

logger = logging.getLogger(__name__)

# Patch shared category lookups before planner modules bind them, then wrap the
# bilingual classifier so deleted categories are not silently resurrected.
install_dynamic_category_support()
install_english_category_support()
install_dynamic_category_support()
install_category_detector_guard()

from modules import router_core as _impl  # noqa: E402

# Keep the public/private API compatible with existing imports and tests.
_CORE_EXPORTS = {
    name for name in dir(_impl)
    if not name.startswith("__")
}
for _name in _CORE_EXPORTS:
    globals()[_name] = getattr(_impl, _name)

# English bare event/action nouns need to reach the same deterministic intent
# rules when the user does not start with an explicit "schedule" command.
_impl.EVENT_WORDS = tuple(dict.fromkeys((
    *_impl.EVENT_WORDS,
    "meeting", "appointment", "call", "doctor", "dentist", "gym", "workout",
    "training", "movie", "cinema", "restaurant", "flight", "train", "taxi",
    "conference", "client", "lunch", "dinner", "breakfast", "trip", "walk",
    "run", "theater", "theatre", "concert", "museum", "pool", "football",
    "match", "massage", "haircut", "barber", "lesson", "lecture", "exam",
    "yoga", "pilates", "birthday",
)))
_impl.BARE_CREATE_EVENT_WORDS = tuple(dict.fromkeys((
    *_impl.BARE_CREATE_EVENT_WORDS,
    "meeting", "appointment", "call", "doctor", "dentist", "gym", "workout",
    "training", "movie", "cinema", "conference", "lunch", "dinner",
    "breakfast", "trip", "walk", "run", "theater", "theatre", "concert",
    "museum", "pool", "football", "match", "massage", "haircut", "barber",
    "lesson", "lecture", "exam", "yoga", "pilates", "birthday",
)))
_impl.ACTION_WORDS = tuple(dict.fromkeys((
    *_impl.ACTION_WORDS,
    "buy", "pick up", "pickup", "take", "pay", "call", "go", "send",
    "prepare", "submit", "order", "book", "meet", "check", "finish",
    "collect", "deliver", "medicine", "tablet",
)))

# Re-export mutated tuples as well.
EVENT_WORDS = _impl.EVENT_WORDS
BARE_CREATE_EVENT_WORDS = _impl.BARE_CREATE_EVENT_WORDS
ACTION_WORDS = _impl.ACTION_WORDS

_original_detect_intent = _impl.detect_intent
_original_resume_pending = _impl._resume_pending


def detect_intent(text: str):
    """Detect intent for Russian or English text."""
    return _original_detect_intent(canonicalize_english(text))


# Internal calls in router_core must use the bilingual detector too.
_impl.detect_intent = detect_intent

_WRAPPER_NAMES = {"route_text", "handle_text", "detect_intent"}


def _sync_runtime_overrides() -> None:
    """Propagate monkey-patched facade symbols to router_core.

    Existing tests and integrations patch functions on modules.router. Keeping
    that behavior avoids a compatibility break after moving the original router
    implementation behind this facade.
    """
    for name in _CORE_EXPORTS:
        if name in _WRAPPER_NAMES:
            continue
        if name not in globals():
            continue
        value = globals()[name]
        if getattr(_impl, name, None) is not value:
            setattr(_impl, name, value)


async def _resume_pending(update, context, text: str) -> bool:
    """Compatibility bridge for tests/transports patching modules.router."""
    _sync_runtime_overrides()
    return await _original_resume_pending(update, context, canonicalize_english(text))


# The core router must resolve pending dialogs through the facade so patches on
# modules.router still affect the same call path as before the bilingual split.
_impl._resume_pending = _resume_pending


async def route_text(update, context, text: str | None = None) -> bool:
    """Normalize English commands and delegate to the established router."""
    if not getattr(update, "message", None):
        return False

    raw = (text if text is not None else getattr(update.message, "text", "") or "").strip()
    if not raw:
        return False

    canonical = canonicalize_english(raw)
    _sync_runtime_overrides()
    return await _impl.route_text(update, context, text=canonical)


async def handle_text(update, context) -> None:
    """Telegram text entrypoint with the same error boundary as the old router."""
    try:
        handled = await route_text(update, context)
        if handled:
            return
        if getattr(update, "message", None):
            await update.message.reply_text(
                "Не понял команду. Например: «врач завтра в 19:00» "
                "или «напомни через 30 минут позвонить»."
            )
    except Exception:
        logger.exception("Unhandled error in text router")
        if getattr(update, "message", None):
            language = detect_input_language(getattr(update.message, "text", "") or "")
            await update.message.reply_text(
                "I couldn't process the message. Please try again."
                if language == "en"
                else "Не удалось обработать сообщение. Попробуйте ещё раз."
            )
