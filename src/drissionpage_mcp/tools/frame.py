"""iframe frame 管理工具。

APS 等系统中每个功能模块以 iframe 形式挂载在顶级 DOM，
激活态（可见）的 iframe 即当前交互的功能页面。
"""

from __future__ import annotations

from ..manager import manager
from ..models import FrameInfo
from fastmcp import FastMCP

# 领域子服务器：由 server.py mount 组合（官方 composition 模式）
mcp = FastMCP("Frames")


@mcp.tool(
    tags={"frame"},
    annotations={"title": "列出 iframe", "readOnlyHint": True},
)
def frame_list(tab_id: str | None = None, browser_id: str | None = None) -> list[FrameInfo]:
    """列出标签页顶级 DOM 中的所有 iframe 及其可见状态（可见即激活态功能模块）。

    返回的 frame_index / iframe_id 可作为其他工具 frame 参数的取值。
    """
    return manager.list_frames(tab_id, browser_id)
