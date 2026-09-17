# AI Personal Secretary

Web-first personal assistant with calendar, reminders, notes, tasks, voice input, navigation helpers, email tools, memory, and life-balance analytics.

## Language support

Planner input accepts Russian and English text. AssemblyAI voice recognition uses automatic Russian/English language detection. English commands are normalized into the same deterministic planner pipeline used for Russian commands, so both languages share the same validation and action logic.

## Categories

New users start with the built-in semantic categories: work, health, rest, travel, family, personal, and other. These are starter categories rather than a fixed UI list.

Users can rename or delete starter categories and create their own categories in Settings. Renaming a starter category keeps its semantic meaning, so automatic classification continues to work. Custom categories are available for manual assignment to calendar events. Removing a category does not remove calendar events; events that still reference a removed category remain uncategorized until the user selects another category.

The Life Wheel uses the same active category registry and the same Russian/English classifier as the calendar.
