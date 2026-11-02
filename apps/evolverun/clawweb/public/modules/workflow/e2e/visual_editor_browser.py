"""Real Chromium regression, served entirely from the public ClawWeb build.

Build public/app/clawweb/dist/web first; no Vite or backend server is needed.
--base-url is a synthetic loopback origin, not a network destination. The script
requires Python Playwright 1.58+, its Chromium installation, and PyYAML. Browser
launch must be run by the coordinating thread with the required approval.
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from visual_editor_network import (
    OfflineNetworkGuard,
    create_guarded_context,
    default_dist_dir,
)

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


SPEC: dict[str, Any] = {
    "id": "editor-test",
    "title": "工单分析与处置",
    "version": "1",
    "custom": {"enabled": False, "count": 0, "nil": None},
    "nodes": [
        {
            "id": "route",
            "title": "前置路由检测",
            "phase": "P1",
            "executor": {
                "type": "embedded-agent",
                "prompt": "判断是否需要进一步分析，返回 needsAnalysis 布尔值。",
                "outputMode": "json",
                "custom": {"preserve": True},
            },
            "dependsOn": [],
            "onResult": {
                "branches": [
                    {
                        "branchId": "analyze",
                        "match": {"needsAnalysis": True},
                        "complete": True,
                    },
                    {
                        "branchId": "archive",
                        "match": {"needsAnalysis": False},
                        "complete": False,
                    },
                ]
            },
            "outputContract": {"required": True, "schema": {"type": "object"}},
        },
        {
            "id": "parse",
            "title": "解析告警参数",
            "phase": "P2",
            "executor": {
                "type": "cli-script",
                "command": "python3",
                "args": ["scripts/example.py", "--ticket", "{{input.params.ticketId}}"],
                "env": {"DEMO": "example"},
                "workdir": "workspace",
            },
            "dependsOn": ["route"],
            "branchId": "analyze",
        },
        {
            "id": "archive",
            "title": "直接归档",
            "phase": "P2",
            "executor": {"type": "done", "message": "本路径完成"},
            "dependsOn": ["route"],
            "branchId": "archive",
        },
        {
            "id": "query",
            "title": "查询核心数据",
            "phase": "P3",
            "executor": {
                "type": "mcp-call",
                "server": "demo",
                "tool": "query",
                "args": {},
            },
            "dependsOn": ["parse"],
        },
        {
            "id": "summary",
            "title": "汇总分析",
            "phase": "P4",
            "executor": {
                "type": "embedded-agent",
                "prompt": "总结输入的结果。",
                "outputMode": "json",
            },
            "dependsOn": ["query"],
        },
        {
            "id": "finish",
            "title": "流程完成",
            "phase": "P5",
            "executor": {"type": "done"},
            "dependsOn": ["summary", "archive"],
            "triggerRule": "one_success",
        },
    ],
}

UI_GEOMETRY_FIELDS = frozenset(
    {"width", "height", "measured", "position", "positionAbsolute", "_x", "_y"}
)


def bounding_box(locator: Locator) -> dict[str, float]:
    box = locator.bounding_box()
    if box is None:
        raise AssertionError(f"Expected a visible bounding box for {locator}")
    return {
        "x": box["x"],
        "y": box["y"],
        "width": box["width"],
        "height": box["height"],
    }


def node_position(locator: Locator) -> dict[str, float]:
    """Read rendered flow coordinates, not the editor's internal store."""
    return locator.evaluate(
        """node => {
            const matrix = new DOMMatrixReadOnly(getComputedStyle(node).transform);
            return {x: matrix.m41, y: matrix.m42};
        }"""
    )


def has_no_ui_geometry(spec: dict[str, Any]) -> bool:
    """This fixture has no legacy coordinates or measurements to preserve."""
    return not UI_GEOMETRY_FIELDS.intersection(spec) and all(
        not UI_GEOMETRY_FIELDS.intersection(node) for node in spec["nodes"]
    )


def measure_minimap(page: Page, phase: str, node_count: int) -> dict[str, Any]:
    """Wait for every actual SVG mini-node to have positive rendered dimensions."""
    page.wait_for_function(
        """expected => {
            const nodes = [...document.querySelectorAll('.react-flow__minimap-node')];
            return document.querySelectorAll('.react-flow__node').length === expected
                && nodes.length === expected
                && nodes.every(node => {
                    const width = Number(node.getAttribute('width'));
                    const height = Number(node.getAttribute('height'));
                    const box = node.getBoundingClientRect();
                    return Number.isFinite(width) && width > 0
                        && Number.isFinite(height) && height > 0
                        && box.width > 0 && box.height > 0;
                });
        }""",
        arg=node_count,
    )
    nodes = page.locator(".react-flow__minimap-node").evaluate_all(
        """nodes => nodes.map(node => ({
            width: Number(node.getAttribute('width')),
            height: Number(node.getAttribute('height')),
            renderedWidth: node.getBoundingClientRect().width,
            renderedHeight: node.getBoundingClientRect().height
        }))"""
    )
    # Recheck the recorded evidence, not only the preceding animation-frame poll.
    if len(nodes) != node_count or not all(
        all(math.isfinite(value) and value > 0 for value in node.values())
        for node in nodes
    ):
        raise AssertionError(f"MiniMap lost measured nodes during {phase}: {nodes}")
    return {"phase": phase, "node_count": node_count, "minimap_nodes": nodes}


def run_checks(
    page: Page,
    guard: OfflineNetworkGuard,
    output: Path,
    passed: list[str],
    errors: list[str],
    measurements: dict[str, Any],
) -> None:
    import yaml
    from playwright.sync_api import expect

    def check(label: str, condition: bool) -> None:
        guard.assert_clean()
        if not condition:
            raise AssertionError(label)
        passed.append(label)
        print("PASS", label, flush=True)

    def get_doc() -> dict[str, Any]:
        return yaml.safe_load(page.get_by_label("工作流 YAML").input_value())

    def get_node(node_id: str) -> dict[str, Any]:
        return next(node for node in get_doc()["nodes"] if node["id"] == node_id)

    def record_minimap(phase: str) -> None:
        measurements["minimap"].append(
            measure_minimap(page, phase, len(get_doc()["nodes"]))
        )

    page.goto(
        guard.base_url + "/workflows/workspace?workflowId=editor-test&tab=editor",
        wait_until="networkidle",
    )
    expect(page.locator(".react-flow__node")).to_have_count(6)
    check(
        "real workspace loads synthetic definition without runtime errors",
        not errors and len(get_doc()["nodes"]) == 6,
    )
    record_minimap("initial load")
    check(
        "initial minimap has one positive-size measured node per canvas node",
        len(measurements["minimap"][-1]["minimap_nodes"]) == 6,
    )
    check(
        "editor rail is compact and accessible",
        bounding_box(page.get_by_label("任务护航导航"))["width"] < 100,
    )
    page.locator('.react-flow__node[data-id="route"] h3').click()
    expect(page.get_by_label("节点名称", exact=True)).to_have_value("前置路由检测")
    record_minimap("ready selection")
    page.screenshot(path=str(output / "editor-ready.png"), full_page=True)
    page.get_by_label("节点名称", exact=True).fill("路由判断（已编辑）")
    record_minimap("after title edit")
    page.get_by_label("任务指令", exact=True).fill(
        "{This stays a string: false, 0, null}"
    )
    check(
        "business fields update without converting text to JSON",
        get_node("route")["executor"]["prompt"]
        == "{This stays a string: false, 0, null}",
    )
    check(
        "unknown fields and output contract survive visible edits",
        get_doc()["custom"] == SPEC["custom"]
        and get_node("route")["outputContract"] == SPEC["nodes"][0]["outputContract"]
        and get_node("route")["executor"]["custom"] == {"preserve": True},
    )
    page.screenshot(path=str(output / "editor-ai.png"), full_page=True)
    page.get_by_role("button", name="YAML", exact=True).click()
    draft = page.get_by_label("工作流 YAML").input_value() + "\nfutureFlag: false\n"
    page.get_by_label("工作流 YAML").fill(draft)
    page.get_by_role("button", name="画布", exact=True).click()
    page.get_by_role("button", name="保存", exact=True).click()
    check(
        "unapplied YAML survives view switches and blocks server save",
        page.get_by_label("工作流 YAML").input_value() == draft
        and not any(call["path"] == "/api/workflows/save" for call in guard.calls),
    )
    page.get_by_role("button", name="YAML", exact=True).click()
    page.get_by_role("button", name="校验并应用", exact=True).click()
    expect(
        page.get_by_text("已应用 6 个节点的配置。尚未保存到服务器。")
    ).to_be_visible()
    check("validated YAML applies to the real store", get_doc()["futureFlag"] is False)
    page.get_by_role("button", name="画布", exact=True).click()
    page.get_by_label("查找画布节点").fill("解析告警")
    page.get_by_label("查找画布节点").press("Enter")
    expect(page.get_by_label("节点名称", exact=True)).to_have_value("解析告警参数")
    record_minimap("after selection change")
    page.get_by_text("原始参数 JSON", exact=True).click()
    # JSON editor stays local until Apply, including valid-but-unapplied drafts.
    args_field = page.get_by_label("完整参数", exact=True)
    old_args = get_node("parse")["executor"]["args"]
    args_field.fill("[invalid")
    check(
        "invalid JSON is isolated from workflow state",
        get_node("parse")["executor"]["args"] == old_args,
    )
    expect(page.get_by_role("button", name="复制节点", exact=True)).to_be_disabled()
    args_field.fill(json.dumps(old_args + ["--demo"]))
    page.get_by_role("button", name="应用 完整参数", exact=True).click()
    check(
        "explicit JSON apply preserves sibling executor config",
        get_node("parse")["executor"]["args"] == old_args + ["--demo"]
        and get_node("parse")["executor"]["env"] == {"DEMO": "example"},
    )
    page.screenshot(path=str(output / "editor-script.png"), full_page=True)
    page.get_by_label("搜索节点类型").fill("完成")
    expect(page.get_by_role("button", name="添加完成", exact=True)).to_be_visible()
    page.get_by_role("button", name="添加完成", exact=True).drag_to(
        page.get_by_test_id("editor-canvas-dropzone"),
        target_position={"x": 110, "y": 340},
    )
    expect(page.locator(".react-flow__node")).to_have_count(7)
    added = get_doc()["nodes"][-1]["id"]
    check(
        "native palette drag creates a real node with phase and no fake values",
        get_node(added)["executor"] == {"type": "done"}
        and get_node(added)["phase"] == "P1"
        and "retry" not in get_node(added),
    )
    page.get_by_label("搜索节点类型").fill("")
    page.get_by_role("button", name="总览", exact=True).click()
    # The existing fit-view animation takes 240 ms; let it settle before measuring.
    page.wait_for_timeout(350)
    before_yaml = get_doc()
    node_selector = f'.react-flow__node[data-id="{added}"]'
    node = page.locator(node_selector)
    before_box = bounding_box(node)
    before_position = node_position(node)
    card = bounding_box(node.locator("h3"))
    start_x = card["x"] + card["width"] / 2
    start_y = card["y"] + card["height"] / 2
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + 60, start_y + 45, steps=10)
    page.mouse.up()
    # A panned viewport can move the bounding box without moving the node. Require
    # both screen displacement and a changed node-local transform after mouseup.
    page.wait_for_function(
        """({selector, position, box}) => {
            const node = document.querySelector(selector);
            if (!node) return false;
            const matrix = new DOMMatrixReadOnly(getComputedStyle(node).transform);
            const current = node.getBoundingClientRect();
            return Math.hypot(matrix.m41 - position.x, matrix.m42 - position.y) > 1
                && Math.hypot(current.x - box.x, current.y - box.y) >= 20;
        }""",
        arg={"selector": node_selector, "position": before_position, "box": before_box},
    )
    after_box = bounding_box(node)
    after_position = node_position(node)
    screen_distance = math.hypot(
        after_box["x"] - before_box["x"], after_box["y"] - before_box["y"]
    )
    flow_distance = math.hypot(
        after_position["x"] - before_position["x"],
        after_position["y"] - before_position["y"],
    )
    measurements["drag"] = {
        "node_id": added,
        "before_box": before_box,
        "after_box": after_box,
        "before_position": before_position,
        "after_position": after_position,
        "screen_distance": screen_distance,
        "flow_distance": flow_distance,
    }
    check(
        "real node drag changes rendered position, not just an unchanged YAML assertion",
        screen_distance >= 20 and flow_distance > 1,
    )
    check(
        "node movement does not serialize new coordinates or alter execution spec",
        get_doc() == before_yaml,
    )
    record_minimap("after node drag")
    check(
        "minimap stays measured after title edits, selection and dragging without spec dimensions",
        len(measurements["minimap"]) == 5 and has_no_ui_geometry(get_doc()),
    )
    source = bounding_box(page.get_by_label("分支 analyze 输出", exact=True))
    target = bounding_box(page.get_by_label("完成 输入", exact=True))
    page.mouse.move(
        source["x"] + source["width"] / 2, source["y"] + source["height"] / 2
    )
    page.mouse.down()
    page.mouse.move(
        target["x"] + target["width"] / 2,
        target["y"] + target["height"] / 2,
        steps=15,
    )
    page.mouse.up()
    expect(page.get_by_text("7 个节点 · 7 条连线", exact=True)).to_be_visible()
    check(
        "real port drag creates branch membership even when complete=true",
        get_node(added)["dependsOn"] == ["route"]
        and get_node(added)["branchId"] == "analyze",
    )
    # Undo/redo use actual UI, not a synthetic store hook.
    toolbar = page.get_by_role("toolbar", name="画布工具栏")
    toolbar.get_by_role("button", name="撤销", exact=True).click()
    check(
        "undo removes only the last connection",
        not get_node(added).get("dependsOn") and len(get_doc()["nodes"]) == 7,
    )
    toolbar.get_by_role("button", name="重做", exact=True).click()
    check("redo restores the connection", get_node(added)["dependsOn"] == ["route"])
    # Focus a non-branch leaf to exercise deletion confirmation and store history.
    page.get_by_label("查找画布节点").fill(added)
    page.get_by_label("查找画布节点").press("Enter")
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_label("节点配置", exact=True).get_by_role(
        "button", name="删除节点", exact=True
    ).click()
    expect(page.locator(".react-flow__node")).to_have_count(6)
    toolbar.get_by_role("button", name="撤销", exact=True).click()
    check(
        "confirmed node deletion is reversible",
        len(get_doc()["nodes"]) == 7 and get_node(added)["dependsOn"] == ["route"],
    )
    page.get_by_role("button", name="保存", exact=True).click()
    # Only this confirmation may perform the two synthetic write requests.
    with guard.allow_save():
        page.get_by_role("button", name="保存工作流", exact=True).click()
        expect(
            page.get_by_text("工作流已保存（git saved/editor-test · demo1234）")
        ).to_be_visible()
    check(
        "save uses the existing DB then Git flow, both intercepted",
        [
            call["path"]
            for call in guard.calls
            if call["path"] in {"/api/workflows/save", "/api/auto-heal/save"}
        ]
        == ["/api/workflows/save", "/api/auto-heal/save"],
    )
    check(
        "saved payload preserves hidden values",
        guard.saved["custom"] == SPEC["custom"]
        and guard.saved["nodes"][0]["outputContract"]["required"] is True
        and "futureFlag" in guard.saved
        and has_no_ui_geometry(guard.saved),
    )
    page.get_by_role("button", name="部署", exact=True).click()
    expect(page.get_by_text("部署工作流", exact=True)).to_be_visible()
    check(
        "deploy remains explicit confirmation only",
        not any(call["path"] == "/api/auto-heal/deploy" for call in guard.calls),
    )
    page.get_by_role("button", name="取消", exact=True).click()
    page.get_by_role("button", name="总览", exact=True).click()
    page.wait_for_timeout(300)
    page.screenshot(path=str(output / "editor-overview.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    check(
        "desktop workspace fits 1440px",
        page.evaluate("document.documentElement.scrollWidth") == 1440,
    )
    check(
        "browser has no uncaught errors or non-loopback traffic",
        not errors and not guard.external and not guard.violations,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:5189",
        help="Synthetic loopback origin. No server is contacted or required.",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=default_dist_dir(),
        help="Built public ClawWeb dist/web directory (defaults relative to __file__).",
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=Path(tempfile.gettempdir()) / "clawweb-editor-browser",
    )
    args = parser.parse_args()
    guard = OfflineNetworkGuard(args.base_url, args.dist_dir, SPEC)
    output = args.artifacts.resolve()
    output.mkdir(parents=True, exist_ok=True)
    passed: list[str] = []
    errors: list[str] = []
    measurements: dict[str, Any] = {"minimap": []}
    success = False
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = create_guarded_context(browser, guard)
                context.on(
                    "page",
                    lambda page: page.on(
                        "pageerror", lambda error: errors.append(str(error))
                    ),
                )
                context.add_init_script(
                    "window.__TERN__={user:{outUserNo:'demo-user',nickName:'演示用户'}}"
                )
                page = context.new_page()
                run_checks(page, guard, output, passed, errors, measurements)
                context.close()
            finally:
                browser.close()
            guard.assert_clean()
            if errors:
                raise AssertionError(f"Browser errors: {errors}")
            success = True
    except Exception as error:
        errors.append(f"{type(error).__name__}: {error}")
        raise
    finally:
        # Preserve violations and partial check counts even if a UI wait times out.
        report = {
            "success": success,
            "passed": len(passed),
            "checks": passed,
            "errors": errors,
            "api_calls": guard.calls,
            "external": guard.external,
            "network_violations": guard.violations,
            "base_url": guard.base_url,
            "dist_dir": str(guard.dist_dir),
            "measurements": measurements,
        }
        (output / "results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("ALL" if success else "FAILED AFTER", len(passed), "PASSED", flush=True)


if __name__ == "__main__":
    main()
