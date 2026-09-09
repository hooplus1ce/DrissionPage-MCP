"""声明式场景回归工具（D5）。"""

from __future__ import annotations

from typing import Any

from fastmcp import Client, FastMCP

from ..models import ScenarioRunResult
from ..scenario import run_scenario

# 领域子服务器：由 server.py mount 组合（官方 composition 模式）
mcp = FastMCP("Scenario")


@mcp.tool(
    tags={"scenario", "automation"},
    annotations={"title": "运行声明式回归场景", "readOnlyHint": False},
)
async def scenario_run(
    scenario: str | dict[str, Any],
    stop_on_error: bool = True,
) -> ScenarioRunResult:
    """运行声明式测试回归场景（支持 YAML/JSON 字典、文件路径或内联文本）。

    支持多步骤自动编排、上下文变量传递（如 ${tab_id}、${context_id}）、
    结果断言（status_ok、message_contains）及失败原因定位。

    Args:
        scenario: 场景定义，可为文件路径（如 "scenarios/approval_flow.yaml"）、
            场景字典结构、或包含 YAML 文本的字符串
        stop_on_error: 遇到单步断言或执行失败时是否立即终止场景（默认 True）
    """
    from ..server import mcp as root_server

    async with Client(root_server) as client:
        return await run_scenario(scenario, client, stop_on_error=stop_on_error)
