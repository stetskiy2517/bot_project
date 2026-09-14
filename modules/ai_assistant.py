"""LLM fallback for messages not handled by deterministic product modules."""

from __future__ import annotations

import logging

from integrations.ai import AIError, complete, get_ai_status, is_ai_available

logger = logging.getLogger(__name__)

UNHANDLED_WEB_MESSAGE = "Не понял команду. Сформулируй её иначе или уточни, что нужно сделать."

_SYSTEM_PROMPT = """Ты ИИ-модуль приложения «Личный секретарь».
Отвечай по-русски, коротко и по делу.
До тебя сообщение уже прошло через обычные модули календаря, напоминаний, заметок и задач.
Ты не выполняешь действия в приложении сам. Никогда не утверждай, что создал, изменил или удалил событие, заметку, задачу или напоминание.
Если пользователь явно просит выполнить действие в приложении, а запрос дошёл до тебя, скажи, что команду не удалось разобрать, и предложи короткую более понятную формулировку.
На обычные вопросы и разговорные сообщения отвечай как личный помощник.
Не выдумывай сведения о календаре, заметках, напоминаниях, пользователе или внешнем мире, если их нет в сообщении.
Не проси присылать пароли, API-ключи и другие секреты.
Если речь о лекарствах, можешь помочь организовать напоминание, но не назначай препарат, дозировку или схему приёма.
"""


def ai_status() -> dict:
    return get_ai_status()


def answer_unhandled(text: str) -> str | None:
    candidate = " ".join(str(text or "").split()).strip()
    if not candidate or not is_ai_available():
        return None
    try:
        return complete(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": candidate[:10000]},
            ],
            temperature=0.2,
        )
    except AIError as exc:
        logger.warning("AI fallback failed: %s", exc)
        return None


def replace_unhandled_reply(replies: list, answer: str) -> list:
    replaced = False
    result = []
    for item in replies:
        if item == UNHANDLED_WEB_MESSAGE and not replaced:
            result.append(answer)
            replaced = True
        else:
            result.append(item)
    return result
