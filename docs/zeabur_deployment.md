# Zeabur Deployment

Use Zeabur as a Full-stack Docker service, not a static site. This repository
contains a root `Dockerfile` that builds the React frontend first, copies
`ui-new/dist` into the Python image, and starts FastAPI as the same-origin
backend for `/`, `/app`, `/api`, and `/health`.

Recommended topology:

```text
Judge browser
  -> https://<zeabur-domain>/app#/cockpit
  -> Zeabur Docker service
  -> FastAPI serves React, API, uploads, checks, and PDF/Excel reports
```

## 1. Create Service

In Zeabur, create a new service from GitHub:

```text
stanleychujm0301/more-fillings-one-source-of-truth
```

Use these deployment settings:

```text
Service type: Dockerfile / Docker
Dockerfile path: Dockerfile
Root directory: /
Port: 8080
```

If Zeabur offers multiple detected project types, choose Dockerfile instead of
Node.js, Vite, or Static Site. Zeabur should run the full-stack Dockerfile.

## 2. Environment Variables

Set these variables in Zeabur:

```text
PORT=8080
APP_ENV=production
PYTHONUTF8=1
DEEPSEEK_API_KEY=<your real key>
STORAGE_DIR=/var/data/storage
SQLITE_PATH=/var/data/storage/ahcc.db
CHROMA_PERSIST_DIR=/var/data/storage/chroma
```

Do not set `VITE_API_ORIGIN` for the Zeabur Docker service. The React frontend
and FastAPI backend are same-origin inside the container.

## 3. Persistent Storage

If Zeabur offers a volume or disk setting, mount it at:

```text
/var/data
```

This keeps uploaded reports, generated jobs, SQLite state, and Chroma cache
available across redeploys.

## 4. Verify

After deployment, open:

```text
https://<zeabur-domain>/health
```

It should return JSON with `status`, `result_version`, and
`extraction_engine_version`.

Then open:

```text
https://<zeabur-domain>/app#/cockpit
```

Run a smoke test:

1. Upload two PDFs.
2. Click start check.
3. Wait for the job detail page.
4. Download PDF and Excel reports.

If build fails while loading base images, check that the deployment is using the
latest commit and the Dockerfile defaults are:

```text
NODE_IMAGE=node:22-bookworm-slim
PYTHON_IMAGE=python:3.12-slim
```

If Zeabur returns 502, confirm the service exposes port `8080`, the container
is healthy, and `/health` responds first.

## 5. 502 Troubleshooting

`502 Bad Gateway` from the Zeabur domain means the Zeabur gateway is reachable,
but it cannot connect to the running container. It is not a React hash-route
problem. Check these items in order:

1. The service is using the latest GitHub commit on `main`.
2. The service type is Dockerfile / Docker, not Node.js, Vite, or Static Site.
3. The exposed service port is `8080`; keep `PORT=8080` or leave `PORT` unset.
4. The runtime log shows Uvicorn starting with:

```text
Uvicorn running on http://0.0.0.0:8080
AHCC 启动：extraction_engine=2026-06-01.3 result_version=11
```

If the runtime log does not contain those lines, copy the first Python traceback
or container error from Zeabur logs. Common causes are a stale deployment commit,
wrong service type, wrong port, failed Docker image build, or a container crash
before Uvicorn starts.
