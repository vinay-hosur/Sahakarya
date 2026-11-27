# Integration setup

Integrations are workspace-scoped. The API uses fixed provider endpoints, keeps credentials server-side, encrypts them with Fernet before storing them, and reports **Verified** only after MCP initialization and tool discovery succeed.

## Required local secret key

Set `INTEGRATION_ENCRYPTION_KEY` in the root `.env`. Generate a key from `backend`:

```powershell
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Restart the API after changing the key. Existing credentials encrypted with a previous key cannot be decrypted, so do not rotate it without reconnecting those integrations. Never put provider secrets in frontend `VITE_` variables or commit `.env`.

## GitHub

The current adapter connects to `https://api.githubcopilot.com/mcp/` with a GitHub bearer token. Create a fine-grained personal access token limited to the demo repository. Grant **Issues: Read and write** for approved issue creation. SAHAKARYA can read README and file-tree context from public repositories without repository contents permissions. For private repository metadata and files, grant **Metadata: Read-only** and **Contents: Read-only**. Organization-owned repositories may require organization approval.

In SAHAKARYA, sign in, choose the target workspace, open **Connect your team’s tools**, paste the GitHub token, and submit. Confirm the UI reports **Verified** and displays discovered tools. If it reports an error, the saved token is encrypted but the connection is not live; recheck repository selection, expiration, organization approval, and permissions.

For a demo, use a token scoped to one repository. A token configured for all repositories exposes more than this feature needs.

## Slack OAuth

Register an internal Slack app for your organization or distribute a Marketplace app. Add the OAuth callback `https://<your-host>/api/v1/integrations/slack/oauth/callback` to the app's redirect URLs. It must be HTTPS and must route to the SAHAKARYA API through your reverse proxy. Configure these values in the root `.env` and restart the API:

```dotenv
SLACK_CLIENT_ID=...
SLACK_CLIENT_SECRET=...
SLACK_REDIRECT_URI=https://<your-host>/api/v1/integrations/slack/oauth/callback
SLACK_FRONTEND_URL=https://<your-host>
SLACK_USER_SCOPES=search:read.public,search:read.private,search:read.mpim,search:read.im,channels:history,groups:history,mpim:history,im:history
```

The scopes must also be allowed by your app configuration. Confirm the callback URL matches exactly. `INTEGRATION_ENCRYPTION_KEY` is also required. Workspace owners and members can then select **Connect Slack**; viewers cannot initiate the connection. SAHAKARYA stores only a hash of the short-lived OAuth state, consumes it once, exchanges the code server-side, encrypts the user token, and verifies the Slack MCP handshake/tool list before showing **Verified**. Disconnect calls Slack's token revocation endpoint before removing the local record; if Slack cannot confirm revocation, SAHAKARYA keeps the connection so it can be retried. Tests use a mocked Slack token exchange and MCP discovery; a live Slack connection has not been performed.

## Optional multi-provider reasoning

Grounded synthesis is optional. Set an API key and model for OpenAI, Anthropic, Gemini, and/or Groq in the backend environment, then restart the API. The keys stay on the server. Without a configured provider, the application still supports native lexical search and source evidence. Groq is integrated through its Responses API; see its [rate limits](https://console.groq.com/docs/rate-limits) and [supported structured output models](https://console.groq.com/docs/structured-outputs) before selecting a model. Exact quotas vary by account and model. Successful synthesis requires an active provider quota.

## Verification boundary

The automated suite uses local MCP servers and mocked Slack/provider responses. The containerized local app is available at `http://localhost:8080`; its health endpoints are `/health/live` and `/health/ready`. No live GitHub issue creation, Slack connection, or successful model synthesis is reported. For external smoke tests, connect a user-owned account and verify the returned source URL or external issue URL in that account.

