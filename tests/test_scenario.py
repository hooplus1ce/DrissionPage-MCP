"""D5 声明式场景回归引擎 (scenario_run) 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from drissionpage_mcp.scenario import parse_scenario, run_scenario, substitute_vars


def test_parse_scenario_various_formats(tmp_path):
    # 1. 直接 dict
    d = {"name": "test_dict", "steps": [{"step": "s1", "tool": "ping"}]}
    assert parse_scenario(d) == d

    # 2. YAML 字符串
    yaml_str = """
name: test_yaml_str
steps:
  - step: 步骤一
    tool: nav_menu
    args:
      menu_name: 产线管理
"""
    parsed = parse_scenario(yaml_str)
    assert parsed["name"] == "test_yaml_str"
    assert parsed["steps"][0]["tool"] == "nav_menu"

    # 3. JSON 字符串
    json_str = json.dumps(d)
    assert parse_scenario(json_str)["name"] == "test_dict"

    # 4. 文件路径
    f = tmp_path / "flow.yaml"
    f.write_text(yaml_str, encoding="utf-8")
    assert parse_scenario(str(f))["name"] == "test_yaml_str"


def test_substitute_vars():
    variables = {"user": "admin", "id": "1001", "url": "https://hoolinks.com"}
    text = "User ${user} with ID ${id} on ${url}"
    assert substitute_vars(text, variables) == "User admin with ID 1001 on https://hoolinks.com"

    nested = {
        "title": "Welcome ${user}",
        "params": ["${id}", {"link": "${url}/home"}],
    }
    expected = {
        "title": "Welcome admin",
        "params": ["1001", {"link": "https://hoolinks.com/home"}],
    }
    assert substitute_vars(nested, variables) == expected


async def test_scenario_run_success_flow(client, seeded_manager):
    _session, _chromium, tab = seeded_manager

    scenario = {
        "name": "test_happy_flow",
        "variables": {"prefix": "APS"},
        "steps": [
            {
                "step": "读取页面信息",
                "tool": "get_page_info",
                "args": {},
                "save": {"page_url": "url"},
            },
            {
                "step": "全屏截图",
                "tool": "screenshot",
                "args": {"full_page": False},
            },
        ],
    }

    res = await client.call_tool("scenario_run", {"scenario": scenario})
    data = res.data
    assert data.ok is True
    assert data.name == "test_happy_flow"
    assert data.total_steps == 2
    assert data.passed_steps == 2
    assert data.failed_step is None
    assert len(data.steps) == 2
    assert data.variables.get("tab_id") == tab.tab_id
    assert "example.com" in data.variables.get("page_url", "")


async def test_scenario_run_assertion_failure_stops(client, seeded_manager):
    scenario = {
        "name": "test_failure_flow",
        "steps": [
            {
                "step": "第一步：正常获取页面信息",
                "tool": "get_page_info",
                "args": {},
            },
            {
                "step": "第二步：断言一个不存在的消息（预期失败）",
                "tool": "wait_message",
                "args": {"pattern": "不可能存在的消息", "timeout": 0.3, "raise_if_not_found": False},
                "expect": {"status_ok": True},  # found=False 会触发 status_ok 断言失败
            },
            {
                "step": "第三步：不应被执行的步骤",
                "tool": "screenshot",
                "args": {},
            },
        ],
    }

    res = await client.call_tool("scenario_run", {"scenario": scenario, "stop_on_error": True})
    data = res.data
    assert data.ok is False
    assert data.total_steps == 2  # 第三步由于 stop_on_error 没执行
    assert data.passed_steps == 1
    assert "断言失败" in (data.failed_step or "")
