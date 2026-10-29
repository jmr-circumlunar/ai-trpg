"""认证：密码哈希、会话令牌、登录限流。

设计（知识库 `过程记录/方案/05-登录与公网开放.md`）：
- 密码用 **PBKDF2-HMAC-SHA256**（标准库自带，不引依赖），16 字节随机 salt，20 万次迭代；
- 会话令牌 32 字节随机，**库里只存哈希** → 库被拖走也无法冒充登录；
- 登录失败限流：同「用户名 + IP」连续 5 次失败锁 5 分钟（单进程内存计数，重启即清）。

doc: 03-模块/M03-存储/00-模块总述.md
module: M03
layer: L2
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field

PBKDF2_ITERATIONS = 200_000
SESSION_DAYS = 7
COOKIE_NAME = "trpg_session"

ALGORITHM = "pbkdf2_sha256"


# ── 密码 ──────────────────────────────────────────────────────
def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    """返回 `pbkdf2_sha256$iterations$salt_hex$hash_hex`。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码。格式不认识或损坏一律 False（不抛异常给调用方）。"""
    try:
        algo, iterations, salt_hex, hash_hex = str(stored).split("$")
        if algo != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ── 会话令牌 ──────────────────────────────────────────────────
def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """令牌落库前先哈希（库里泄露也拿不到 cookie）。"""
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def session_expiry(now: float | None = None, days: int = SESSION_DAYS) -> str:
    from datetime import datetime, timezone

    ts = (now if now is not None else time.time()) + days * 86400
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def session_expired(expires_at: str, now: float | None = None) -> bool:
    from datetime import datetime, timezone

    try:
        end = datetime.fromisoformat(str(expires_at))
    except (ValueError, TypeError):
        return True
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end.timestamp() <= (now if now is not None else time.time())


# ── 登录限流（单进程内存；够用即可）──────────────────────────
@dataclass
class LoginLimiter:
    """同一「用户名 + IP」连续失败 N 次 → 锁一段时间。"""

    max_failures: int = 5
    window_seconds: int = 300
    _fails: dict[str, list[float]] = field(default_factory=dict)

    def _prune(self, key: str, now: float) -> list[float]:
        hits = [t for t in self._fails.get(key, []) if now - t < self.window_seconds]
        self._fails[key] = hits
        return hits

    def locked_for(self, key: str, now: float | None = None) -> int:
        """返回剩余锁定秒数（0 = 没锁）。"""
        now = now if now is not None else time.time()
        hits = self._prune(key, now)
        if len(hits) < self.max_failures:
            return 0
        return int(self.window_seconds - (now - hits[0])) + 1

    def fail(self, key: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self._prune(key, now)
        self._fails.setdefault(key, []).append(now)

    def success(self, key: str) -> None:
        self._fails.pop(key, None)


# 全局限流器（单进程共享）
LIMITER = LoginLimiter()
