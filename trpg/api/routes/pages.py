"""页面路由（静态 HTML）（M14）。

只做 HTTP 解析与序列化，玩法逻辑一律下沉到 engine/ 与各模块。

doc: 03-模块/M14-接口与前端/00-模块总述.md
module: M14
layer: L6
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..deps import *  # noqa: F401,F403
from ..schemas import *  # noqa: F401,F403

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
def index() -> str:
    """玩家页。优先 Vue 构建产物（`static/dist/index.html`），否则回退旧静态页。"""
    return _serve_page("index.html")

@router.get("/worlds", response_class=HTMLResponse)
def worlds_page() -> str:
    """P3：登录后第一站——选世界，历史局优先。"""
    return _serve_page("worlds.html")


@router.get("/lobby", response_class=HTMLResponse)
def lobby_page() -> str:
    """P3：开局前的席位与参数（配置在这里成型，进局即锁）。"""
    return _serve_page("lobby.html")


@router.get("/archive", response_class=HTMLResponse)
def archive_page() -> str:
    """P3：某一世界的历史局。"""
    return _serve_page("archive.html")


@router.get("/play", response_class=HTMLResponse)
def play_page() -> str:
    """对局页（`?sid=` 指定存档；配置控件在局内隐藏）。"""
    return _serve_page("index.html")


@router.get("/agents", response_class=HTMLResponse)
def agents_page() -> str:
    """Agent 库（全局人格配置，方案 36）。"""
    return _serve_page("index.html")


@router.get("/setup", response_class=HTMLResponse)
def setup_page() -> str:
    """房间（号位 × agent，方案 35）：同一套前端壳子按路径切页。"""
    return _serve_page("index.html")


@router.get("/world/{world_id}/agents", response_class=HTMLResponse)
def world_agents_page(world_id: str) -> str:
    """本世界 Agent 配置页（参战名单；人格仍在全局 Agent 库）。"""
    return _serve_page("index.html")


@router.get("/world/{world_id}/saves", response_class=HTMLResponse)
def saves_page(world_id: str) -> str:
    """世界存档页（1 个自动 + 5 个命名槽）。"""
    return _serve_page("index.html")


@router.get("/world/{world_id}", response_class=HTMLResponse)
def world_page(world_id: str) -> str:
    """世界详情（存档槽 + 新建对局，方案 35）。

    前端按路径切页（`src/lib/route.js`），所以这里只要把同一个壳子发出去。
    """
    return _serve_page("index.html")


@router.get("/characters", response_class=HTMLResponse)
def characters_page() -> str:
    page = ROOT / "static" / "characters.html"
    if not page.exists():
        return "<h1>ai-trpg</h1><p>static/characters.html 不存在</p>"
    return page.read_text(encoding="utf-8")


@router.get("/trace", response_class=HTMLResponse)
def trace_page() -> str:
    page = ROOT / "static" / "trace.html"
    if not page.exists():
        return "<h1>ai-trpg</h1><p>static/trace.html 不存在</p>"
    return page.read_text(encoding="utf-8")


@router.get("/gm", response_class=HTMLResponse)
def gm_page() -> str:
    """GM 控制台。优先 Vue 构建产物（`static/dist/gm.html`）。"""
    return _serve_page("gm.html")


# 允许通过 /static 暴露的静态资源类型（只放前端资产，不放数据）
_STATIC_SUFFIXES = {".js", ".css", ".svg", ".png", ".ico", ".woff2"}


@router.get("/static/{name:path}")
def static_asset(name: str) -> FileResponse:
    """前端静态资源（`blocks.js`、Vue 构建产物 `assets/*.js` 等）。

    允许子目录：Vite 的产物是 `assets/xxx-<hash>.js` 这类路径。
    仍只放前端资产，不放数据；后缀白名单挡住其余文件。
    """
    base = (ROOT / "static").resolve()
    path = (base / name).resolve()
    if path.suffix.lower() not in _STATIC_SUFFIXES:
        raise HTTPException(status_code=404, detail="不允许的静态资源类型")
    if not path.is_file() or base not in path.parents:
        raise HTTPException(status_code=404, detail="静态资源不存在")
    return FileResponse(path)

