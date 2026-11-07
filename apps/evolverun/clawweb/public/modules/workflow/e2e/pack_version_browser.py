"""Bounded, entirely offline real-build Chromium Pack-version UI regression.

Run: python3 public/modules/workflow/e2e/pack_version_browser.py --repeat 3
Always builds public/app/clawweb with its installed Vite (no npm download, no old
dist). Each journey gets a fresh browser context. All requests are fulfilled from
that build or exact fixtures, otherwise aborted; no PRE/PROD/backend/Git is used.
Evidence is UI/wire-contract evidence ONLY, not proof of Git/SQLite correctness.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import os
import subprocess
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

from pack_version_fixture import (
    BASE_COMMIT,
    BINARY,
    EDITED_SCRIPT,
    LATER_COMMIT,
    SAVED_COMMIT,
    SCRIPT,
    A,
    B,
    PackFixture,
    file,
    spec,
)
from playwright.sync_api import Dialog, Page, expect, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from visual_editor_network import create_guarded_context, default_dist_dir


class PackUI:
    """Only DOM/user interactions; no imports, React/store hooks or fake UI."""

    def __init__(self, page: Page, fixture: PackFixture, output: Path) -> None:
        self.page, self.fixture, self.output = page, fixture, output
        self.checks: list[str] = []
        self.dialogs: list[dict[str, str]] = []
        self.dialog_decision: bool | None = None
        page.on("dialog", self.handle_dialog)

    def handle_dialog(self, dialog: Dialog) -> None:
        self.dialogs.append(
            {
                "type": dialog.type,
                "message": dialog.message,
                "decision": str(self.dialog_decision),
            }
        )
        if self.dialog_decision is None:
            dialog.dismiss()
            return  # Record unexpected prompts; assert after browser events settle.
        decision, self.dialog_decision = self.dialog_decision, None
        dialog.accept() if decision else dialog.dismiss()

    def check(self, label: str, condition: bool = True) -> None:
        self.fixture.assert_clean()
        if not condition:
            raise AssertionError(label)
        self.checks.append(label)

    def screenshot(self, name: str) -> None:
        self.page.screenshot(path=str(self.output / f"{name}.png"), full_page=True)

    def ready(self, workflow_id: str) -> None:
        expect(
            self.page.get_by_role(
                "button",
                name=f"切换工作流，当前：{spec(workflow_id)['title']}",
                exact=True,
            )
        ).to_be_visible()
        expect(self.page).to_have_url(
            f"{self.fixture.base_url}/workflows/workspace?workflowId={workflow_id}&tab=editor"
        )
        expect(
            self.page.get_by_text(
                "画布与文件共同保存为完整 Pack 版本，保存到 saved 后再确认部署。",
                exact=True,
            )
        ).to_be_visible()

    def open(self, workflow_id: str = B) -> None:
        self.page.goto(
            f"{self.fixture.base_url}/workflows/workspace?workflowId={workflow_id}&tab=editor"
        )
        self.ready(workflow_id)

    def select(self, workflow_id: str) -> None:
        self.page.get_by_role("button", name="切换工作流，当前：", exact=False).click()
        self.page.get_by_role("dialog", name="选择工作流").get_by_role(
            "button", name=spec(workflow_id)["title"], exact=False
        ).click()
        self.ready(workflow_id)

    def scripts(self) -> None:
        self.page.get_by_role("tab", name="脚本文件", exact=True).click()
        expect(
            self.page.get_by_role("region", name="版本文件编辑器", exact=True)
        ).to_be_visible()

    def edit_script(self, text: str = EDITED_SCRIPT) -> None:
        self.page.get_by_role("button", name=SCRIPT, exact=True).click()
        self.page.get_by_role("textbox", name="文件内容", exact=True).fill(text)
        expect(
            self.page.get_by_role("textbox", name="文件内容", exact=True)
        ).to_have_value(text)

    def prepare_save(self) -> None:
        self.open()
        self.select(A)
        self.scripts()
        listing = self.page.get_by_role("list", name="版本文件列表")
        expect(listing.get_by_role("button")).to_have_text(
            [entry["path"] for entry in self.fixture.files[A]]
        )
        self.edit_script()
        expect(
            self.page.get_by_role("combobox", name="所选文件模式", exact=True)
        ).to_have_value("100755")
        self.fixture.expected_changes = [
            {"operation": "replace", **file(SCRIPT, EDITED_SCRIPT, "100755")}
        ]
        self.check(
            "workflow picker opens complete real React file inventory; script edit retains executable mode"
        )

    def save(self) -> None:
        with self.fixture.allow_write("PUT", self.fixture.save_path):
            with self.page.expect_request(
                lambda request: (
                    request.method == "PUT" and request.url.endswith("/pack-version")
                )
            ):
                self.page.get_by_role("button", name="保存到草稿", exact=True).click()
            # Wait for the actual response-driven state, not merely request dispatch.
            if self.fixture.save_mode == "conflict":
                expect(
                    self.page.get_by_role("alert")
                    .filter(has_text="保存冲突：远端版本已变化")
                    .first
                ).to_be_visible()
            elif self.fixture.save_mode == "lost":
                expect(
                    self.page.get_by_role("region", name="保存结果对账")
                ).to_be_visible()
            else:
                self.saved()

    def saved(self) -> None:
        expect(
            self.page.get_by_role("status").filter(
                has_text=f"已保存到草稿 commit {SAVED_COMMIT}，尚未部署。"
            )
        ).to_be_visible()
        expect(
            self.page.get_by_role("button", name="草稿版（saved）", exact=True)
        ).to_have_attribute("aria-pressed", "true")
        expect(
            self.page.get_by_role("button", name="保存到草稿", exact=True)
        ).to_be_disabled()

    def blob_transport(self, path: str) -> None:
        observed = self.page.evaluate("window.__packBrowserFetch")
        writes = [
            call
            for call in observed
            if call["path"] == path and call["method"] in {"PUT", "POST"}
        ]
        self.check(
            "native fetch uses application/json Blob body for " + path,
            bool(writes)
            and all(
                call["blob"] and call["type"] == "application/json" for call in writes
            ),
        )

    def no_deploy(self) -> None:
        self.check(
            "no deploy POST before explicit confirmation",
            not any(
                call["path"] == "/api/auto-heal/deploy" for call in self.fixture.writes
            ),
        )

    def keep_drafts(self) -> tuple[str, str]:
        self.page.get_by_role("tab", name="工作流编排", exact=True).click()
        self.page.locator('.react-flow__node[data-id="script"] h3').click()
        self.page.get_by_label("节点名称", exact=True).fill("保留的画布名称")
        self.page.get_by_role("button", name="YAML", exact=True).click()
        yaml_input = self.page.get_by_label("工作流 YAML", exact=True)
        draft = yaml_input.input_value() + "\n# unapplied browser navigation draft\n"
        yaml_input.fill(draft)
        self.scripts()
        text = 'print("navigation draft survives")\n'
        self.edit_script(text)
        return draft, text

    def assert_drafts(self, drafts: tuple[str, str]) -> None:
        self.page.get_by_role("button", name=SCRIPT, exact=True).click()
        expect(
            self.page.get_by_role("textbox", name="文件内容", exact=True)
        ).to_have_value(drafts[1])
        self.page.get_by_role("tab", name="工作流编排", exact=True).click()
        expect(self.page.get_by_label("工作流 YAML", exact=True)).to_have_value(
            drafts[0]
        )
        self.page.get_by_role("button", name="画布", exact=True).click()
        expect(
            self.page.locator('.react-flow__node[data-id="script"] h3')
        ).to_have_text("保留的画布名称")
        self.scripts()


def save_binary_deploy(ui: PackUI) -> None:
    ui.prepare_save()
    ui.page.get_by_label("新文件相对路径", exact=True).fill("assets/upload.bin")
    ui.page.get_by_role("button", name="添加空文件", exact=True).click()
    ui.page.get_by_label("上传文件替换所选文件", exact=True).set_input_files(
        {
            "name": "fixture.bin",
            "mimeType": "application/octet-stream",
            "buffer": BINARY,
        }
    )
    expect(
        ui.page.get_by_text("此文件不是可安全编辑的 UTF-8 文本", exact=False)
    ).to_be_visible()
    expect(ui.page.get_by_role("button", name="保存到草稿", exact=True)).to_be_enabled()
    ui.fixture.expected_changes += [
        {"operation": "create", **file("assets/upload.bin", BINARY)}
    ]
    ui.screenshot("binary-edited")
    ui.save()
    body = ui.fixture.writes[0]
    ui.check(
        "PUT fixed sourceVersion/sourceCommit/expectedSavedHead + requestId + CSRF, >14KiB JSON without gzip",
        body["bytes"] > 14 * 1024
        and body["headers"]["content-type"] == "application/json",
    )
    unchanged = {
        entry["path"]: entry for entry in ui.fixture.files[A] if entry["path"] != SCRIPT
    }
    actual = {entry["path"]: entry for entry in ui.fixture.draft_files[A]}
    ui.check(
        "only explicit script/binary changes submitted; untouched fixture bytes/modes preserved",
        all(actual[path] == value for path, value in unchanged.items())
        and ui.fixture.applied_saves == 1,
    )
    ui.blob_transport(ui.fixture.save_path)
    ui.no_deploy()
    ui.page.get_by_role("button", name="部署工作流", exact=True).click()
    expect(ui.page.get_by_role("button", name="确认部署", exact=True)).to_be_visible()
    expect(
        ui.page.get_by_text(f"草稿 commit：{SAVED_COMMIT}", exact=False)
    ).to_be_visible()
    ui.no_deploy()
    ui.screenshot("deploy-confirmation")
    # Advance remote fixture only AFTER the user has seen candidate S.
    ui.fixture.advance_draft()
    with ui.fixture.allow_write("POST", "/api/auto-heal/deploy"):
        ui.page.get_by_role("button", name="确认部署", exact=True).click()
        expect(
            ui.page.get_by_text(f"部署成功：deploy #1 · v2 · deploy/{A}/#1", exact=True)
        ).to_be_visible()
    ui.check(
        "confirmed deploy POST pins returned saved S even after mock saved head advances to S2",
        ui.fixture.writes[-1]["body"]["candidateCommit"] == SAVED_COMMIT
        and ui.fixture.draft_head == LATER_COMMIT,
    )
    ui.blob_transport("/api/auto-heal/deploy")
    ui.screenshot("deployed")


def conflict_keeps_draft(ui: PackUI) -> None:
    ui.fixture.save_mode = "conflict"
    ui.prepare_save()
    ui.save()
    expect(ui.page.get_by_role("textbox", name="文件内容", exact=True)).to_have_value(
        EDITED_SCRIPT
    )
    expect(
        ui.page.get_by_role("button", name="保存到草稿", exact=True)
    ).to_be_disabled()
    ui.page.get_by_role("button", name="部署工作流", exact=True).click()
    expect(
        ui.page.get_by_text(
            "版本文件存在未保存更改或保存状态不确定，请先处理后再部署。", exact=True
        )
    ).to_be_visible()
    ui.no_deploy()
    ui.dialog_decision = False
    ui.page.get_by_role("button", name="重新读取", exact=True).click()
    expect(ui.page.get_by_role("textbox", name="文件内容", exact=True)).to_have_value(
        EDITED_SCRIPT
    )
    ui.page.get_by_role("tab", name="工作流编排", exact=True).click()
    ui.scripts()
    ui.page.get_by_role("button", name=SCRIPT, exact=True).click()
    expect(ui.page.get_by_role("textbox", name="文件内容", exact=True)).to_have_value(
        EDITED_SCRIPT
    )
    ui.check(
        "409 blocks save/deploy and preserves draft across cancelled reload and tab remount",
        ui.fixture.applied_saves == 0
        and len(ui.fixture.writes) == 1
        and len(ui.dialogs) == 1,
    )
    ui.screenshot("conflict-retained")


def lost_response(ui: PackUI, *, recover: bool) -> None:
    ui.fixture.save_mode = "lost"
    ui.prepare_save()
    ui.save()
    original = copy.deepcopy(ui.fixture.attempt)
    ui.page.get_by_role("button", name=SCRIPT, exact=True).click()
    expect(ui.page.get_by_role("textbox", name="文件内容", exact=True)).to_have_value(
        EDITED_SCRIPT
    )
    expect(
        ui.page.get_by_role("textbox", name="文件内容", exact=True)
    ).to_have_attribute("readonly", "")
    expect(
        ui.page.get_by_role("button", name="保存到草稿", exact=True)
    ).to_be_disabled()
    with ui.page.expect_download() as download:
        ui.page.get_by_role(
            "button", name="下载原始尝试候选（JSON）", exact=True
        ).click()
    exported = ui.output / "original-candidate.json"
    download.value.save_as(str(exported))
    candidate = json.loads(exported.read_text())
    expected_files = [
        file(SCRIPT, EDITED_SCRIPT, "100755") if item["path"] == SCRIPT else item
        for item in ui.fixture.files[A]
    ]
    ui.check(
        "lost response freezes/exports exact original candidate, baseline and requestId",
        candidate["files"] == expected_files
        and candidate["sourceCommit"] == BASE_COMMIT
        and candidate["expectedSavedHead"] == BASE_COMMIT
        and candidate["requestId"] == original["requestId"],
    )
    ui.fixture.advance_draft()
    ui.page.get_by_role("button", name="查看远端草稿 HEAD", exact=True).click()
    expect(
        ui.page.get_by_text(f"读取时的远端草稿 HEAD：{LATER_COMMIT}", exact=False)
    ).to_be_visible()
    expect(ui.page.get_by_role("region", name="保存结果对账")).to_be_visible()
    ui.page.get_by_role("button", name="查询保存操作", exact=True).click()
    expect(
        ui.page.get_by_text(
            "保存操作状态：unknown。只有 completed 结果才能确认保存。", exact=True
        )
    ).to_be_visible()
    ui.check(
        "observing S2 HEAD / unknown GET cannot falsely confirm S or mutate its candidate",
        len(ui.fixture.writes) == 1 and ui.fixture.operation_state == "unknown",
    )
    ui.no_deploy()
    ui.screenshot("unknown-retained")
    path = (
        f"/api/workflows/{A}/pack-operations/{original['requestId']}/reconcile"
        if recover
        else ui.fixture.save_path
    )
    with ui.fixture.allow_write("POST" if recover else "PUT", path):
        ui.page.get_by_role(
            "button",
            name="恢复保存操作" if recover else "重试原始保存（同一 requestId）",
            exact=True,
        ).click()
        ui.saved()
    expect(ui.page.get_by_role("region", name="保存结果对账")).to_have_count(0)
    ui.page.get_by_role("button", name=SCRIPT, exact=True).click()
    expect(ui.page.get_by_role("textbox", name="文件内容", exact=True)).to_have_value(
        EDITED_SCRIPT
    )
    ui.check(
        "explicit recovery confirms original S"
        if recover
        else "same-requestId retry sends byte-identical original PUT, not S2 baseline",
        ui.fixture.applied_saves == 1
        and ui.fixture.attempt == original
        and ui.fixture.draft_head == LATER_COMMIT,
    )
    ui.check(
        "recovery uses CSRF POST without another PUT"
        if recover
        else "retry body equals first PUT",
        (ui.fixture.writes[-1]["body"] is None)
        if recover
        else ui.fixture.writes[-1]["body"] == original,
    )
    ui.screenshot("recovered" if recover else "retried")


def history_identity(page: Page) -> dict:
    return page.evaluate(
        "({href: location.href, index: history.state.idx, key: history.state.key, length: history.length})"
    )


def back_forward_clean(ui: PackUI) -> None:
    ui.open()
    b = history_identity(ui.page)
    ui.select(A)
    a = history_identity(ui.page)
    ui.page.evaluate("history.back()")
    ui.ready(B)
    ui.check(
        "native Back commits prior workflow without replacing entry",
        history_identity(ui.page) == {**b, "length": a["length"]},
    )
    ui.page.evaluate("history.forward()")
    ui.ready(A)
    ui.check(
        "native Forward commits next workflow without replacing entry",
        history_identity(ui.page) == a,
    )
    ui.check(
        "clean history makes no writes or discard dialogs",
        not ui.fixture.writes and not ui.dialogs,
    )
    ui.screenshot("back-forward")


def default_workflow_history_draft(ui: PackUI) -> None:
    ui.open(A)
    ui.page.evaluate("""() => {
      const source = history.state;
      history.replaceState({...source, idx: 0, key: 'explicit'}, '', '?workflowId=%s&tab=editor');
      history.pushState({...source, idx: 1, key: 'default'}, '', '?tab=editor');
    }""" % A)
    ui.page.reload()
    expect(ui.page.get_by_role("button", name=f"切换工作流，当前：{spec(A)['title']}", exact=True)).to_be_visible()
    drafts = ui.keep_drafts()
    ui.page.evaluate("history.back()")
    ui.page.wait_for_function("location.search.includes('workflowId=') && history.state.key === 'explicit'")
    ui.assert_drafts(drafts)
    ui.page.evaluate("history.forward()")
    ui.page.wait_for_function("!location.search.includes('workflowId=') && history.state.key === 'default'")
    ui.assert_drafts(drafts)
    ui.check("default and explicit same-workflow history preserves drafts without prompt", not ui.dialogs and not ui.fixture.writes)


def same_workflow_upload_history(ui: PackUI, *, forward: bool) -> None:
    ui.open(A)
    # Make both history entries without mounting the networked overview tab.
    # The actual POP still travels through Chromium and the real Router.
    if forward:
        ui.page.evaluate("""() => {
          history.replaceState({idx: 0, key: 'upload-source'}, '', '?workflowId=%s&tab=editor');
          history.pushState({idx: 1, key: 'upload-target'}, '', '?workflowId=%s&tab=overview');
        }""" % (A, A))
        ui.page.evaluate("history.back()")
        ui.page.wait_for_function("history.state.key === 'upload-source'")
    else:
        ui.page.evaluate("""() => {
          history.replaceState({idx: 0, key: 'upload-target'}, '', '?workflowId=%s&tab=overview');
          history.pushState({idx: 1, key: 'upload-source'}, '', '?workflowId=%s&tab=editor');
        }""" % (A, A))
    ui.page.reload()
    ui.ready(A)
    ui.scripts()
    ui.page.get_by_role("button", name=SCRIPT, exact=True).click()
    ui.page.evaluate("""() => {
      const read = FileReader.prototype.readAsArrayBuffer;
      FileReader.prototype.readAsArrayBuffer = function(file) {
        const reader = this;
        window.__releasePackFileRead = () => read.call(reader, file);
      };
    }""")
    ui.page.get_by_label("上传文件替换所选文件", exact=True).set_input_files(
        {"name": "pending.txt", "mimeType": "text/plain", "buffer": b"pending upload contents"}
    )
    expect(ui.page.get_by_text("正在读取上传文件，请等待完成后再保存或切换。", exact=True)).to_be_visible()
    source = history_identity(ui.page)
    ui.dialog_decision = True  # Expected upload-block alert, not an unexpected prompt.
    with ui.page.expect_event("dialog"):
        ui.page.evaluate("history.forward()" if forward else "history.back()")
    ui.page.wait_for_function(
        "expected => location.href === expected.href && history.state.idx === expected.index && history.state.key === expected.key",
        arg=source,
    )
    expect(ui.page.get_by_text("正在读取上传文件，请等待完成后再保存或切换。", exact=True)).to_be_visible()
    ui.check("same-workflow POP blocks once while preserving the active file read",
             len(ui.dialogs) == 1 and ui.dialogs[0]["type"] == "alert" and not ui.fixture.writes)
    ui.page.evaluate("window.__releasePackFileRead()")
    expect(ui.page.get_by_role("textbox", name="文件内容", exact=True)).to_have_value("pending upload contents")
    expect(ui.page.get_by_text("正在读取上传文件，请等待完成后再保存或切换。", exact=True)).to_have_count(0)
    ui.screenshot("upload-retained")


def history_draft_guard(ui: PackUI, *, forward: bool, fail: bool) -> None:
    ui.open()
    ui.select(A)
    source, target = A, B
    if forward:
        ui.page.evaluate("history.back()")
        ui.ready(B)
        source, target = B, A
    drafts = ui.keep_drafts()
    source_entry = history_identity(ui.page)
    before_reads = sum(
        call == {"method": "GET", "path": f"/api/workflows/{target}"}
        for call in ui.fixture.calls
    )
    ui.fixture.fail_workflow = target if fail else None
    ui.dialog_decision = fail
    response = (
        ui.page.expect_response(
            lambda response: (
                response.url == f"{ui.fixture.base_url}/api/workflows/{target}"
                and response.status == 503
            )
        )
        if fail
        else nullcontext()
    )
    with response, ui.page.expect_event("dialog"):
        ui.page.evaluate("history.forward()" if forward else "history.back()")
    # The native POP briefly changes URL; require restoration AND dialog evidence.
    ui.page.wait_for_function(
        "expected => location.href === expected.href && history.state.idx === expected.index && history.state.key === expected.key",
        arg=source_entry,
    )
    ui.ready(source)
    ui.assert_drafts(drafts)
    after_reads = sum(
        call == {"method": "GET", "path": f"/api/workflows/{target}"}
        for call in ui.fixture.calls
    )
    ui.check(
        "Back/Forward cancel avoids target read"
        if not fail
        else "accepted Back/Forward target read actually fails",
        after_reads == before_reads + (1 if fail else 0),
    )
    ui.check(
        "original URL/history entry, canvas, unapplied YAML and Pack draft survive",
        history_identity(ui.page) == source_entry and not ui.fixture.writes,
    )
    ui.screenshot("drafts-retained")
    if fail:
        expect(
            ui.page.get_by_role("alert").filter(has_text="切换未完成，原草稿仍保留")
        ).to_be_visible()
    ui.check(
        "exactly one native discard confirmation (no second guard prompt)",
        len(ui.dialogs) == 1,
    )
    ui.screenshot("load-failure-protected" if fail else "cancel-protected")


SCENARIOS: dict[str, Callable[[PackUI], None]] = {
    "save-binary-deploy": save_binary_deploy,
    "409-keeps-draft": conflict_keeps_draft,
    "lost-response-retry": lambda ui: lost_response(ui, recover=False),
    "lost-response-reconcile": lambda ui: lost_response(ui, recover=True),
    "back-forward-clean": back_forward_clean,
    "default-workflow-history-draft": default_workflow_history_draft,
    "same-workflow-upload-back": lambda ui: same_workflow_upload_history(ui, forward=False),
    "same-workflow-upload-forward": lambda ui: same_workflow_upload_history(ui, forward=True),
    "back-cancel": lambda ui: history_draft_guard(ui, forward=False, fail=False),
    "forward-cancel": lambda ui: history_draft_guard(ui, forward=True, fail=False),
    "back-load-failure": lambda ui: history_draft_guard(ui, forward=False, fail=True),
    "forward-load-failure": lambda ui: history_draft_guard(ui, forward=True, fail=True),
}


def write_reports(output: Path, report: dict) -> None:
    results = report["results"]
    report["passed"] = sum(row["passed"] for row in results)
    report["failed"] = len(results) - report["passed"]
    report["success"] = (
        bool(results) and report["failed"] == 0 and not report.get("fatal_error")
    )
    (output / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    suite = ET.Element(
        "testsuite",
        name="offline-pack-version-chromium",
        tests=str(len(results)),
        failures=str(report["failed"]),
    )
    rows = []
    for row in results:
        case = ET.SubElement(
            suite, "testcase", name=row["name"], time=str(row["seconds"])
        )
        if not row["passed"]:
            ET.SubElement(case, "failure").text = row["error"]
        artifact = row["name"]
        ET.SubElement(case, "system-out").text = (
            f"Artifacts: {output / artifact}\n" + "\n".join(row["checks"])
        )
        links = " ".join(
            f'<a href="{artifact}/{path.name}">{path.name}</a>'
            for path in (output / artifact).glob("*.png")
        )
        rows.append(
            f"<tr><td>{html.escape(artifact)}</td><td>{'PASS' if row['passed'] else 'FAIL'}</td><td>{links}<pre>{html.escape(row.get('error', ''))}</pre></td></tr>"
        )
    ET.ElementTree(suite).write(
        output / "junit.xml", encoding="utf-8", xml_declaration=True
    )
    (output / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Pack browser results</title><h1>Offline real Chromium UI contracts</h1>'
        f"<p>{report['passed']} passed / {len(results)} executed. Fixture only: no real Git, SQL, SSO or deployment.</p><table>"
        + "".join(rows)
        + "</table>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, choices=range(1, 6), default=3)
    parser.add_argument(
        "--scenario",
        action="append",
        choices=SCENARIOS,
        help="Focused diagnosis; default runs all scenarios",
    )
    args = parser.parse_args()
    output = Path(tempfile.mkdtemp(prefix="pack-browser-", dir="/tmp"))
    print(f"ARTIFACTS {output}", flush=True)
    report = {
        "results": [],
        "limitations": [
            "Only real React/Chromium UI and mocked API wire contracts; not Git/SQLite/permissions/SSO verification.",
            "No PRE/PROD, backend, actual deployment or external network. Synthetic in-memory operation receipts.",
            "Memory drafts checked within the same page only; no reload/restart persistence claim.",
            "Desktop Chromium only; screenshots and traces retained, video not recorded.",
        ],
    }
    app = default_dist_dir().parents[1]
    dist = output / "dist"
    started = time.monotonic()
    try:
        # Published workspace exports point at module dist/. Avoid silently testing
        # old compiled UI: temporary build-only aliases compile the actual sources.
        public = app.parents[1]
        aliases = []
        for package, directory in [
            ("workflow", public / "modules/workflow/web"),
            ("clawweb-shared", public / "shared/web"),
            ("clawevolve", public / "modules/clawevolve/web/src"),
            ("clawinsight", public / "modules/clawinsight/web"),
        ]:
            if (directory / "index.ts").is_file():
                aliases.append(
                    f"{{find: /^@avernet\\/{package}\\/web$/, replacement: {json.dumps(str(directory / 'index.ts'))}}}"
                )
            aliases.append(
                f"{{find: /^@avernet\\/{package}\\/web\\//, replacement: {json.dumps(str(directory) + '/')}}}"
            )
        aliases.append(
            "{find: '@avernet/clawweb-shared/theme.css', replacement: "
            + json.dumps(str(public / "shared/web/theme.css"))
            + "}"
        )
        diagnostic_build = os.environ.get("PACK_BROWSER_DIAGNOSTICS") == "1"
        config = output / "vite.source.config.mjs"
        config.write_text(
            f"import original from {json.dumps(str(app / 'vite.config.ts'))};\n"
            + "export default {...original, build: {...original.build, sourcemap: "
            + ("true" if diagnostic_build else "false")
            + "}, resolve: {...original.resolve, alias: ["
            + ",".join(aliases)
            + "]}};\n"
        )
        source_files = [
            path
            for root in (
                public / "modules/workflow/web",
                public / "shared/web",
                app / "web",
            )
            for path in root.rglob("*")
            if path.is_file() and "__tests__" not in path.parts
        ]
        source_hashes = {
            str(path.relative_to(public)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(source_files)
        }
        (output / "source-sha256.json").write_text(
            json.dumps(source_hashes, indent=2) + "\n"
        )
        report["build_mode"] = (
            "public app entrypoint + temporary local source aliases; not published-package evidence"
            + (
                "; temporary sourcemaps enabled for stack diagnosis"
                if diagnostic_build
                else ""
            )
        )
        # Installed executable only: cannot trigger npx/npm registry fetching.
        with (output / "build.log").open("w") as log:
            subprocess.run(
                [
                    str(app / "node_modules/.bin/vite"),
                    "build",
                    "--config",
                    str(config),
                    "--outDir",
                    str(dist),
                    "--emptyOutDir",
                ],
                cwd=app,
                env={**os.environ, "npm_config_offline": "true"},
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=120,
                check=True,
            )
        if any(
            hashlib.sha256((public / path).read_bytes()).hexdigest() != digest
            for path, digest in source_hashes.items()
        ):
            raise AssertionError(
                "UI sources changed during build; rerun for a stable source snapshot"
            )
        report["fresh_build"] = {
            "directory": str(dist),
            "sha256": {
                str(path.relative_to(dist)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted(dist.rglob("*"))
                if path.is_file()
            },
        }
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-sync",
                ],
            )
            report["chromium_version"] = browser.version
            try:
                for repeat in range(1, args.repeat + 1):
                    for scenario in args.scenario or SCENARIOS:
                        name = f"run-{repeat}-{scenario}"
                        artifacts = output / name
                        artifacts.mkdir()
                        fixture = PackFixture(dist)
                        context = create_guarded_context(browser, fixture)
                        context.set_default_timeout(8000)
                        errors: list[str] = []
                        context.on(
                            "page",
                            lambda page, errors=errors: page.on(
                                "pageerror", lambda error: errors.append(str(error))
                            ),
                        )
                        context.add_init_script("""
                            window.__TERN__ = {user: {outUserNo: 'browser-user', nickName: 'Browser fixture'}};
                            window.__packBrowserDiagnostics = {confirms: [], popstates: []};
                            const nativeConfirm = window.confirm.bind(window);
                            window.confirm = function(message) {
                                const error = new Error('confirm instrumentation');
                                window.__packBrowserDiagnostics.confirms.push({
                                    time: performance.now(), message: String(message),
                                    href: location.href, state: history.state,
                                    stack: error.stack,
                                });
                                return nativeConfirm(message);
                            };
                            window.addEventListener('popstate', function(event) {
                                window.__packBrowserDiagnostics.popstates.push({
                                    time: performance.now(), href: location.href,
                                    state: event.state, cancelBubble: event.cancelBubble,
                                });
                            }, true);
                            // Passive type observation only: forward the real fetch unchanged.
                            window.__packBrowserFetch = [];
                            const nativeFetch = window.fetch;
                            window.fetch = function(input, init) {
                                const body = init?.body;
                                window.__packBrowserFetch.push({
                                    path: new URL(typeof input === 'string' ? input : input.url, location.href).pathname,
                                    method: init?.method ?? 'GET', blob: body instanceof Blob,
                                    type: body instanceof Blob ? body.type : typeof body,
                                    size: body instanceof Blob ? body.size : null,
                                });
                                return Reflect.apply(nativeFetch, this, arguments);
                            };
                        """)
                        context.tracing.start(
                            screenshots=True, snapshots=True, sources=True
                        )
                        page = context.new_page()
                        ui = PackUI(page, fixture, artifacts)
                        row = {"name": name, "passed": False, "checks": ui.checks}
                        began = time.monotonic()
                        try:
                            SCENARIOS[scenario](ui)
                            fixture.assert_clean()
                            if any(
                                dialog["decision"] == "None" for dialog in ui.dialogs
                            ):
                                raise AssertionError(
                                    f"Unexpected native dialog(s): {ui.dialogs}"
                                )
                            if errors:
                                raise AssertionError(
                                    f"Uncaught browser errors: {errors}"
                                )
                            ui.check(
                                "no uncaught JS errors, external requests, forbidden APIs or writes outside explicit UI actions"
                            )
                            row["passed"] = True
                        except (AssertionError, PlaywrightError):
                            row["error"] = traceback.format_exc()
                            ui.screenshot("failure")
                            (artifacts / "dom.txt").write_text(
                                page.locator("body").inner_text()
                            )
                        finally:
                            row["fetch_observations"] = page.evaluate(
                                "window.__packBrowserFetch"
                            )
                            row["navigation_diagnostics"] = page.evaluate(
                                "window.__packBrowserDiagnostics"
                            )
                            row.update(
                                seconds=round(time.monotonic() - began, 3),
                                api_calls=fixture.calls,
                                writes=fixture.writes,
                                responses=fixture.responses,
                                dialogs=ui.dialogs,
                                external=fixture.external,
                                network_violations=fixture.violations,
                                page_errors=errors,
                            )
                            context.tracing.stop(path=str(artifacts / "trace.zip"))
                            context.close()
                            report["results"].append(row)
                            write_reports(output, report)
                            print(
                                f"{'PASS' if row['passed'] else 'FAIL'} {name} ({row['seconds']}s)",
                                flush=True,
                            )
            finally:
                browser.close()
    except Exception:
        report["fatal_error"] = traceback.format_exc()
        raise
    finally:
        report["seconds"] = round(time.monotonic() - started, 3)
        write_reports(output, report)
        print(
            f"RESULT {report['passed']}/{len(report['results'])} passed; {output}/index.html",
            flush=True,
        )
    if not report["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
