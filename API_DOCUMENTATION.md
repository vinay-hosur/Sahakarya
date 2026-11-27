# SAHAKARYA API

Base URL for local development: `http://localhost:8000`  
Interactive OpenAPI page: `http://localhost:8000/docs`  
OpenAPI JSON: `http://localhost:8000/openapi.json`

All `/api/v1` routes except `/meta` require a valid SAHAKARYA session cookie unless stated otherwise. Browser writes require an allowed `Origin`. UUIDs are used for workspace and request identifiers. Error responses use FastAPI's JSON `detail` field.

## System

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/health/live` | No | Process liveness. |
| GET | `/health/ready` | No | Readiness; returns 503 if PostgreSQL cannot be reached. |
| GET | `/api/v1/meta` | No | API version, environment, configured reasoning providers (`id`, `label`, `model`), active default, and whether optional R2/Resend settings are complete. Secrets are not returned. |

## Authentication

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/auth/register` | Register with email, display name, and password (minimum 12 characters); creates a private default workspace and session cookie. |
| POST | `/api/v1/auth/login` | Sign in and receive the HTTP-only session cookie. |
| GET | `/api/v1/auth/me` | Return the current user and their workspaces. |
| POST | `/api/v1/auth/logout` | Revoke the current session and clear the cookie. |

## Workspaces

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/workspaces` | List workspaces for the signed-in user, including name, description, and role. |
| POST | `/api/v1/workspaces` | Create a workspace. Body: `{ "name": "Product team", "description": "Optional team purpose" }`. |
| GET | `/api/v1/workspaces/{workspace_id}` | Read a workspace the user belongs to. |
| PATCH | `/api/v1/workspaces/{workspace_id}` | Update its name (owner only) and/or description (owner/admin). Body: `{ "description": "Platform reliability" }` or `{ "name": "New name" }`. |

### Projects

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/projects` | List native projects in a workspace. |
| POST | `/api/v1/workspaces/{workspace_id}/projects` | Create a native project. Body: `{ "name": "Authentication", "description": "Resolve the sign-in issue." }`. Status starts as `active`. |
| GET | `/api/v1/workspaces/{workspace_id}/projects/{project_id}` | Read a project scoped to that workspace. |
| PATCH | `/api/v1/workspaces/{workspace_id}/projects/{project_id}` | Update `name`, `description`, or `status` (`active`, `on_hold`, `completed`). Changes are recorded in audit events. |

Project reads require workspace membership. Owner/member can write; viewer can read only. Projects inherit workspace membership and do not have separate project membership.

### Tasks

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks` | List tasks attached to a project. |
| POST | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks` | Create a task. Body fields include `title` (required), `description`, `priority`, `due_date`, `assignee_id`, up to 10 `labels` (32 characters each), and up to 25 existing `depends_on_task_ids` from the same project. Dependencies cannot contain cycles. New tasks start as `todo`. |
| GET | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks/{task_id}` | Read one task within its workspace/project scope. |
| PATCH | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks/{task_id}` | Update task details, labels, and dependency IDs. Dependencies must remain in the same project and acyclic. Send at least one field; `due_date` and `assignee_id` may be null to clear them; send an empty dependency list to clear dependencies. |
| DELETE | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks/{task_id}` | Delete a task and its comments; returns 409 while other tasks depend on it. Records a `task.deleted` audit event and preserves the deletion in task history storage. |
| GET | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks/{task_id}/history` | Read immutable task creation and field-change records with actor names and timestamps. |
| GET | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks/{task_id}/comments` | List comments for a task with author display name and timestamp. |
| POST | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/tasks/{task_id}/comments` | Add a comment. Body: `{ "body": "Please verify the edge case." }` (1–10,000 characters after trimming); `@teammate@email` mentions notify matching workspace members. |

Owner/member can create and update tasks and add comments; viewer can read tasks and comments/history. Assignment is limited to users who are already workspace members. Assignment, comments, and mentions notify affected workspace members, except the acting user. Task and comment creation/changes create audit events. The UI supports assignment, labels, dependency selection, status changes, task history, notifications, and task deletion with confirmation.

### Project intelligence

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/overview` | Return factual task counts by status, up to six overdue/due-soon/blocked/high-priority task summaries, and five recently updated decisions. The overview is derived from stored records and requires workspace membership. |

### Knowledge hub

| Method | Path | Purpose |
| --- | --- | --- |
| GET | /api/v1/workspaces/{workspace_id}/documents?project_id=&category=&tag=&q= | List documents visible in a workspace; filters are optional and q matches title/content text. |
| POST | /api/v1/workspaces/{workspace_id}/documents | Create a document with title, content, optional category, tags, and project_id. The creator becomes its owner and version 1 is recorded. |
| POST | /api/v1/workspaces/{workspace_id}/documents/upload | Multipart upload: file (PDF, DOCX, MD, TXT; 50 MiB max), optional title/category/tags/project_id. Text is extracted and chunk-indexed. Files above 5 MiB require configured R2; otherwise the API returns 503. When R2 is configured, file bytes go to object storage and PostgreSQL stores metadata/object key. |
| GET | /api/v1/workspaces/{workspace_id}/documents/search?q=&project_id=&limit= | Search native document passages using workspace-scoped lexical ranking; limit is 1–20. |
| POST | /api/v1/workspaces/{workspace_id}/documents/search/answer | Body: `{ "query": "...", "project_id": null, "limit": 10 }`. Returns matching passages and, when configured, an optional answer with an evidence assessment (facts, interpretations, recommendations, unknowns, and conflicts). This explicitly sends up to 8 matched passages to the reasoning provider. |
| GET | /api/v1/workspaces/{workspace_id}/documents/{document_id} | Read a document in its workspace. |
| PATCH | /api/v1/workspaces/{workspace_id}/documents/{document_id} | Edit title/content/category/tags/project association. Each changed edit creates a version snapshot. Only the document owner or workspace owner may edit. |
| GET | /api/v1/workspaces/{workspace_id}/documents/{document_id}/file | Download the original file for an uploaded document. |
| GET | /api/v1/workspaces/{workspace_id}/documents/{document_id}/versions | List immutable document snapshots, newest first. |
| GET / POST | /api/v1/workspaces/{workspace_id}/documents/{document_id}/comments | Read or add a document comment. |

All document routes enforce workspace membership; project associations must point to a project in that workspace. Members can create documents and comment; viewers can read and perform lexical search. Document writes, comments, uploads, and grounded-search requests create audit events; search audit metadata stores query length and result count, not the query. Extracted content is limited to 100,000 characters. Scanned PDFs requiring OCR are rejected. Search uses indexed 300-word chunks with 60-word overlap, term/phrase/title scoring, and document-level deduplication; this is lexical retrieval, not semantic/vector search. PostgreSQL stores extracted text and file metadata; original bytes are stored in R2 when configured, or in PostgreSQL for legacy uploads up to 5 MiB. Upload uses a single request and is not resumable.

### Decision intelligence

| Method | Path | Purpose |
| --- | --- | --- |
| GET / POST | /api/v1/workspaces/{workspace_id}/projects/{project_id}/decisions | List or create project decisions. Fields include title, description, owner, date, reasoning, alternatives, status (proposed, approved, rejected, superseded), and references. |
| GET / PATCH | /api/v1/workspaces/{workspace_id}/projects/{project_id}/decisions/{decision_id} | Read or update a decision; edits create a history snapshot and audit event. |
| GET | /api/v1/workspaces/{workspace_id}/projects/{project_id}/decisions/{decision_id}/history | List decision snapshots, newest first. |

Each reference must target exactly one workspace-scoped document, project task, or HTTP(S) URL. Decision owners must be workspace members. Viewers can read; owners and members can create/update. Decisions and references are included in project activity.

### Unified activity

| Method | Path | Purpose |
| --- | --- | --- |
| GET | /api/v1/workspaces/{workspace_id}/activity?project_id=&limit=30&offset=0 | List chronological audit-backed activity for a workspace or one project. limit is 1–100. |

The response includes actor, timestamp, event type, related object, and a safe summary. Project filtering is validated against the workspace. All workspace members may read; the feed is scoped to the caller's workspace membership.

### Workspace members and invitations

| Method | Path | Purpose |
| --- | --- | --- |
| GET | /api/v1/workspaces/{workspace_id}/members | List workspace members; any member can read the roster. |
| PATCH | /api/v1/workspaces/{workspace_id}/members/{user_id} | Owner/admin changes a member role to admin, member, or viewer; only the owner can grant/change an admin role. |
| DELETE | /api/v1/workspaces/{workspace_id}/members/{user_id} | Owner/admin removes a member. Only the owner can remove an admin; workspace owner cannot be removed. |
| POST / GET | /api/v1/workspaces/{workspace_id}/invitations | Owner/admin creates or lists invitations with pending/accepted/expired/revoked status. Create body: { "email": "person@example.com", "role": "viewer" }. The response includes a one-time token/link and email delivery status. |
| POST | /api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/resend | Rotate the invitation token, extend expiry by seven days, and optionally email the new link. |
| DELETE | /api/v1/workspaces/{workspace_id}/invitations/{invitation_id} | Owner/admin revokes a pending invitation. Only the owner can manage invitations for the admin role. |
| POST | /api/v1/workspaces/invitations/accept | Signed-in invited user accepts with { "token": "<one-time invitation token>" }; the account email must match. |

Invitation tokens expire after seven days and only their SHA-256 hashes are stored. Email is sent when `RESEND_API_KEY` and `INVITATION_FROM_EMAIL` are configured; otherwise the owner shares the returned secure link. Admins can manage member/viewer access. Only owners can invite, change, revoke, or remove admins. The owner role cannot be assigned through invitation or changed/removed here.

## Integrations

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/integrations` | List provider status and discovered capabilities. Secret values are never returned. |
| POST | `/api/v1/workspaces/{workspace_id}/integrations/{provider}/connect` | Connect or recheck GitHub with `{ "access_token": "..." }`; credentials are encrypted server-side. Raw Slack-token requests return 409; Slack uses OAuth. |
| POST | `/api/v1/workspaces/{workspace_id}/integrations/slack/oauth/start` | Owner/member only. Creates short-lived single-use state and returns Slack's authorization URL. Requires Slack client settings, an HTTPS callback, and `INTEGRATION_ENCRYPTION_KEY`. |
| GET | `/api/v1/integrations/slack/oauth/callback` | Slack OAuth callback. Validates/consumes the state, exchanges the code, encrypts the issued token, verifies MCP tool discovery, and redirects to `SLACK_FRONTEND_URL`. |
| DELETE | `/api/v1/workspaces/{workspace_id}/integrations/{provider}` | Owner/member only. Removes a local integration; Slack is revoked with `auth.revoke` first. A revocation failure leaves the integration configured. GitHub tokens must be revoked in GitHub separately. |
| GET | `/api/v1/workspaces/{workspace_id}/integrations/telegram/status` | Return Telegram connection status and bot identity without returning the token. |
| POST | `/api/v1/workspaces/{workspace_id}/integrations/telegram/connect` | Validate a bot token with Telegram `getMe`, encrypt it, and register a secret-verified webhook. Requires `INTEGRATION_ENCRYPTION_KEY` and HTTPS `PUBLIC_API_URL`. |
| GET | `/api/v1/workspaces/{workspace_id}/integrations/telegram/chats` | List discovered groups, their association, and synchronization time. |
| PATCH | `/api/v1/workspaces/{workspace_id}/integrations/telegram/chats/{chat_id}` | Enable/pause a discovered group and associate it with a same-workspace project. |
| DELETE | `/api/v1/workspaces/{workspace_id}/integrations/telegram` | Remove the webhook and credential while retaining previously captured group context. |
| POST | `/api/v1/integrations/telegram/webhook/{integration_id}` | Public Telegram Bot API endpoint; verifies `X-Telegram-Bot-Api-Secret-Token`, then stores supported new text/caption messages only for enabled groups. |

Telegram bots cannot fetch all historical group messages. Group privacy settings and group membership determine which new messages Telegram delivers. Telegram messages are workspace-scoped and enter assistant retrieval only after a group is enabled. Configure the bot token in the UI; do not place it in Render environment logs or frontend variables.

## Workspace chat and notifications

| Method | Path | Purpose |
| --- | --- | --- |
| GET / POST | `/api/v1/workspaces/{workspace_id}/chat/channels` | List or create workspace channels. |
| GET / POST | `/api/v1/workspaces/{workspace_id}/chat/channels/{channel_id}/messages` | Read recent channel messages or post a message. `@teammate@email` mentions notify the matching workspace member. |
| WS | `/api/v1/workspaces/{workspace_id}/chat/ws?channel_id={channel_id}` | Authenticated real-time channel updates; the UI falls back to HTTP polling if a WebSocket is unavailable. |
| GET | `/api/v1/workspaces/{workspace_id}/notifications?unread_only=&limit=` | List only the signed-in member's notifications; limit is 1–100. |
| PATCH | `/api/v1/workspaces/{workspace_id}/notifications/{notification_id}/read` | Mark one of the caller's notifications read. |
| POST | `/api/v1/workspaces/{workspace_id}/notifications/read-all` | Mark the caller's unread workspace notifications read. |

Chat and notification routes require workspace membership. Members can post; viewers can read. Notifications are private to their recipient. Chat fan-out currently uses one API process; multiple instances need shared pub/sub before scaling.

## Context requests

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/requests?limit=10` | List recent request summaries; limit is 1–50. |
| POST | `/api/v1/workspaces/{workspace_id}/requests` | Search connected, eligible MCP read tools. Body: `{ "prompt": "What did we decide about onboarding?" }` (3–2000 characters). Returns request state, bounded evidence, source metadata, and optional validated reasoning. |
| GET | `/api/v1/workspaces/{workspace_id}/requests/{request_id}` | Read a saved request and its evidence. |

GitHub prompts that explicitly ask to count or list repositories use GitHub's authenticated `GET /user/repos` endpoint directly, with the connected token and REST pagination. Count queries inspect the last page using one item per page; list queries return up to the 20 most recently updated repositories. Questions about a named repository use GitHub's REST API to retrieve repository metadata, README, file tree, and up to three relevant source/configuration files. Issues and pull requests continue through discovered, allowlisted MCP search tools. The fine-grained token needs repository metadata and contents read access, plus the required issue/PR read permissions for those searches. Repository 404 responses are described as not found or inaccessible because GitHub does not distinguish those states for private repositories.

## Workspace AI assistant

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/assistant/messages?limit=100` | Read the shared workspace conversation (1–200 most recent messages), including provider/model and available token-usage counts. |
| POST | `/api/v1/workspaces/{workspace_id}/assistant/chat` | Ask a grounded question. Body: `{ "prompt": "Prepare an onboarding brief", "project_id": null, "provider": "openai" }`. `project_id` and `provider` are optional; provider may be `openai`, `anthropic`, `gemini`, `groq`, or `xai` when configured. Returns a persisted assistant reply with citation metadata and an evidence assessment that separates sourced facts, interpretations, recommendations, unknowns, and conflicts. |
| POST | `/api/v1/workspaces/{workspace_id}/assistant/projects/{project_id}/task-proposals` | Draft up to five evidence-cited project tasks. Body: `{ "prompt": "Turn the migration plan into tasks", "provider": "gemini" }`; provider is optional. Suggestions are not persisted as tasks; users edit and explicitly create selected tasks through the native task endpoint. Returns 503 when no configured provider is available and 502 for invalid provider output. |
| GET | `/api/v1/workspaces/{workspace_id}/assistant/memories?project_id=` | List shared workspace memories, optionally including workspace-wide memories and those for one project. |
| POST | `/api/v1/workspaces/{workspace_id}/assistant/memories` | Save shared memory. Body: `{ "content": "Deployments require a review", "project_id": null }`. Content is 3–1,000 characters; each workspace supports up to 100 entries. |
| DELETE | `/api/v1/workspaces/{workspace_id}/assistant/memories/{memory_id}` | Remove a saved memory. |

All routes require workspace membership. Viewers may read messages/memories and ask questions; only owners/members can add or remove memories or create the suggested tasks. Assistant turns and memory changes are persisted and audited; task-proposal audit records contain aggregate counts/provider/usage, not prompt text. When providers return usage data, input/output/total token counts are stored with the assistant message and audit event. Chat and task proposals send bounded matching native/external records to the selected server-configured provider; connect/read timeouts are configurable with `REASONING_CONNECT_TIMEOUT_SECONDS` and `REASONING_READ_TIMEOUT_SECONDS`. They return 503 if that provider is not configured and 502 if the provider request fails. The assistant does not write to an integration.

## GitHub issue proposals

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/workspaces/{workspace_id}/requests/{request_id}/proposals` | Create an issue draft from selected source IDs, a repository, title, and description. Does not write to GitHub. |
| GET | `/api/v1/workspaces/{workspace_id}/requests/{request_id}/proposals` | List proposals for a request. |
| GET | `/api/v1/workspaces/{workspace_id}/proposals/{proposal_id}` | Read a proposal and execution status. |
| PATCH | `/api/v1/workspaces/{workspace_id}/proposals/{proposal_id}` | Edit a draft; recalculates its hash. |
| POST | `/api/v1/workspaces/{workspace_id}/proposals/{proposal_id}/cancel` | Cancel a draft that has not been approved. |
| POST | `/api/v1/workspaces/{workspace_id}/proposals/{proposal_id}/approve` | Approve the exact current payload. Body: `{ "payload_hash": "<sha256 from proposal>" }`. |
| POST | `/api/v1/workspaces/{workspace_id}/proposals/{proposal_id}/execute` | Execute only the approved payload and return confirmed or uncertain status. Body has the same payload hash. |

The action routes currently support GitHub issue creation only. The executor restricts execution to the discovered `create_issue` capability and records audit/execution data. The result has been tested with a local MCP test server, not a live GitHub account.

