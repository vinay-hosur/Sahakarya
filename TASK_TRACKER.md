# SAHAKARYA Task Tracker

**Last updated:** 2026-11-27  
Statuses: `Done` = implemented and verified at stated scope; `In progress` = active work; `Not started` = no implementation yet; `Blocked on configuration` = code or user-owned service setup is still required.

## Phase 0 — Audit and stabilize

| ID | Task | Status | Evidence |
| --- | --- | --- | --- |
| P0-01 | Inspect repository, source, models, migrations, tests, configs, and docs | Done | See `IMPLEMENTATION_AUDIT.md`; no rebuild performed. |
| P0-02 | Diagnose API refusal and Docker Engine state | Done | Docker Desktop had not been started; its Engine pipe was unavailable. Started Docker Desktop 29.6.2. |
| P0-03 | Validate PostgreSQL migrations | Done | PostgreSQL 17 healthy; migrations applied through `a91c5f7e2b40` (task history and workspace notifications). |
| P0-04 | Start backend and frontend | Done | Full Docker stack runs PostgreSQL, API, and built UI; container health and proxied routes verified on 2026-11-27 after final rebuild. |
| P0-05 | Validate auth/workspace on PostgreSQL | Done | Temporary live register/session/list/create/read smoke passed; temporary rows removed. |
| P0-06 | Execute automated checks | Done | Backend 59 tests passed; Ruff, format check, frontend lint/build, and Pages proxy assertions passed. See TEST_REPORT.md. |
| P0-07 | Create implementation audit and maintain project docs | Done | `IMPLEMENTATION_AUDIT.md`, `API_DOCUMENTATION.md`, `INTEGRATION_GUIDE.md`, `TEST_REPORT.md`, `PRODUCT_ROADMAP.md` added; README/architecture updated. |

## Phase 1 — Native platform foundation

| ID | Task | Status | Priority / acceptance |
| --- | --- | --- | --- |
| P1-01 | Projects API and project management UI | Done | Workspace-scoped create/list/read/update; owner/member authorization; audit events; isolation/validation tests; live PostgreSQL smoke check; no external integration needed. |
| P1-02 | Workspace invitation and member management | Done | Owner-only invite, accept, list, role change, revoke, and remove routes/UI; token hashing, expiry, owner protection, isolation and authorization tests. |
| P1-03 | Owner/member/viewer role policy for native modules | Done | Native project/task/document/decision writes enforce member/owner access; viewers read; document edits limited to document owner or workspace owner; member administration is owner-only. |
| P1-04 | Project membership and permission boundaries | Not started | Project-scoped access layered on workspace authorization. |

## Phase 2 — Native collaboration applications

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| P2-01 | Native task model/API/UI | Done | Create/list/read/update/delete, priority, due date, workspace-member assignment, labels, acyclic same-project dependencies, immutable task history, comments, audit events, project UI, and role checks are implemented. |
| P2-02 | Knowledge documents | Done | Native create/list/read/edit, project association, category/tags, ownership, immutable version snapshots, comments, lexical filters, permissions, audit events, UI; PostgreSQL workflow verified. |
| P2-03 | Decision records | Done | Project CRUD, owner/date/reasoning/alternatives/status, document/task/URL references, immutable history, permissions, audit events, UI; PostgreSQL workflow verified. |
| P2-04 | Shared activity API/UI | Done | Project-filtered chronological activity covers project/task/comment/document/decision events; membership filtering, actor/summary/time/object, UI, tests, and PostgreSQL workflow verified. |
| P2-05 | Project overview/intelligence | Done | Project overview API and UI derive task status, overdue/due-soon dates, blockers, high priority tasks, and recent decisions from stored records; API tests include viewer access. |
| P2-06 | Collaboration features | Done | Task assignment/comment notifications, @email mentions in task comments and workspace chat, private notification feed, read-one/read-all endpoints, bell UI, teammate mention picker, and test coverage. |
| P2-07 | Workspace chat | Done | Persistent workspace channels/messages, role-aware writes, audit events, authenticated WebSocket updates, and HTTP polling fallback; automated and PostgreSQL smoke checks recorded. Multi-instance pub/sub remains open. |
| P2-08 | Knowledge file ingestion | Done | PDF/DOCX/MD/TXT upload, 5 MiB input cap, 100,000 character extraction cap, project/category/tags, file download, indexed 300-word chunks with overlap, permission checks, and audit events. OCR is unsupported. |
| P2-09 | Native document search | Done | Workspace/project-scoped lexical search with source excerpts; explicit optional grounded answer sends up to eight matched passages to configured reasoning; search audit stores aggregate metadata. No embeddings/semantic search yet. |

## Phase 3 — Unified context and semantic retrieval

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| P3-01 | Provider-neutral source/context interface | Not started | Native and external sources share normalized provenance and permission-aware retrieval contract. |
| P3-02 | Embedding provider interface and content versioning | Not started | Configurable provider/model; unchanged content is not re-embedded. |
| P3-03 | PostgreSQL pgvector migration/storage | Not started | Extension availability is checked; migration and indexes are documented. |
| P3-04 | Hybrid retrieval | Not started | Keyword + semantic candidates, permission filters, deduplication, ranking, source links, result/token budgets. |
| P3-05 | Retrieval quality and isolation tests | Not started | Workspace/project filters, duplicates, citations, and bounded context tested. |
| P3-06 | Native lexical document retrieval | Done | Implemented and tested through file ingestion, chunk search, project filtering, viewer read access, and grounded-answer citation validation. This does not complete semantic retrieval. |

## Phase 4 — AI project intelligence

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| P4-01 | Multi-provider reasoning adapters | Done | Configured OpenAI Responses, Anthropic Messages, or Gemini adapters can be selected; keys remain server-side. A separately injected adapter interface and usage accounting remain future work. |
| P4-02 | Grounded answers, conflicts, and uncertainty | In progress | Existing strict schema/citation guard is baseline; add fact/interpretation/recommendation/unknown distinctions and conflict handling. |
| P4-03 | Structured native task proposals | Done | Project-scoped grounded context, strict provider schema, evidence citation validation, bounded suggestions, and no model-side write; tested with mocked provider responses. |
| P4-04 | Usage/error tracking | Done | Persist input/output/total token usage when returned by the provider; configure connect/read timeouts; sanitize provider errors and surface quota failures. |
| P4-05 | Persistent grounded workspace assistant and memory | Done | Shared chat, project selection, provider selector, native/external citations, workspace/project memory, role checks, audit events, migrations, and mocked provider tests. |

## Phase 5 — Action execution

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| P5-01 | Native task proposal and approval | Done | User can edit cited AI task drafts and explicitly create selected tasks through the native task API; proposal generation never mutates project data. |
| P5-02 | Strengthen role/payload/permission checks | In progress | Existing hash approval is baseline; revalidate permissions and invalidate approval on edits. |
| P5-03 | Keep GitHub issue action and real smoke test | Blocked on configuration | Local test server path passes; requires owner-provided scoped GitHub credential and target repository for actual create/verify. |
| P5-04 | Execution history and complete audit view | Not started | Success/failure/unknown state, external result, actor, request, and proposal visible. |

## Phase 6 — MCP and integrations

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| P6-01 | Extract reusable MCP connector lifecycle/adapters | Not started | Config, connect/disconnect/reconnect, tool schema, timeout, health, and errors provider-independent. |
| P6-02 | Live GitHub retrieval and issue smoke tests | Blocked on configuration | Actual repository source URL and approved issue URL recorded; mocks remain separately labeled. |
| P6-03 | Implement registered-app Slack OAuth | Blocked on configuration | OAuth start/callback, hashed single-use state, role checks, encrypted token storage, MCP discovery, reconnect, revocation on disconnect, and mocked tests are implemented. A registered Slack app, allowed scopes, client credentials, and public HTTPS callback are required for live verification. |
| P6-04 | Combine native/GitHub/Slack context | Not started | One request searches all permitted sources; unauthorized channels are excluded. |
| P6-05 | Improve GitHub repository queries | Done | Repository count/list intents use authenticated GitHub REST pagination and return attributed evidence; automated tests mock API responses. Live account verification still requires the workspace's GitHub credential. |

## Phase 7 — Workflow automation

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| P7-01 | Workflow definitions/triggers/conditions/actions | Not started | Declarative safe workflow model; no arbitrary code execution. |
| P7-02 | Execution retries, permissions, approvals, history | Not started | Idempotent executions and audit evidence for each state. |

## Phase 8 — Demo and deployment readiness

| ID | Task | Status | Acceptance |
| --- | --- | --- | --- |
| P8-01 | Reproducible setup and native demo guide | In progress | README provides setup and a full native walkthrough. No seeded workspace is shipped; create demo records in the signed-in workspace. |
| P8-02 | Deployment and operational hardening | In progress | Free deployment path configured for one Render API, Neon Free PostgreSQL, and Cloudflare Pages with REST/WebSocket proxy; locally validated. Public provisioning still needs signed-in Neon, Render, and Cloudflare accounts; free-tier sleep/quota limits and backup/monitoring/rate-limit hardening remain. |
| P8-03 | Expand critical tests and real smoke-test report | Done | 59 backend tests, Ruff, frontend lint/build, Pages proxy assertions, migration head, Docker health, and local PostgreSQL HTTP workflows are recorded in TEST_REPORT.md. Live external-provider tests remain unperformed. |

