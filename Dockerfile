FROM node:20-bookworm-slim AS ui-builder

WORKDIR /app
COPY ui-new/package*.json ./ui-new/
WORKDIR /app/ui-new
RUN npm ci
COPY ui-new/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1
ENV PORT=8001
ENV APP_ENV=production
ENV STORAGE_DIR=/var/data/storage
ENV SQLITE_PATH=/var/data/storage/ahcc.db
ENV CHROMA_PERSIST_DIR=/var/data/storage/chroma

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        fonts-noto-cjk \
        ghostscript \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY ahcc ./ahcc
COPY kb ./kb
COPY rules ./rules
COPY ui ./ui
COPY --from=ui-builder /app/ui-new/dist ./ui-new/dist

RUN pip install --upgrade pip \
    && pip install --no-cache-dir -e . \
    && mkdir -p /var/data/storage

EXPOSE 8001

CMD ["sh", "-c", "uvicorn ahcc.api.main:app --host 0.0.0.0 --port ${PORT:-8001}"]
