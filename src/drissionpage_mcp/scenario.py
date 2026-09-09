"""声明式场景回归执行引擎（D5 场景层）。

支持 YAML / JSON 格式的端到端测试场景，按步骤自动调用 MCP 工具、
传递上下文变量（如 ${tab_id}）并执行结果断言。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import yaml
from fastmcp.exceptions import ToolError

from .models import ScenarioRunResult, ScenarioStepResult

VAR_PATTERN = re.compile(r"\$\{([a-zA-Z_0-9]+)\}")
SCENARIOS_DIR = Path("scenarios")


def parse_scenario(source: str | dict[str, Any]) -> dict[str, Any]:
    """解析场景数据源（支持字典、文件路径、内联 YAML/JSON 字符串）。"""
    if isinstance(source, dict):
        return source

    text = str(source).strip()
    if not text:
        raise ToolError("场景定义不能为空")

    # 1. 尝试作为文件路径解析
    candidates = [
        Path(text),
        SCENARIOS_DIR / text,
        SCENARIOS_DIR / f"{text}.yaml",
        SCENARIOS_DIR / f"{text}.yml",
        SCENARIOS_DIR / f"{text}.json",
    ]
    for p in candidates:
        if p.is_file():
            raw = p.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                raise ToolError(f"场景文件必须是字典结构: {p}")
            return data

    # 2. 尝试作为内联 YAML/JSON 字符串解析
    try:
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        raise ToolError(f"场景解析失败: {exc}") from exc

    raise ToolError(f"未找到场景文件或合法的场景定义: {source!r}")


def substitute_vars(obj: Any, variables: dict[str, Any]) -> Any:
    """递归替换对象中的 ${var} 变量占位符。"""
    if isinstance(obj, str):

        def _replace(match: re.Match) -> str:
            var_name = match.group(1)
            return str(variables.get(var_name, match.group(0)))

        return VAR_PATTERN.sub(_replace, obj)
    if isinstance(obj, dict):
        return {k: substitute_vars(v, variables) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute_vars(x, variables) for x in obj]
    return obj


def _summarize_output(data: Any, content: list) -> str:
    if data is not None:
        if hasattr(data, "model_dump"):
            dumped = data.model_dump()
            return json.dumps(dumped, ensure_ascii=False)[:300]
        return str(data)[:300]
    if content:
        texts = [c.text for c in content if hasattr(c, "text")]
        return " ".join(texts)[:300]
    return ""


async def run_scenario(
    source: str | dict[str, Any],
    client,
    stop_on_error: bool = True,
) -> ScenarioRunResult:
    """运行声明式场景，返回完整的执行与断言结果。"""
    scenario_data = parse_scenario(source)
    name = str(scenario_data.get("name") or "unnamed_scenario")
    raw_steps = scenario_data.get("steps") or []
    if not raw_steps or not isinstance(raw_steps, list):
        raise ToolError("场景中未包含有效的 steps 步骤列表")

    variables: dict[str, Any] = dict(scenario_data.get("variables") or {})
    step_results: list[ScenarioStepResult] = []
    overall_ok = True
    failed_step_info = None
    start_time = time.time()

    for idx, raw_step in enumerate(raw_steps, start=1):
        if not isinstance(raw_step, dict):
            continue

        step_name = str(raw_step.get("step") or f"步骤 {idx}")
        tool_name = str(raw_step.get("tool") or "").strip()
        if not tool_name:
            continue

        raw_args = raw_step.get("args") or {}
        # 变量插值
        args = substitute_vars(raw_args, variables)

        step_t0 = time.time()
        step_ok = True
        step_err = None
        output_summary = ""

        try:
            res = await client.call_tool(tool_name, args)
            output_summary = _summarize_output(res.data, res.content)

            # 自动跟踪上下文变量（如 tab_id, context_id）
            if res.data is not None:
                for track_key in ("tab_id", "context_id", "browser_id"):
                    val = getattr(res.data, track_key, None)
                    if val:
                        variables[track_key] = str(val)

            # 自定义变量提取
            save_vars = raw_step.get("save") or {}
            if isinstance(save_vars, dict) and res.data is not None:
                for target_var, src_field in save_vars.items():
                    val = getattr(res.data, src_field, None)
                    if val is not None:
                        variables[target_var] = str(val)

            # 执行断言检查
            expect = raw_step.get("expect") or {}
            if isinstance(expect, dict):
                # 检查 status_ok
                if expect.get("status_ok") and res.data is not None:
                    ok_val = getattr(res.data, "ok", None)
                    found_val = getattr(res.data, "found", None)
                    if ok_val is False or found_val is False:
                        step_ok = False
                        step_err = f"断言失败: 返回结果指示操作未成功 (ok={ok_val}, found={found_val})"

                # 检查 message_contains
                msg_pat = expect.get("message_contains")
                if msg_pat:
                    msg_txt = ""
                    if res.data is not None:
                        msg_txt = str(
                            getattr(res.data, "message", None)
                            or getattr(res.data, "matched_text", None)
                            or ""
                        )
                    if not msg_txt and res.content:
                        msg_txt = " ".join(
                            c.text for c in res.content if hasattr(c, "text")
                        )
                    if msg_pat not in msg_txt:
                        step_ok = False
                        step_err = f"断言失败: 消息未包含期望的 [{msg_pat}]，实际输出: {msg_txt[:100]}"

        except Exception as exc:  # noqa: BLE001
            step_ok = False
            step_err = f"{type(exc).__name__}: {exc}"

        step_cost = round(time.time() - step_t0, 3)
        step_results.append(
            ScenarioStepResult(
                index=idx,
                step=step_name,
                tool=tool_name,
                ok=step_ok,
                elapsed_seconds=step_cost,
                output_summary=output_summary,
                error=step_err,
            )
        )

        # 步间休眠
        sleep_sec = raw_step.get("sleep")
        if sleep_sec:
            try:
                await asyncio.sleep(float(sleep_sec))
            except Exception:
                pass

        if not step_ok:
            overall_ok = False
            failed_step_info = f"第 {idx} 步 [{step_name}] 失败: {step_err}"
            if stop_on_error:
                break

    return ScenarioRunResult(
        ok=overall_ok,
        name=name,
        total_steps=len(step_results),
        passed_steps=sum(1 for s in step_results if s.ok),
        elapsed_seconds=round(time.time() - start_time, 3),
        failed_step=failed_step_info,
        steps=step_results,
        variables={k: str(v) for k, v in variables.items()},
    )
