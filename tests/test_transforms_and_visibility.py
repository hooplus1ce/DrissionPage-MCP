"""测试 FastMCP 转换器与组件可见性（ResourcesAsTools、Tags 可见性、会话隔离与工具搜索）。"""

from __future__ import annotations

import json
import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.transforms import ResourcesAsTools
from fastmcp.server.transforms.search import BM25SearchTransform

from drissionpage_mcp.server import (
    mcp,
    enable_feature_internal,
    disable_feature_internal,
    list_features,
    SUPPORTED_FEATURES,
)


@pytest.mark.asyncio
async def test_resources_as_tools_skills():
    """测试 ResourcesAsTools 转换器生成的工具能正常列出并读取所有技能。"""
    async with Client(mcp) as client:
        tools = [t.name for t in await client.list_tools()]
        assert "list_resources" in tools, "必须暴露 list_resources 工具"
        assert "read_resource" in tools, "必须暴露 read_resource 工具"

        # 列出所有资源
        res_list = await client.call_tool("list_resources", {})
        data = json.loads(res_list.data) if isinstance(res_list.data, str) else res_list.data
        uris = [item["uri"] for item in data]

        # 校验 3 大技能均已登记
        expected_skills = [
            "skill://scenario-generator/SKILL.md",
            "skill://aps-data-permission/SKILL.md",
            "skill://filter-vtable-audit/SKILL.md",
        ]
        for expected in expected_skills:
            assert expected in uris, f"未发现技能资源: {expected}"

        # 逐一读取技能内容
        for expected in expected_skills:
            read_res = await client.call_tool("read_resource", {"uri": expected})
            text = str(read_res.data)
            assert len(text) > 100, f"技能 {expected} 内容异常过短"
            assert "name:" in text or "# " in text, f"技能 {expected} 缺少有效内容"


@pytest.mark.asyncio
async def test_feature_suites_tags_visibility():
    disable_feature_internal("x6")
    async with Client(mcp) as client:
        # 禁用状态下 x6 应当被隐藏
        tools_initial = [t.name for t in await client.list_tools()]
        assert "x6_nodes" not in tools_initial

        # 启用 x6
        res_en = await client.call_tool("enable_feature", {"name": "x6"})
        assert "已启用特性套件 [x6]" in str(res_en.data)
        tools_en = [t.name for t in await client.list_tools()]
        assert "x6_nodes" in tools_en
        assert "x6_connect" in tools_en

        # 禁用 x6
        res_dis = await client.call_tool("disable_feature", {"name": "x6"})
        assert "已禁用特性套件 [x6]" in str(res_dis.data)
        tools_dis = [t.name for t in await client.list_tools()]
        assert "x6_nodes" not in tools_dis

        # 查看套件状态
        features = list_features()
        assert "x6" in features
        assert "vtable" in features
        assert features["x6"]["enabled"] is False

    enable_feature_internal("x6")


@pytest.mark.asyncio
async def test_dev_tool_lock_and_unlock():
    """测试底层 run_js 开发者工具的安全锁定与临时解锁。"""
    async with Client(mcp) as client:
        tools_initial = [t.name for t in await client.list_tools()]
        assert "run_js" not in tools_initial

        # 空授权凭证应报错
        with pytest.raises(Exception):
            await client.call_tool(
                "enable_dev_tool",
                {"name": "run_js", "user_explicit_instruction": ""},
            )

        # 正常解锁
        res_en = await client.call_tool(
            "enable_dev_tool",
            {"name": "run_js", "user_explicit_instruction": "执行自动化测试脚本"},
        )
        assert "已临时解锁工具 [run_js]" in str(res_en.data)
        tools_unlocked = [t.name for t in await client.list_tools()]
        assert "run_js" in tools_unlocked

        # 重新锁定
        res_dis = await client.call_tool("disable_dev_tool", {"name": "run_js"})
        assert "已锁定并隐藏工具 [run_js]" in str(res_dis.data)
        tools_locked = [t.name for t in await client.list_tools()]
        assert "run_js" not in tools_locked


@pytest.mark.asyncio
async def test_bm25_tool_search_transform():
    """测试 BM25SearchTransform 工具搜索机制与 always_visible 常驻。"""
    test_app = FastMCP("SearchTest")

    @test_app.tool
    def high_freq_tool() -> str:
        """高频工具"""
        return "high"

    @test_app.tool
    def low_freq_database_sync(db_name: str) -> str:
        """低频数据库同步操作"""
        return f"synced {db_name}"

    test_app.add_transform(
        BM25SearchTransform(
            always_visible=["high_freq_tool"],
            max_results=3,
        )
    )

    async with Client(test_app) as client:
        visible = [t.name for t in await client.list_tools()]
        assert "high_freq_tool" in visible
        assert "search_tools" in visible
        assert "call_tool" in visible
        assert "low_freq_database_sync" not in visible

        # 通过 search_tools 查找低频工具
        search_res = await client.call_tool("search_tools", {"query": "database sync"})
        discovered_names = [item["name"] for item in search_res.data]
        assert "low_freq_database_sync" in discovered_names

        # 通过 call_tool 代理执行
        call_res = await client.call_tool(
            "call_tool",
            {"name": "low_freq_database_sync", "arguments": {"db_name": "prod_db"}},
        )
        assert "synced prod_db" in str(call_res.data)
