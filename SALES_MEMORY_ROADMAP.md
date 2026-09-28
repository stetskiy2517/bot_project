# AI Sales Memory — MVP roadmap

Branch: `feature/sales-memory`

## Goal

Add a separate work-memory layer for companies, people, interactions and commitments without mixing it with the existing behavioural `user_memories` store.

## Existing foundation

- SQLite is the current application store (`core/db.py`).
- `core/memory_store.py` stores inferred user facts, preferences, habits and observations.
- `core/ai_memory_store.py` journals note/reminder/voice/calendar events for AI processing.
- Sales Memory must reference these systems, not replace or duplicate them.

## MVP entities

1. Company
   - name, aliases, industry, website, notes, status.
2. Contact
   - full name, company, position, phone, email, Telegram, notes, status.
3. Interaction
   - meeting/call/email/message/note, happened_at, summary, outcome, next_step.
   - optional source_type/source_id to point at calendar, email, note, task, etc.
4. Commitment
   - promise/action, due_at, status, company/contact, optional linked source.

Every row is scoped by `user_id`.

## Data rules

- No secrets/tokens in Sales Memory.
- Soft-delete business records where history matters.
- Do not let AI silently overwrite a manually edited contact/company field.
- AI extraction should propose changes first; deterministic validation writes them.
- Cross-module links use stable source identifiers instead of copying source data.

## Delivery stages

### Stage 1 — data layer
- SQLite tables and indexes.
- CRUD for companies/contacts.
- append interactions.
- create/complete commitments.
- unit tests and user isolation.

### Stage 2 — API
- authenticated endpoints for list/search/create/update.
- no UI redesign.

### Stage 3 — UI
- Clients screen.
- Company card.
- Contact card.
- interaction timeline.
- open commitments.

### Stage 4 — AI ingestion
- extract company/contact/commitment candidates from email, notes, voice and meetings.
- deduplicate candidates.
- require confirmation for uncertain identity merges.

### Stage 5 — planner integration
- commitments can create/link tasks and reminders.
- upcoming meetings show company/contact context.
- completed tasks can append an interaction/outcome.

## MVP acceptance

- User A cannot read or mutate User B records.
- Duplicate company/contact creation is handled predictably.
- Interaction history is append-only.
- Commitments retain completion history.
- Existing Planner/AI Memory behaviour remains unchanged.
- Full unit test suite passes before merge.
