# SAHAKARYA product roadmap

## Current verified foundation

- React/Vite/TypeScript UI, FastAPI API, PostgreSQL, Alembic migrations, health checks, request IDs, and structured logging.
- Email/password identity, private workspaces, hashed HTTP-only sessions, and owner/member/viewer roles.
- Native workspace-scoped projects and tasks with comments, status, priority, due date, validated assignment, labels, acyclic dependencies, immutable change history, deletion, and audit events; project overviews summarize stored task and decision records.
- Knowledge Hub: create/list/read/edit, project association, category/tags, document owner, immutable version snapshots, comments, lexical filters, authorization, audit events, and UI.
- Knowledge imports: PDF/DOCX/Markdown/TXT extraction, original-file retention/download, project-aware lexical chunk indexing and search, and optional explicitly requested grounded answers. Scanned PDFs/OCR and semantic embeddings are not supported.
- Decision Intelligence: project decisions with owner/date/reasoning/alternatives/status, document/task/URL evidence, immutable history, authorization, audit events, and UI.
- Unified Activity Timeline: chronological workspace/project-filtered project, task, comment, document, and decision events with actor, timestamp, object, and summary.
- Workspace Member Administration: owner-managed invitations, membership roster, member/viewer role changes, revocation and removal; invitation tokens are hashed and expire after seven days.
- Workspace chat: persistent channels/messages, permission-checked writes, audit events, authenticated WebSocket updates, and HTTP polling fallback. The current fan-out is single-process.
- Workspace AI assistant: shared persistent conversation; selectable OpenAI, Anthropic, or Gemini model; project-scoped grounding across native projects, tasks, knowledge files, decisions, saved memories, and retrieved GitHub context; citations; and shared workspace/project memory controls.
- Refreshed app navigation with distinct hash-routed product areas, a native document search panel, and an assistant workbench with onboarding/open-work/project-update starters.
- Workspace-scoped GitHub connector with encrypted credentials and MCP capability discovery; no live provider action is claimed. Slack registered-app OAuth, encrypted token storage, MCP discovery, and revocation on disconnect are implemented; live use awaits Slack app configuration.
- GitHub repository count/list intents call the authenticated REST repository endpoint; integration credentials still need live-account verification in the user's workspace.
- Bounded, source-attributed lexical retrieval and optional structured synthesis through configured OpenAI, Anthropic, or Gemini adapters; no semantic search is available. The latest live model smoke request reached the provider but was rejected by its usage limit.
- Human-reviewed GitHub issue proposal and execution state machine tested against a local MCP test server.

## Active priorities: deepen collaboration and improve operations

1. Add document deletion/version restore and decision comments if product requirements call for them.
2. Review role policy across the existing connector, context, and reviewed-action routes; add ownership transfer only with an explicit product rule.
3. Add safe workflow definitions/executions; use shared pub/sub before scaling chat across API instances.
4. Improve reproducible demos and production operations without adding fake seed records.

The four requested native modules were implemented and verified before this expansion. File ingestion, workspace chat, and lexical native retrieval are now implemented on top of them. PostgreSQL migration and API verification and automated permission/CRUD/isolation checks are recorded in TEST_REPORT.md. Semantic/vector retrieval remains unimplemented.

## Next: unified native context and intelligence

1. Define a provider-neutral context contract over projects, tasks, documents, decisions, comments, and external sources.
2. Add an embedding provider interface and PostgreSQL pgvector storage only after checking local/deployment extension support.
3. Extend lexical document retrieval into hybrid retrieval with permission filtering before ranking, deduplication, source links, and context/token budgets.
4. Move provider adapters behind a separately injected interface and improve conflict/uncertainty handling.
5. Continue improving task proposals with uncertainty/conflict handling, deduplication, and quality feedback. The first typed, cited, editable, human-approved project task workflow is implemented.

Acceptance: answers about a project cite stored, permission-visible records and state uncertainty when evidence is missing.

## External integrations

1. Complete real GitHub MCP smoke test with owner-provided scoped credentials; retain issue approval and idempotency.
2. Configure a registered Slack app and run live OAuth/context-permission smoke checks.
3. Add further providers only through the shared adapter model and when needed by the product.

## Collaboration and workflows

- Workspace invitations, member management, owner/member/viewer policies.
- Comments, mentions, notifications, project members, and complete shared activity history.
- Safe workflow triggers/conditions/actions with permission checks, approval requirements, retries, and audit history.

## Demo and production readiness

- Reproducible setup, seed data clearly labeled as demo data, deployment guidance, backup/retention policy, rate limiting, error monitoring, and real integration smoke checks.
- Free deployment is prepared for Render API + Neon database + Cloudflare Pages; publishing and provisioning still need signed-in provider accounts. Live providers need owner-managed credentials.
- No feature is marked complete until automated checks and, where relevant, a real external smoke test are recorded.

