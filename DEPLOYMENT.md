# Free deployment guide

This setup uses one free Render API service, Neon Free PostgreSQL, and Cloudflare Pages for the React app and same-origin API/WebSocket proxy. The hosting bill can stay at $0 within current free-tier quotas. AI provider usage and any optional paid API features are separate; enter provider keys only in Render's secret environment settings.

## Free-tier limits

- Render Free web services sleep after 15 minutes without traffic and usually take about a minute to wake. One service consumes the 750 monthly free instance hours for a workspace, so this guide deploys one API service. The free service has limited CPU and memory and is intended for demos and low traffic, not an always-on production SLA. See [Render's free instance limits](https://render.com/docs/free).
- Neon Free currently provides 100 compute-hours and 1 GB storage per project. Keep the database and API in the same region to reduce database latency. Monitor usage and export important data yourself; this plan is not a managed production backup guarantee. See [Neon's free plan details](https://neon.com/blog/neon-free-plan-1-gb-per-project).
- Cloudflare Workers/Pages Functions have a 100,000 request/day Free limit and a 10 ms CPU limit per request. The proxy mostly waits on network I/O. See [Cloudflare Workers limits](https://developers.cloudflare.com/workers/platform/limits/). Cloudflare supports WebSocket proxying through `fetch`; see [WebSocket connections](https://developers.cloudflare.com/workers/runtime-apis/websockets/).
- Model providers, Slack, and GitHub set their own account, quota, and billing terms. Hosting for this app does not include AI inference credits.

## 1. Create a persistent free database

1. Use the existing Neon Free project in Ohio shown in your signed-in account. The Render API is set to Ohio so it can stay near that database.
2. Select the production branch/database and copy its **pooled** connection string. Keep it private. It may begin with `postgresql://`; the API normalizes it to the installed psycopg 3 SQLAlchemy driver.
3. Do not use Render's free PostgreSQL for this deployment: that database expires after 30 days.

## 2. Deploy the API on Render Free

1. Push this repository to GitHub and connect it to Render.
2. In Render, choose **New → Blueprint**, select this repository, and review `render.yaml`. It creates one Docker web service named `sahakarya-api` in Ohio on the Free plan.
3. In the service's Environment settings, set `DATABASE_URL` to the Neon pooled connection string. Keep Render's generated `INTEGRATION_ENCRYPTION_KEY`; losing or rotating it makes saved connector credentials unreadable. `PUBLIC_API_URL` is set to `https://sahakarya-api.onrender.com` in the Blueprint and must match the service's actual public origin for Telegram webhooks.
4. Deploy and wait for `/health/ready` to pass. The container runs Alembic migrations before starting FastAPI. Copy the public API service URL, such as `https://sahakarya-api.onrender.com`.

The API's first request after a sleep may be slow. The API has no local persistent disk dependency. Workspace records, extracted text, and search chunks are in PostgreSQL. With no R2 configuration, original uploads above 5 MiB are rejected and smaller uploads use legacy PostgreSQL blob storage.

## 3. Deploy the UI and same-origin proxy on Cloudflare Pages

1. Create a Cloudflare Pages project connected to the same GitHub repository.
2. Set **Root directory** to `frontend`, **Build command** to `npm ci && npm run build`, and **Build output directory** to `dist`.
3. Add the Pages environment variable `API_BASE_URL` with the Render API's HTTPS origin, without an API path (for example, `https://sahakarya-api.onrender.com`). Set it for Production and Preview only if preview environments should reach the same API.
4. Deploy. `frontend/functions/` supplies `/api/*` and `/health/*` proxies, and `frontend/public/_routes.json` limits the function routes to those paths. The browser uses the Pages hostname for sign-in cookies, REST calls, file downloads, and WebSocket chat.
5. Copy the assigned Pages hostname, such as `https://sahakarya.pages.dev`.

## 4. Set the public origin and check the app

1. In Render's API Environment settings, set `CORS_ORIGINS`, `PUBLIC_APP_URL`, and `SLACK_FRONTEND_URL` to the exact Pages origin, with no trailing slash (for example, `https://sahakarya.pages.dev`).
2. `PUBLIC_API_URL` must be the API's public HTTPS origin (for example, `https://sahakarya-api.onrender.com`). The Slack callback will be `https://sahakarya.pages.dev/api/v1/integrations/slack/oauth/callback`, and Cloudflare Pages forwards it to the API.
3. Redeploy/restart the API after changing its environment.
4. Visit the Pages URL. Register the workspace owner, then check sign-in, create a project and task, upload/search a small file, post a chat message, and confirm the notification feed works.

## 5. Add optional integrations and AI

- Add AI provider key/model pairs as Render API environment secrets (`OPENAI_API_KEY`/`OPENAI_MODEL`, `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL`, or `GEMINI_API_KEY`/`GEMINI_MODEL`). Choose a provider whose account has an available quota. Never put keys in GitHub, chat, browser storage, or `VITE_` variables.
- GitHub: create a narrowly scoped fine-grained token for the intended repository, enable only required repository and Issues permissions, then connect it in **Integrations**. Confirm reads before approving a real issue write.
- Slack: create a Slack app, set `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, and any required `SLACK_USER_SCOPES` in Render, and register the exact Pages callback URL from step 4. Connect a test workspace and verify authorized channel access.
- Telegram: create a bot using [BotFather](https://t.me/BotFather) and connect its token in **Integrations**. The token is encrypted at rest. Add the bot to a group, adjust privacy via BotFather's `/setprivacy` if it must receive normal group messages, then send a new message so the group appears for explicit workspace/project association. Only new messages received after association are saved; the Bot API does not provide historical group-message sync.
- Keep Render's generated `INTEGRATION_ENCRYPTION_KEY` stable. Integration tokens are encrypted in the database using this key.

### Optional: 50 MiB file uploads with Cloudflare R2

To allow files larger than the 5 MiB PostgreSQL fallback limit, create an R2 bucket and S3 API credentials in Cloudflare. Add these variables to the Render API service:

- `FILE_STORAGE_ENDPOINT`: `https://<account-id>.r2.cloudflarestorage.com`
- `FILE_STORAGE_BUCKET`: the bucket name
- `FILE_STORAGE_ACCESS_KEY_ID`: the R2 access key ID
- `FILE_STORAGE_SECRET_ACCESS_KEY`: the R2 secret access key

The API stores the original object in R2 and records its object key and metadata in PostgreSQL. New uploads use R2 whenever it is configured. The per-file limit is 50 MiB. Upload progress and transfer errors are shown in the UI; uploads use a single request and are not resumable. Check [current R2 pricing and free usage limits](https://developers.cloudflare.com/r2/pricing/) before relying on a no-cost quota.

### Optional: invitation email through Resend

Verify a sender address/domain with Resend, then set `RESEND_API_KEY` and `INVITATION_FROM_EMAIL` in Render. The invitation workflow remains usable without email: the owner can copy the secure, one-time link and send it using another channel. The invited user must sign in with the matching email before accepting.

## Operational limits

This free setup is suitable for a hackathon demo and low-volume evaluation. A sleeping API, shared free quotas, limited compute/storage, and no production backup/SLA should be expected. Save database exports before important demos. Upgrade only if the app needs always-on responses, larger data, guaranteed backups, or higher throughput.

Do not paste passwords, database URLs, or API tokens into chat. Add them directly to the corresponding provider's secret settings. Live deployment requires signed-in Neon, Render, Cloudflare, and (for external integrations) model/GitHub/Slack accounts.
