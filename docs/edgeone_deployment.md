# Tencent EdgeOne Pages Deployment

Use Tencent EdgeOne Pages as the public static frontend entry. The real checker
backend must still run on a public FastAPI service because it needs Python, PDF
parsing, persistent storage, and PDF/Excel report generation.

Recommended topology:

```text
Judge browser
  -> https://<edgeone-domain>/#/cockpit
  -> EdgeOne Pages static frontend
  -> VITE_API_ORIGIN=https://<backend-origin>
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
Install command: npm run install:ui
Build command: npm run build
Output directory: ui-new/dist
```

Set these EdgeOne Pages environment variables:

```text
VITE_BASE_PATH=/
VITE_API_ORIGIN=https://<backend-origin>
```

Do not include a trailing slash in `VITE_API_ORIGIN`.

## 3. Public Entry

Use the EdgeOne public URL as the competition URL:

```text
https://<edgeone-domain>/#/cockpit
```

Run this smoke test before sharing it:

1. Open `https://<backend-origin>/health` directly.
2. Open `https://<edgeone-domain>/#/cockpit`.
3. Confirm the top status/ticker can connect to the backend.
4. Upload two PDFs.
5. Start a job.
6. Download PDF and Excel reports.

If the frontend opens but all API calls fail, check `VITE_API_ORIGIN` first. It
must be the public backend origin, not the EdgeOne domain and not `127.0.0.1`.

If `VITE_API_ORIGIN` is correct but job creation still fails, inspect the
FastAPI backend logs. At that point the request is reaching the backend service.
