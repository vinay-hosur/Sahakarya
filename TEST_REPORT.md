# Test report

**Run date:** 2026-11-27  
**Scope:** Native collaboration, task dependency/history, notifications and mentions, file ingestion/search, workspace AI assistant with provider selection and persistent memory, and managed-host deployment preparation.

## Automated checks actually executed

| Command/check | Result |
| --- | --- |
| `cd backend; uv run pytest -q` | 59 passed (collection count: 59). |
| `cd backend; uv run ruff check app tests` | Passed. |
| `cd backend; uv run ruff format --check app tests` | Passed; 50 files already formatted. |
| `cd frontend; npm run lint` | Passed (oxlint). |
| `cd frontend; npm run build` | Passed (TypeScript project build and Vite production bundle). |
| Cloudflare Pages proxy assertions (Node) | Passed: upstream path/query, session cookie, browser origin, forwarded host/protocol, and rejection of an insecure API origin. |
| PostgreSQL migration at API startup | Passed against PostgreSQL 17; applied `a91c5f7e2b40`. |
| `docker compose exec api uv run alembic current` | `a91c5f7e2b40` (head). |
| Docker Compose health checks | PostgreSQL, API, and web containers healthy after final rebuild. |
| Docker HTTP smoke | Web root, `/health/ready`, and `/api/v1/meta` returned successfully; OpenAPI includes the task-proposal route; Alembic is at migration head. |

## Coverage added or retained

- Knowledge Hub tests cover create/edit, version snapshots, comments, audit events, workspace/project isolation, document-owner edit rules, and viewer read-only access.
- File ingestion tests upload Markdown through multipart HTTP, verify extracted content and metadata, search an indexed passage, download the original bytes, call the grounded-answer path with a mocked reasoning provider, and verify upload/search audit events.
- Search permission coverage confirms a viewer can search and a non-member cannot access another workspace's search endpoint.
- Chat tests cover channel/message persistence, viewer write restrictions, audit events, and the authenticated WebSocket event flow.
- GitHub repository count/list tests verify REST pagination handling and bounded, source-attributed results using mocked API responses; they do not use a live GitHub token.
- Decision tests cover CRUD, evidence references, immutable history, audit events, cross-workspace/member validation, and viewer read-only access.
- Activity tests cover workspace/project filtering, native event types, viewer read access, and denial for non-members.
- Member tests cover invitation hashing/acceptance/single use, email match, role changes, removal, owner protection, and non-owner denial.
- The full backend suite also covers auth, projects, tasks/comments, integrations, retrieval, and reviewed actions.
- Task tests verify normalized labels, same-project dependencies, cycle rejection, deletion protection while dependencies exist, immutable change history, and viewer-safe reads.
- Notification tests cover assignment and comment events, chat @email mentions, recipient-only visibility, mark-one/read-all behavior, and unread filtering.
- Configuration tests verify Render/Postgres URLs select psycopg 3 and the Slack callback can be derived from the public frontend URL.
- Slack integration tests verify OAuth URL/state creation, hash-only state storage, one-time callback validation, mocked code exchange, encrypted token persistence, MCP discovery, safe API output, token revocation on disconnect, and audit events. They do not contact a live Slack workspace.
- Assistant tests cover provider selection, grounded response persistence/citations, shared memory lifecycle, viewer restrictions, audit records, and the unconfigured-provider response. Provider adapter contract tests use mocked OpenAI/Anthropic/Gemini responses.
- Task-proposal tests cover strict provider schema and usage parsing, reject unknown citations, map valid citations to project evidence, and verify drafts do not create tasks until the user submits a task through the native API.

## Live local application and PostgreSQL checks

- `GET /health/ready` returned `{"status":"ready","dependencies":{"database":"available"}}`.
- Frontend root at `http://localhost:8080/` returned HTTP 200 from the running Docker stack.
- The applied PostgreSQL migration head is `a91c5f7e2b40`; task labels/dependencies/history and recipient-scoped notifications, as well as OAuth state, assistant usage, memory, and file/chunk tables, are present through Alembic.
- A temporary account used the running API to upload a Markdown file, read its extracted document metadata, retrieve a lexical search hit, and download the original bytes. The temporary workspace and account were removed after the smoke check.
- A PostgreSQL WebSocket smoke check also verified authenticated chat presence and realtime message delivery alongside stored message history.
- A separate temporary owner/member pair exercised invitations, task assignment, task comments, chat mentions, notification retrieval, and read-all against the running PostgreSQL-backed API. The temporary workspaces and accounts were removed after verification.
- The free deployment configuration is prepared for Render API + Neon PostgreSQL + Cloudflare Pages. The Cloudflare proxy route and Pages routing manifest were checked locally. The hosted services have not been provisioned; signed-in provider accounts are still required.
- A live model smoke request returned a provider usage-limit response. The API now reports that condition without exposing provider response contents or credentials; successful synthesis still depends on restored provider quota. Assistant and grounded-answer contract tests use mocked provider responses.

## Limits of these checks

Automated API tests use in-memory SQLite for most CRUD/permission paths, local MCP test servers for connector/retrieval/action tests, and mocked provider responses. PostgreSQL checks cover migrations, file persistence, chunk search, download, WebSocket delivery, invitations, task notifications, and chat mentions. No live GitHub operation or Slack OAuth account flow was performed; Slack needs a registered app and HTTPS callback. The live model request was blocked by provider quota. File uploads are limited to 5 MiB; extracted text is limited to 100,000 characters; scanned PDFs are rejected because OCR is not implemented. Document retrieval is lexical, not semantic/vector search. Workspace chat fan-out is in-process and requires shared pub/sub before multi-instance deployment. Public deployment still awaits the free hosting accounts and their configuration.
