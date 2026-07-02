# Placeholder

旧版静态前端已于 2026-07 移除（正式前端为 `ui-new/`，构建后挂载在 `/app`）。

保留本目录仅为兼容缓存了旧版 `Dockerfile`（含 `COPY ui ./ui`）的构建环境，
避免其因 `/ui: not found` 构建失败。当前 `Dockerfile` 已不引用本目录，
确认部署平台使用最新构建计划后可安全删除。
