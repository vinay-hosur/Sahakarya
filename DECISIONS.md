# SAHAKARYA Decision Log

## Baseline facts from the supplied references

- Product: SAHAKARYA, Team LIMITLESS, tagline “Connecting Context. Enabling Action.”
- The product links conversations, code, tasks, and documentation through a context layer; it is not a generic chatbot or a replacement for those tools.
- The presentation establishes React, Vite, Python, FastAPI, PostgreSQL, LLMs, embeddings, vector search, and MCP as the high-level stack.
- The SRS is the primary implementation specification. It adds detailed engineering recommendations; recommendations and open choices are not treated as finalized product decisions.
- Any external write needs explicit human approval, deterministic policy validation, idempotency/reconciliation, and an audit record.

## Adopted implementation defaults

| Decision | Default | Reason / status |
| --- | --- | --- |
| Application shape | Modular monolith | Explicit SRS default suited to the MVP. |
| Frontend | React + Vite + TypeScript | Presentation stack; use the current official Vite React TypeScript template. |
| Backend | Python + FastAPI + Pydantic | Presentation/SRS stack. |
| ORM and migrations | SQLAlchemy 2.x + Alembic | SRS persistence requirement. |
| Local database | PostgreSQL 17 via Docker Compose | Persistent local development path; container is running and the initial migration is applied. |
| API prefix | `/api/v1` | SRS contract. Liveness/readiness remain under `/health`. |
| Frontend health state | Fetch actual backend health endpoints | Shows a verifiable backend/database state, without simulating product modules. |
| UI palette | Warm parchment with navy, orange, and cobalt accents; Arial-compatible sans | Derived from the presentation theme; video style remains unverified. |
| User authentication | Email/password, Argon2 password hashes, opaque server-side sessions, HTTP-only cookies | Local MVP default; session tokens are hashed in PostgreSQL. Email verification, password reset, and persistent rate limiting remain future hardening work. |
| Workspace access | Workspace membership on every workspace route; owners can rename | New accounts start with a private workspace. Foreign workspace reads return 404. |
| MCP implementation | Official MCP Python SDK v2 over Streamable HTTP | Handles the 2026 stateless protocol and falls back to older initialize-based servers. Only fixed official GitHub and Slack endpoints are enabled. |
| Integration secrets | User supplies provider access token; Fernet ciphertext stored server-side | Local `.env` contains a generated development key and is ignored by Git. Never expose this key or provider tokens in the frontend. |
| First write action | GitHub issue creation through the official MCP `create_issue` tool | The backend verifies the discovered tool schema, cites selected retrieved sources, binds approval to a canonical payload hash, and uses a unique action reference for duplicate checks. Local MCP execution is tested; live repository authorization remains unverified. |
| Reasoning provider | Optional OpenAI Responses API adapter | Uses strict JSON Schema output and validates citation IDs. It is inactive unless both `OPENAI_API_KEY` and `OPENAI_MODEL` are set on the API server. The application sends bounded retrieved excerpts only when configured. |

## Unresolved product/provider decisions

| Decision | Current position | What is needed |
| --- | --- | --- |
| Second context source | Slack is the adopted MVP default because the brief centers conversations and Slack documents an official remote MCP server. The connector UI is implemented, but the live account is not connected. | Provide a Slack OAuth access token in the workspace Integrations panel; complete any needed admin approval, handshake, and capability discovery. |
| LLM model | No model is selected or configured in the local `.env`. | Set a model supported by OpenAI Structured Outputs and provide an API key through the server environment for live synthesis. |
| Authentication hardening | Local MVP email/password flow is implemented. No email verification, password reset, or distributed rate limiter yet. | Add before an internet-facing deployment. |
| MCP account authorization | Local Streamable HTTP handshake, context read, approval, issue write, and idempotency are verified against a local test server; SaaS accounts have not been authorized. | Add the user's GitHub PAT and, if desired, Slack OAuth access token through the UI; do not commit them. |
| Deployment host/database | Not selected. | Team/deployment account decision before deployment. |
| Retention policy | Not selected. | Team policy before production use. |
| pgvector | Schema boundary supports embeddings, but enabling the extension and retrieval depends on the chosen database image/provider. | Verify extension availability and embedding dimensions when selecting the model. |

## Reference alignment

No direct PPT/SRS product conflict was found. The PPT defines the product and broad MVP; the SRS adds testable requirements and explicitly marks provider choices as unresolved. If a second live context source cannot be verified, follow the SRS fallback: use clearly labelled seeded context and do not call it a connected integration.

## MCP availability research

Availability was checked against primary vendor documentation on 26 November 2026. GitHub maintains an official remote MCP server at `https://api.githubcopilot.com/mcp/`; its docs describe OAuth and PAT authentication and repository/issue capabilities. Slack documents an official remote MCP endpoint at `https://mcp.slack.com/mcp`, OAuth access, and search/read/action capabilities. The application completed a modern MCP discovery and tool-list exchange against a local test server using the official Python SDK; no GitHub or Slack user account has yet been connected. See the [GitHub MCP server](https://github.com/github/github-mcp-server), [GitHub setup docs](https://docs.github.com/en/copilot/how-tos/copilot-in-your-ide/customize-copilot/extend-copilot-with-tools-and-context/set-up-the-github-mcp-server), [Slack MCP docs](https://docs.slack.dev/ai/slack-mcp-server), and the [MCP Streamable HTTP specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http).

Atlassian's official Rovo MCP server is another possible source for Jira, Confluence, and Compass; its documented authentication includes OAuth 2.1 and, where enabled, API tokens. It remains an alternative because no Atlassian workspace has been selected. See [Atlassian MCP documentation](https://support.atlassian.com/security-and-access-policies/docs/understand-atlassian-mcp-server/).

## Inspection limitation

The video file exists, but local `file:` playback was rejected by the in-app browser policy and the available native playback controls did not expose a working player. Its contents and visual design have not been verified. No video-specific behavior is inferred in the implementation.
