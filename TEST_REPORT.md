# Test report

**Run date:** 2026-11-27  
**Revision state:** Local working tree; these changes have not yet been deployed.

## Checks run on this working tree

| Command/check | Result |
| --- | --- |
| `cd backend; uv run pytest -q` | Passed: 72 tests. |
| `cd backend; uv run ruff check app tests` | Passed. |
| `cd backend; uv run ruff format --check app tests` | Passed: 53 files formatted. |
| `cd frontend; npm run lint` | Passed (oxlint). |
| `cd frontend; npm run build` | Passed (TypeScript and Vite production build). |
| Alembic PostgreSQL offline SQL generation | Passed for the full chain; one merged head: `d8e37fbc6a21`. No database was contacted or modified by this check. |
| `git diff --check` | Passed. |

## Automated coverage

- Invitations: secure one-time tokens, email matching, acceptance, resend/rotation, status history, and admin role boundaries.
- Team chat: an owner invites a member; both accounts exchange messages; both see persisted history after switching accounts. Viewer write restrictions remain covered.
- Uploads: supported PDF/DOCX/Markdown/TXT extraction, metadata, download, lexical indexing, 50 MiB bound, and mocked R2 storage for large files.
- Telegram: mocked Bot API validation, webhook secret checking, group discovery/association, duplicate delivery, assistant evidence retrieval, and disconnect preservation.
- GitHub: mocked REST retrieval of repository metadata, README, file tree, and selected source/configuration files; count/list retrieval remains covered.
- Workspace overview data: workspace descriptions are created, read, and updated through authenticated APIs. The UI uses actual member and audit activity endpoints.
- Existing project, task, decision, notification, AI provider contract, citation, and reviewed-action tests remain in the suite.

## Not verified against live services

- No live Render deployment or production database migration was run for this local change set.
- No real R2 upload, Resend email, Telegram bot/webhook, GitHub repository token, or Slack OAuth operation was performed. The corresponding automated integration tests use mocks or local test servers.
- The 50 MiB limit is enforced in the application, but uploads above 5 MiB require the four R2 settings in Render. The 50 MiB path is tested with a mocked object-storage adapter, not a real bucket.
- Telegram captures new eligible text/caption messages after a group is enabled. It does not import group history; BotFather privacy and group permissions control which updates Telegram sends.
- Uploads use a single request and are not resumable. OCR and semantic/vector document search are not implemented. Chat real-time fan-out remains single-process; polling is the client fallback.
- Invitation email requires `RESEND_API_KEY` and `INVITATION_FROM_EMAIL`; otherwise the application provides a secure invitation link for manual sharing.

The earlier deployed baseline was verified separately in the previous report. It must not be taken as evidence that this uncommitted working tree is already live.
