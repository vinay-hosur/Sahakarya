# SAHAKARYA Implementation Plan

## Current assessment

The repository initially contained the three supplied reference files and no application source, package manifests, migrations, or Git metadata. The presentation has eight slides and establishes the product intent, 24-hour MVP, high-level stack, and business direction. The SRS is the primary technical baseline; it adds implementation detail and labels unresolved items as recommendations or decisions. The supplied video could not be played in this environment, so its interaction details remain unverified.

The intended product is SAHAKARYA, an MCP-backed collaborative layer that connects conversations, code, tasks, and documentation. Its core path is: connect tools, retrieve bounded context, reason over cited evidence, propose an action, obtain explicit approval, execute through an allowlisted connector, and record the confirmed result. It must remain source-aware and honest about unavailable integrations.

## Assessment and implementation choices

- **Architecture:** modular monolith, with a React/Vite/TypeScript client and a Python/FastAPI backend. Keep provider-specific MCP code inside connector adapters.
- **Persistence:** PostgreSQL through SQLAlchemy and Alembic. The full local stack uses Docker Compose. PostgreSQL 17 is healthy; the current migration head is `c3e5a9d174b2`.
- **Identity:** email/password with Argon2 hashes and HTTP-only opaque sessions is implemented as the local MVP default. Membership checks guard workspace routes.
- **MCP:** GitHub and Slack are the chosen MVP context sources. The official Python SDK v2 handles Streamable HTTP. GitHub token storage is encrypted; Slack uses registered-app OAuth with hashed one-time state, encrypted token storage, role checks, MCP capability discovery, and revocation on disconnect. Live external accounts still need owner setup.
- **AI:** selectable OpenAI Responses, Anthropic Messages, and Gemini adapters use strict structured output, source-ID validation, bounded excerpts, and prompt-injection handling. A live provider smoke request reached the configured provider but was rejected by its usage limit.
- **Visual direction:** the presentation's theme uses a warm parchment background, navy, orange, and cobalt accents, with Arial. The video is not available as a verified design reference.
- **First action:** the SRS recommends creating a GitHub issue from retrieved discussion. A typed GitHub `create_issue` proposal is implemented with source citations, exact-payload approval, a unique action reference, safe retry reconciliation, and audit events. The end-to-end action is verified against a local MCP server; live account authorization and repository permissions remain unverified.

The framework setup follows the current official Vite React TypeScript template and SQLAlchemy 2.x conventions. References: [Vite guide](https://vite.dev/guide/), [FastAPI](https://fastapi.tiangolo.com/), [SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/), and [Alembic](https://alembic.sqlalchemy.org/en/latest/).

## Roadmap

| Phase | Work | Exit condition |
| --- | --- | --- |
| 0. Repository and references | Inspect repository, PPT, SRS, video playback path, environment, and unresolved choices. | Assessment and decision log recorded. |
| 1. Foundation | Create the React/Vite client, FastAPI service, configuration, database model baseline, migration, health checks, request IDs, CORS, and local setup. | Complete: both services run; API and frontend build; PostgreSQL migrations are applied; readiness checks pass. |
| 2. Identity and workspaces | Implement email/password sessions, workspace create/list/read/rename, and membership checks. | Complete and covered by API tests; email verification and password reset remain future hardening. |
| 3. MCP connector | Implement the GitHub/Slack remote adapters, encrypted tokens, protocol probing, capability discovery, and status lifecycle. | Code and local protocol tests complete. A live GitHub read needs an account token; live Slack also needs registered-app credentials and an HTTPS callback. |
| 4. Context engine | Normalize, filter, deduplicate, rank, bound, persist, and cite retrieved source records. | Complete; tested with a local MCP server. Live SaaS reads still need the user's account token. |
| 5. AI reasoning | Use configurable structured LLM adapters, validate citations, handle uncertainty, and defend against prompt injection. | OpenAI, Anthropic, and Gemini adapters and offline tests are complete; a live request was rejected by the configured provider's usage limit. Embeddings remain unconfigured. |
| 6. Approved action | Create a typed proposal, bind explicit approval to the exact hash, use an allowlisted write, reconcile retries, and audit. | End-to-end local MCP test passes. Live issue creation needs a GitHub token with write permission for the chosen repository. |
| 7. Product flow and demo mode | Finish connected UI states, evidence cards, proposal review, activity, and clearly labelled seeded fallback. | Connected context and review flow complete; seeded fallback intentionally omitted to avoid representing fake results as connected data. |
| 8–9. Verify and demonstrate | Run security, API, connector, AI, and end-to-end checks; prepare setup, deployment, and recovery instructions. | Core offline verification complete; real account rehearsal and deployment await user provider setup and production choices. |

## First implementation tasks

1. Initialize project metadata, Git, and the frontend/backend structure. **Done.**
2. Add environment configuration, PostgreSQL Compose service, and versioned schema migrations. **Done and applied to PostgreSQL.**
3. Add `/health/live` and `/health/ready` plus request ID and CORS middleware. **Done.**
4. Build an account/workspace UI and verified connector setup without simulating AI or live integrations. **Done.**
5. Run backend API/security tests, lint and build the client, and verify PostgreSQL migrations. **Done.**

## Current blockers

The context-to-action workflow is implemented and verified with an official Python MCP SDK test server. A real SaaS walkthrough still needs a scoped GitHub token and target repository, a registered Slack app with client credentials and a reachable HTTPS callback, and provider quota for successful AI synthesis. Cloud deployment, email recovery, retention, rate limiting, and embeddings remain later decisions.
