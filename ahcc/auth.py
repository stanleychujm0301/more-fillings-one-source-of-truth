"""认证纯函数 — 密码哈希/校验、会话 token 生成（无 FastAPI 依赖，便于单测）。

- 密码：PBKDF2-HMAC-SHA256，200k 迭代，每用户随机 salt；校验用 hmac.compare_digest 防时序侧信道。
- 会话：secrets.token_urlsafe(32) 生成原始 token 发给客户端 Cookie；数据库只存 sha256(token)，
  即使库文件泄露也无法冒充在线会话。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

SESSION_COOKIE = "ahcc_session"
SESSION_TTL_DAYS = 7
_PBKDF2_ITERATIONS = 200_000

# 用户名规范：小写字母/数字开头，可含 _ -，3-32 字符（user_id 即用户名小写，PK 唯一性即大小写不敏感唯一）
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")

PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 64


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def is_valid_username(username: str) -> bool:
    return bool(USERNAME_PATTERN.fullmatch(normalize_username(username)))


def hash_password(password: str, *, salt: str | None = None, iterations: int = _PBKDF2_ITERATIONS) -> tuple[str, str, int]:
    """返回 (hash_hex, salt_hex, iterations)。salt 缺省时随机生成。"""
    salt_hex = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        iterations,
    ).hex()
    return digest, salt_hex, iterations


def verify_password(password: str, salt: str, expected_hash: str, iterations: int | None) -> bool:
    if not salt or not expected_hash:
        return False
    rounds = int(iterations or _PBKDF2_ITERATIONS)
    try:
        candidate, _, _ = hash_password(password, salt=salt, iterations=rounds)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected_hash)


def new_session_token() -> tuple[str, str]:
    """返回 (raw_token, token_hash_sha256_hex)。raw 只出现在 Cookie，hash 入库。"""
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_session_token(raw_token: str) -> str:
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()
