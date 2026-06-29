# Tencent EdgeOne Pages Deployment

This project can use Tencent EdgeOne Pages as the public competition entry, but
EdgeOne Pages should be treated as the static frontend plus edge proxy layer.
The real checker backend still needs a public FastAPI service that can run
Python, parse PDFs, write storage, and generate PDF/Excel reports.

Recommended topology:

```text
Judge browser
  -> https://<edgeone-domain>/#/cockpit
  -> EdgeOne Pages static React build
  -> EdgeOne Pages Functions proxy /api/* and /health
  -> public FastAPI backend such as Render, SAE, CVM, Lighthouse, or another Docker service
```

## 1. Deploy The FastAPI Backend First

Use any service that can run the repository `Dockerfile` or a normal Python
process. The backend must expose:

- `/health`
- `/api/session/current`
- `/api/jobs/`
- `/api/jobs/{job_id}/report.pdf`
- `/api/jobs/{job_id}/report.xlsx`

Required backend environment variables:

```text
PORT=8001
APP_ENV=production
PYTHONUTF8=1
DEEPSEEK_API_KEY=<your real key>
STORAGE_DIR=/var/data/storage
SQLITE_PATH=/var/data/storage/ahcc.db
CHROMA_PERSIST_DIR=/var/data/storage/chroma
```

After backend deployment, verify:

```bash
curl https://<backend-origin>/health
```

The response should include `status`, `result_version`, and
`extraction_engine_version`.

## 2. Create EdgeOne Pages Project

Connect this GitHub repository:

```text
stanleychujm0301/more-fillings-one-source-of-truth
```

Use these build settings:

```text
Framework preset: Vite or Other
Root directory: /
Install command: cd ui-new && npm ci
Build command: cd ui-new && npm run build
Output directory: ui-new/dist
```

Set this EdgeOne Pages environment variable:

```text
VITE_BASE_PATH=/
```

This makes the generated frontend assets work from the EdgeOne root domain.

## 3. Configure EdgeOne Functions Proxy

This repository includes:

```text
functions/_proxy.js
functions/api/[[default]].js
functions/health.js
```

Set the EdgeOne Pages Functions environment variable:

```text
BACKEND_ORIGIN=https://<backend-origin>
```

Do not include a trailing slash.

The proxy keeps the frontend code simple: React continues to request `/api/...`
and `/health`, and EdgeOne forwards those requests to the FastAPI backend.

## 4. Public Entry

Use the EdgeOne public URL as the competition URL:

```text
https://<edgeone-domain>/#/cockpit
```

Run this smoke test before sharing it:

1. Open `https://<edgeone-domain>/health`.
2. Confirm it returns backend health JSON.
3. Open `https://<edgeone-domain>/#/cockpit`.
4. Upload two PDFs.
5. Start a job.
6. Download PDF and Excel reports.

If `/health` returns `BACKEND_ORIGIN is not configured`, the EdgeOne function
environment variable has not been set or has not been redeployed.

If `/health` works but job creation fails, inspect the backend logs first. The
request has already passed through EdgeOne and reached FastAPI.
