# SAHAKARYA

SAHAKARYA is a native collaboration workspace for projects, tasks, knowledge documents, decisions, and shared activity. Workspace owners can invite members and manage member/viewer roles. MCP integrations extend the native workspace with external context and reviewed actions.

## Requirements

- Node.js 20.19+ or 22.12+ (Node 24 is supported by Vite's current guide)
- Python 3.12+
- `uv`
- Docker Desktop with the Docker Engine running for the PostgreSQL service

## Local setup

1. Copy `.env.example` to `.env` at the repository root if it does not exist. Keep existing local secrets private; do not replace them with the blank example values.
2. Start PostgreSQL:

   ```powershell
   # Start Docker Desktop first and wait for its Engine to become ready.
   docker compose up -d db
   ```

   The database image is PostgreSQL 17. The health endpoint reports database readiness separately from API liveness.

3. Install backend dependencies and apply the migration:

   ```powershell
   cd backend
   uv sync
   uv run alembic upgrade head
   ```

4. In a terminal from `backend`, run the API:

   ```powershell
   uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

5. In a second terminal from `frontend`, install and run the UI:

   ```powershell
   npm install
   npm run dev
   ```

Open `http://localhost:5173`. Use the plain `npm run dev` command in PowerShell; don't pass the host address as a positional argument. The dashboard reads `/health/live` and `/health/ready` from the API. Readiness reports the database dependency honestly; it does not simulate a successful database connection.

### Run the full stack in Docker

For a single-command containerized demo, copy `.env.example` to `.env`, keep the development cookie setting for local HTTP, then run:

```powershell
docker compose up --build -d
```

Open `http://localhost:8080`. The stack starts PostgreSQL, applies Alembic migrations before the API accepts traffic, and serves the frontend build through Nginx with API and WebSocket proxying. Check service health with `docker compose ps` and logs with `docker compose logs -f api`. Stop the services with `docker compose down`; this preserves the named database volume. The default database password and HTTP cookie settings are for local development only. For an internet-facing deployment, override them, terminate TLS, set `SESSION_COOKIE_SECURE=true`, and configure a trusted origin.

### Free managed deployment

The included [`render.yaml`](render.yaml) creates one free Docker API service in Singapore. Pair it with Neon Free PostgreSQL for persistent data and Cloudflare Pages for the React app plus same-origin REST/WebSocket proxy. This avoids Render's 30-day free database expiration while keeping app hosting within the free tiers. Expect the Render API to sleep after 15 minutes idle and wake slowly; Neon and Cloudflare have usage caps, and AI provider usage is separate. Current limits are documented by [Render](https://render.com/docs/free), [Neon](https://neon.com/blog/neon-free-plan-1-gb-per-project), and [Cloudflare](https://developers.cloudflare.com/workers/platform/limits/).

Follow [DEPLOYMENT.md](DEPLOYMENT.md): create the Neon database, deploy the Render Blueprint with its pooled `DATABASE_URL`, then deploy `frontend/` to Cloudflare Pages with `API_BASE_URL` pointing at Render. Add the Pages origin to Render's `CORS_ORIGINS` and `SLACK_FRONTEND_URL`. Keep the generated `INTEGRATION_ENCRYPTION_KEY` stable. AI and Slack credentials belong only in Render's secret settings; GitHub's scoped token is submitted through **Integrations**. Never put credentials in chat, source files, or `VITE_` variables.

## Accounts and workspaces

Passwords must be at least 12 characters and are stored as Argon2 hashes. The API issues opaque, HTTP-only session cookies and stores only a SHA-256 hash of each session token. Production cookies are `Secure` by default; set `SESSION_COOKIE_SECURE=false` only for local HTTP development. A same-site origin check protects cookie-authenticated writes.

Protected workspace routes require a valid session and membership. Workspace roles are owner, member, and viewer. Viewers can read native project data and the activity timeline; owners and members can create records and comments. Document edits are restricted to the document owner or workspace owner. Workspace owners manage invitations and member roles.

## Native demo walkthrough

The native project/task workflow runs without GitHub or another external service:

1. Open `http://localhost:5173` and register or sign in.
2. In your private workspace, create a project such as **Authentication reliability** and add a short objective.
3. In the project card, select **Project overview** to see record-based task counts, overdue/due-soon tasks, blockers, priorities, and recent decisions. Select **View tasks** to add a task such as **Verify token refresh after sign-in**, choose its priority, due date, labels, prerequisite tasks, and workspace assignee.
4. Change the task status from `todo` to `in progress`, change its assignee, review its change history, and remove a task with the explicit delete confirmation. A prerequisite cannot be deleted while another task depends on it. With a configured AI provider and project evidence, use **Evidence-backed task drafts** to generate cited suggestions, edit them, and explicitly create selected tasks.
5. Open **Comments**, post a project note, and confirm it appears with your name and timestamp.
6. Open **Knowledge** and create a project-linked note with a category and tags. Edit it, review **Versions**, and add a comment. You can also import a small PDF, DOCX, Markdown, or TXT file; its text is extracted and indexed for search, and the original remains downloadable.
7. Open **Decisions** and record a decision with reasoning, alternatives, and supporting document/task evidence.
8. Open **Messages** to create a workspace channel and chat. Mention teammates by selecting their email in the composer; task comments accept `@teammate@email` mentions. Assignment, task comments, and mentions appear in each recipient's notification bell. In **AI Assistant**, ask across native projects, tasks, documents, decisions, saved memory, and retrieved GitHub context. The shared conversation persists across visits. Workspace members can save or remove memories, choose a configured AI provider, and use the onboarding brief, open-work summary, and project update starters. **Knowledge** also supports lexical file/document search; choose **Ask AI from excerpts** only when you want matched passages sent to a configured provider.
9. Open **Activity** to review the project timeline. Open **Team** to invite a member as a viewer or member and manage workspace access.

Projects, tasks, documents, uploaded file bytes, search chunks, chat messages, decisions, comments, and membership changes are stored in PostgreSQL. Changes appear in the activity timeline where applicable. The native demo does not require an integration token. Invitations are created as one-time codes; share the code with the invited person through your own channel and have them sign in with the invited email to accept it.

## MCP integrations

GitHub connects to its fixed remote MCP endpoint using the MCP Python SDK over Streamable HTTP, probes the protocol, and lists tools. The UI shows **Verified** after this check succeeds; this verifies the handshake, not every future read/write operation. Slack uses a registered-app OAuth flow, stores only an encrypted user token, discovers MCP tools, and revokes the token when disconnected. Automated tests cover the flow; live Slack and GitHub account smoke tests still require owner-provided app/account configuration.

The connector UI accepts a GitHub personal access token and starts Slack's OAuth authorization flow when configured. Configure `INTEGRATION_ENCRYPTION_KEY` before connecting either service. Generate a Fernet key from the `backend` directory:

```powershell
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the generated value in the root `.env`, then restart the API. Credentials are encrypted before they are stored in PostgreSQL; they are never returned by the API or sent to the browser after submission. The endpoint is fixed per provider to prevent arbitrary outbound requests. For GitHub, use a fine-grained token limited to one repository and grant Issues read/write; see [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md).

To enable Slack, register an internal or Marketplace Slack app and configure `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET` in `.env`; set `SLACK_FRONTEND_URL` to the public app URL. If `SLACK_REDIRECT_URI` is unset, the API derives `https://<app-host>/api/v1/integrations/slack/oauth/callback` from that frontend URL. Register the exact callback URI with Slack. Set `SLACK_USER_SCOPES` to the user scopes your Slack app is allowed to request. After restart, workspace owners and members can connect Slack in **Integrations**. The OAuth start endpoint uses short-lived, single-use state; Slack tokens are encrypted at rest and revoked when disconnected. See [Slack OAuth setup](https://docs.slack.dev/authentication/installing-with-oauth/), the [Slack MCP server guide](https://docs.slack.dev/ai/slack-mcp-server/), and [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md). Live Slack cannot be verified until those app settings and a reachable HTTPS callback are supplied.

## Context and reviewed actions

The **AI Assistant** keeps a shared conversation for the workspace and answers from matching native projects, tasks, knowledge files, decisions, saved memory, and retrieved GitHub context. Starter prompts guide onboarding briefs, open-work summaries, and project updates. Replies show citations and the provider/model used. **Knowledge** remains available for direct lexical search through documents and uploaded files; “Ask AI from excerpts” explicitly sends matched passages to a configured provider. The **Integrations** workbench searches only discovered, allowlisted MCP read tools. GitHub repository count/list prompts use its authenticated REST endpoint; other provider queries use MCP search tools. Retrieved content is bounded, normalized, deduplicated, ranked, and saved with source URLs and request links.

Optional grounded synthesis supports OpenAI Responses, Anthropic Messages, and Google Gemini. Set a key and model pair for each provider you want to offer (`OPENAI_API_KEY` + `OPENAI_MODEL`, `ANTHROPIC_API_KEY` + `ANTHROPIC_MODEL`, and/or `GEMINI_API_KEY` + `GEMINI_MODEL`) in the root `.env`, then restart the API. `DEFAULT_REASONING_PROVIDER` chooses the initial provider; users can select among configured providers in the assistant and document answer panel. When a user sends an AI request, the selected provider receives the prompt and bounded matching workspace records. Source text is treated as untrusted input, and returned citations are checked against the supplied records. API keys remain server-side. Provider calls are covered by mocked contract tests; the live smoke request returned a provider usage-limit response, so successful live synthesis still requires an active provider quota. Without a configured provider, lexical search and manual issue drafting remain available.

Assistant messages and saved workspace memory are persisted in PostgreSQL. Memory entries are shared with workspace members and can be scoped to one project. Owners and members can add or remove memories. The assistant uses the recent shared conversation, matching native documents, project/task/decision records, saved memory, and previously retrieved MCP context. Each request remains grounded in bounded records and may cite them; it cannot execute an action from chat. GitHub issue creation remains a separate reviewed action flow.

When GitHub is verified, select evidence to cite, enter the repository and issue content, review the exact body and SHA-256 hash, and explicitly approve it before execution. The executor permits only the discovered GitHub `create_issue` tool, rechecks the approval hash, adds a unique action reference, searches for that reference before a write or retry, and records the confirmed result. An uncertain MCP write is not repeated unless reconciliation finds the original issue. This path has been tested against a local MCP test server; a real issue write still requires owner-provided credentials and repository permissions.

## Current product scope

The native ecosystem includes workspace-scoped projects, tasks with assignment, labels, dependencies, immutable history, comments, @email mentions, deletion, and cited AI task drafts with explicit human creation; assignment/comment/chat notifications; deterministic project overviews; versioned knowledge documents and file imports; lexical document search with optional explicitly requested grounded answers; project decisions with history and linked evidence; persistent workspace chat; a permission-filtered activity timeline; and owner-managed workspace invitations and member roles. Remaining engineering gaps include document deletion/version restore, decision comments, ownership transfer, safe workflows, multi-instance chat pub/sub, OCR, semantic/vector search, rate limiting, and automated backup/monitoring. Live AI/GitHub/Slack operations need working provider credentials and workspace permissions. See [IMPLEMENTATION_AUDIT.md](IMPLEMENTATION_AUDIT.md), [PRODUCT_ROADMAP.md](PRODUCT_ROADMAP.md), and [TASK_TRACKER.md](TASK_TRACKER.md).

## Checks

Backend:

```powershell
cd backend
uv run pytest
uv run ruff check app tests
```

Frontend:

```powershell
cd frontend
npm run build
npm run lint
```

API docs are available at `http://localhost:8000/docs` in the separate-dev-server setup and `http://localhost:8080/docs` in the full Docker stack. Health endpoints are `/health/live` and `/health/ready`.
The endpoint inventory is in [API_DOCUMENTATION.md](API_DOCUMENTATION.md). Latest executed checks are recorded in [TEST_REPORT.md](TEST_REPORT.md).

## Configuration and security

Backend settings are read from environment variables and an optional backend `.env` file. Never add API keys or credentials to the frontend's `VITE_` environment variables. The committed `.env.example` contains only local development defaults. The local PostgreSQL password in Compose is for disposable development use; replace it for any shared or deployed environment.

The optional AI adapters support OpenAI, Anthropic, and Gemini; no model keys are set in the checked-in example. MCP credentials are per workspace, and a connector is marked as connected only after a real protocol and tool-discovery check. See [DECISIONS.md](DECISIONS.md) and [TASK_TRACKER.md](TASK_TRACKER.md).
