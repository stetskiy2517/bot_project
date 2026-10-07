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

## Search and commitment next steps

Use the search button in the top bar or Ctrl/Cmd+K to search local notes, tasks,
personal facts, companies, contacts, interaction history and commitments.
Text matches and related records appear separately. Each group supports loading
older results; opening a note/task also updates the shared conversation context.
Email and external calendar search are not included.

In Settings → Memory, both personal and work lists support loading older records.
Open a commitment to create a task or reminder after explicit confirmation. Times
use the account timezone. The new action retains company/contact context and a
link to the commitment. Repeated confirmation returns the existing action.
Completing or reopening the linked task/reminder updates the commitment; merely
delivering a notification does not complete it. Deleting an action frees its link
so an open commitment can create a replacement.

## GigaChat certificate trust

Before deploying this version, configure one trust source in the server environment:

- `GIGACHAT_CA_BUNDLE`: an absolute path to a PEM trust bundle obtained and verified
  by the operator (include public roots if needed for the auth endpoint).
- Or `GIGACHAT_ROOT_SHA256`: the 64 hexadecimal characters of the trusted root's
  DER SHA-256 fingerprint, verified through an independent trusted channel. The
  application downloads the certificate using verified HTTPS, checks the pin and
  validity period, then builds its isolated bundle with public roots.

For an already independently verified certificate, obtain its DER fingerprint with
`openssl x509 -in trusted-root.pem -noout -fingerprint -sha256`; remove colons from
the value. Computing a fingerprint of an unverified download does not establish
trust. The old automatically generated cache is rebuilt only from a pinned root.
TLS verification is never disabled. Without a trust source, AI calls fail safely
and diagnostics report the error; local deterministic functions remain available.

## Runtime diagnostics

`scripts/production_smoke.py` reads the running web process through the protected
loopback `/internal/runtime` endpoint, rather than treating its own empty AI state
or the current checkout as the deployed state. Run with the same server environment
and `WEB_SESSION_SECRET`; the monitor token is derived locally and never printed.
`RUNTIME_STATUS_URL` may override the default port, but only for this loopback
endpoint. AI observations older than one hour become `unknown`; diagnostics show
degradation and the last successful call without making paid probe requests.
Restart the web service after deployment; a changed checkout alone is insufficient.

The GitHub Actions deployment accepts the public, independently verified root pin
from repository variable `GIGACHAT_ROOT_SHA256`, or preserves an existing server
trust configuration. `scripts/preflight_trust.py` validates trust before the service
is restarted, without an AI inference request. Failed validation aborts deployment
and restores the prior release/environment. Configure trust before pushing an
upgrade to `main`; a successfully tested commit alone does not confirm deployment.

## Deletions and safe database restore

The first startup creates a separate deletion receipt journal and binds the database
to its identity. `ERASURE_JOURNAL_PATH` can point to persistent storage outside the
application data directory; by default it is
`<DB_PATH parent>/.privacy/<DB filename>-erasures.sqlite3`. Receipts contain only
keyed identity hashes and deletion times. Confirmed deletion records a durable
receipt before removing data. Startup reapplies it before workers or login begin.
A later signup is distinguished by its creation time, including reused numeric IDs.

Keep this journal independently of database snapshots, retain it for at least as
long as any restorable backup, and replicate it to protected persistent storage
after deletions. Never roll it back to a snapshot's date. A missing, invalid or
mismatched journal blocks startup/restore for a database already bound to it.
This protects restores that retain the current journal; losing the journal prevents
a safe restore. At upgrade, the initially empty journal cannot recover deletions
made before this mechanism existed.

To restore:

1. Stop all web, bot and worker processes using the target database. Finish any
   SQLite checkpoint while offline; unresolved WAL/SHM/journal files block restore.
2. Load the server environment and run
   `python scripts/restore_state.py /path/to/snapshot --target /path/to/bot.db`
   (optional `--journal /path/to/current-erasures.sqlite3`).
3. The command verifies the backup manifest checksum and SQLite integrity, checks
   the journal identity, removes previously deleted accounts from a temporary
   database, then atomically replaces the target. Failures leave the target intact.
4. Keep the existing VAPID private key. The command restores only SQLite; if the key
   was lost, restore the matching key separately from a verified protected backup
   before starting notification workers.
5. Start services and run `python scripts/production_smoke.py --expected-sha <sha>`.

Always use this command, including for older backups without a journal binding.
Directly copying an old unbound database can bypass detection of that restore.
Never initialize a new empty journal to work around a restore failure.
