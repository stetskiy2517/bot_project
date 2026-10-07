"""Bounded, Unicode-aware SQL text matching shared by local search."""
import re


def normalize(value):
    return str(value or "").casefold().replace("ё", "е")


def tokens(query):
    return re.findall(r"[\w@.+-]+", normalize(query))[:16]


def predicate(fields, words):
    text = " || ' ' || ".join(f"COALESCE({field},'')" for field in fields)
    return " AND ".join(f"instr(search_normalize({text}),?)>0" for _ in words) or "0", list(words)


def register(connection):
    connection.create_function("search_normalize", 1, normalize, deterministic=True)
