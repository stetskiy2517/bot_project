"""Runtime bridge between semantic auto-categories and a user's active set."""

from __future__ import annotations

import sys


def install_category_detector_guard() -> None:
    from modules import calendar

    current = calendar._detect_category
    if getattr(current, "_user_category_guard", False):
        return

    original = current

    def guarded_detect_category(
        text: str,
        category_colors: dict[str, str | None] | None = None,
    ) -> tuple[str, str | None]:
        result = original(text, category_colors)
        if category_colors is None:
            return result
        category = result[0]
        if category in category_colors:
            return category, category_colors.get(category)
        if "other" in category_colors:
            return "other", category_colors.get("other")
        return "uncategorized", None

    guarded_detect_category._user_category_guard = True
    guarded_detect_category._original_detector = original
    calendar._detect_category = guarded_detect_category

    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("modules.") or module is None:
            continue
        if getattr(module, "_detect_category", None) is current:
            setattr(module, "_detect_category", guarded_detect_category)
