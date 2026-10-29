"""认证、账号与探活（M14）。

只做 HTTP 解析与序列化，玩法逻辑一律下沉到 engine/ 与各模块。

doc: 03-模块/M14-接口与前端/00-模块总述.md
module: M14
layer: L6
"""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import *  # noqa: F401,F403
from ..schemas import *  # noqa: F401,F403

router = APIRouter()

def _require_admin(request: Request) -> dict:
    account = getattr(request.state, "account", None)
    if not account or account.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return account


@router.post("/api/login")
def api_login(req: LoginReq, request: Request, response: Response):
    """账号密码登录。成功后下发 HttpOnly 会话 cookie。"""
    username = str(req.username or "").strip()
    client_host = request.client.host if request.client else "?"
    key = f"{username}|{client_host}"

    locked = auth_mod.LIMITER.locked_for(key)
    if locked:
        raise HTTPException(429, f"尝试次数过多，请 {locked} 秒后再试")

    conn = _new_conn()
    try:
        account = db.get_account(conn, username)
        ok = bool(
            account
            and not account["disabled"]
            and auth_mod.verify_password(str(req.password or ""), account["password_hash"])
        )
        if not ok:
            auth_mod.LIMITER.fail(key)
            raise HTTPException(401, "用户名或密码不对")
        auth_mod.LIMITER.success(key)
        token = auth_mod.new_token()
        db.insert_auth_session(
            conn,
            auth_mod.token_hash(token),
            account["id"],
            auth_mod.session_expiry(days=SESSION_DAYS),
            request.headers.get("user-agent", ""),
        )
        db.touch_login(conn, account["id"])
    finally:
        conn.close()

    response.set_cookie(
        auth_mod.COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIE,
        max_age=SESSION_DAYS * 86400,
        path="/",
    )
    return {"ok": True, "username": account["username"], "role": account["role"]}


@router.post("/api/logout")
def api_logout(request: Request, response: Response):
    token = request.cookies.get(auth_mod.COOKIE_NAME)
    if token:
        conn = _new_conn()
        try:
            db.delete_auth_session(conn, auth_mod.token_hash(token))
        finally:
            conn.close()
    response.delete_cookie(auth_mod.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/api/me")
def api_me(request: Request):
    account = getattr(request.state, "account", None)
    if not account:
        raise HTTPException(401, "未登录")
    return account


@router.get("/api/accounts")
def api_list_accounts(request: Request):
    _require_admin(request)
    conn = _new_conn()
    try:
        return {"accounts": db.list_accounts(conn)}
    finally:
        conn.close()


@router.post("/api/accounts")
def api_create_account(req: AccountReq, request: Request):
    """管理员建账号（公网不开放自助注册）。"""
    _require_admin(request)
    username = str(req.username or "").strip()
    password = str(req.password or "")
    if not username or len(password) < 8:
        raise HTTPException(400, "用户名不能为空，密码至少 8 位")
    conn = _new_conn()
    try:
        if db.get_account(conn, username):
            raise HTTPException(409, "用户名已存在")
        aid = db.create_account(
            conn,
            username=username,
            password_hash=auth_mod.hash_password(password),
            role=str(req.role or "user"),
        )
        return {"ok": True, "id": aid, "username": username}
    finally:
        conn.close()

@router.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    page = ROOT / "static" / "login.html"
    if not page.exists():
        return "<h1>ai-trpg</h1><p>static/login.html 不存在</p>"
    return page.read_text(encoding="utf-8")

@router.get("/api/health")
def health() -> dict:
    return health_snapshot(len(app.state.sessions))


# ── 角色库 ──────────────────────────────────────────────────
