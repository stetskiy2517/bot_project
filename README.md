# AI Personal Secretary

## Language support

Planner input accepts Russian and English text. AssemblyAI voice recognition uses automatic Russian/English language detection. English commands are normalized into the same deterministic planner pipeline used for Russian commands, so both languages share the same validation and action logic.

## Categories

New users start with the built-in semantic categories: work, health, rest, travel, family, personal, and other. These are starter categories rather than a fixed UI list.

Users can rename or delete starter categories and create their own categories in Settings. Renaming a starter category keeps its semantic meaning, so automatic classification continues to work. Custom categories are available for manual assignment to calendar events. Removing a category does not remove calendar events; events that still reference a removed category remain uncategorized until the user selects another category.

The Life Wheel uses the same active category registry and the same Russian/English classifier as the calendar.


## Development rule: systemic fixes first

When a bug exposes a shared architectural problem, fix the shared layer instead of adding a module-specific workaround.

- Calendar, tasks, reminders, notes, files and UI entry points must use the same conversation context.
- Do not add new `last_*`, `active_*` or parallel pending-state keys for individual modules.
- A follow-up such as “его”, “эту”, “перенеси”, “измени длительность” or “удали” must resolve through the shared context before a module invents its own reference logic.
- Local guards are acceptable only for genuinely local validation. They must not duplicate cross-module state or routing rules.
- Every systemic regression fix should include a cross-module regression test.
