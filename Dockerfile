ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${NODE_IMAGE} AS ui-builder

ARG NPM_REGISTRY=https://registry.npmmirror.com

WORKDIR /app
COPY ui-new/package*.json ./ui-new/
WORKDIR /app/ui-new
RUN npm config set registry "${NPM_REGISTRY}" \
    && npm ci
COPY ui-new/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS runtime

ARG USE_ALIYUN_APT_MIRROR=0
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1
ENV PORT=8080
ENV APP_ENV=production
ENV STORAGE_DIR=/var/data/storage
ENV SQLITE_PATH=/var/data/storage/ahcc.db
ENV CHROMA_PERSIST_DIR=/var/data/storage/chroma

WORKDIR /app

RUN if [ "${USE_ALIYUN_APT_MIRROR}" = "1" ] && [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i \
          -e 's|http://deb.debian.org/debian|http://mirrors.aliyun.com/debian|g' \
          -e 's|http://security.debian.org/debian-security|http://mirrors.aliyun.com/debian-security|g' \
          /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
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

RUN python -m pip install --upgrade pip -i "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" \
    && python -m pip install --no-cache-dir -e . -i "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" \
    && mkdir -p /var/data/storage

EXPOSE 8080

CMD ["sh", "-c", "uvicorn ahcc.api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
