# Test report

**Run date:** 2026-11-27  
**Published revision:** `a2d46c6` on `main`.

## Checks run on this revision

| Command/check | Result |
| --- | --- |
| `cd backend; uv run pytest -q` | Passed: 72 tests. |
| `cd backend; uv run ruff check app tests` | Passed. |
| `cd backend; uv run ruff format --check app tests` | Passed: 53 files formatted. |
| `cd frontend; npm run lint` | Passed (oxlint). |
| `cd frontend; npm run build` | Passed (TypeScript and Vite production build). |
| Alembic PostgreSQL offline SQL generation | Passed for the full chain; one merged head: `d8e37fbc6a21`. |
| `git diff --check` | Passed. |

## Deployment smoke checks

- Render API `https://sahakarya-api.onrender.com/health/ready`: HTTP 200; PostgreSQL reported available.
- Render OpenAPI: HTTP 200, 62 paths, including Telegram connect/status/groups/webhook and invitation resend.
- Render `/api/v1/meta`: HTTP 200; runtime reports Gemini `gemini-3.1-flash-lite` and xAI `grok-4.3` configured, with Gemini selected by default. This verifies configuration presence, not a successful live model completion.
- Cloudflare Pages `https://sahakarya.pages.dev/`: HTTP 200 and serves the new frontend bundle (`index-Vy3wkJ0X.js`).
- The Render container runs `alembic upgrade head` before Uvicorn. The new API reached ready and exposed the new routes after deployment.

## Automated coverage

- Invitations: secure one-time tokens, email matching, acceptance, resend/rotation, status history, and admin role boundaries.
- Team chat: an owner invites a member; both accounts exchange messages; both see persisted history after switching accounts. Viewer write restrictions remain covered.
- Uploads: supported PDF/DOCX/Markdown/TXT extraction, metadata, download, lexical indexing, 50 MiB bound, and mocked R2 storage for large files.
- Telegram: mocked Bot API validation, webhook secret checking, group discovery/association, duplicate delivery, assistant evidence retrieval, and disconnect preservation.
- GitHub: mocked REST retrieval of repository metadata, README, file tree, and selected source/configuration files; count/list retrieval remains covered.
- Workspace overview data: workspace descriptions are created, read, and updated through authenticated APIs. The UI uses real member and audit activity endpoints.
- Existing project, task, decision, notification, AI-provider contract, citation, and reviewed-action tests remain in the suite.

## Still needs service setup or live verification

- Runtime metadata reports `object_storage_configured: false`. Files above 5 MiB are rejected until the four Cloudflare R2 settings are added to Render. The 50 MiB path is tested with a mocked object-storage adapter, not a real bucket.
- Runtime metadata reports `invitation_email_configured: false`. Invitations work as secure copyable links; automatic email requires Resend settings.
- No real Telegram bot token/webhook, R2 upload, Resend email, GitHub repository token, or Slack OAuth operation was exercised. The automated integration paths use mocks or local test servers.
- Telegram captures new eligible text/caption messages after a group is enabled. It does not import group history; BotFather privacy and group permissions control which updates Telegram sends.
- Uploads use one request and are not resumable. OCR and semantic/vector document search are not implemented. Chat real-time fan-out remains single-process; polling is the client fallback.
