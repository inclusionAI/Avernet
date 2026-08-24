#!/usr/bin/env python3
"""从 5 段真源再生成 task-loop skill 包(SKILL.md + references/ + README.md)。
段体:剥各自 frontmatter 取正文,标题统一降一级(H1->H2 ...)并入;正文文本与 HR 零改。
"""
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
FEAT = HERE
PKG = os.path.join(FEAT, "task-loop")
REFS = os.path.join(PKG, "references")

SRC = {
    "recognition": "/Users/shangjian.msj/Desktop/task-recognition/SKILL.md",
    "planning": "/Users/shangjian.msj/Github/Avernet/src/backend/tests/community/core/task/singlebox_e2e/skills/planning/SKILL.md",
    "search": "/Users/shangjian.msj/Github/Avernet/src/backend/tests/community/core/task/singlebox_e2e/skills/search/SKILL.md",
    "acceptance": "/Users/shangjian.msj/Github/Avernet/src/backend/tests/community/core/task/singlebox_e2e/skills/acceptance/SKILL.md",
    "bbs": "/Users/shangjian.msj/Github/Avernet/src/backend/specs/2026-08-09-task-goal-driven-bbs-active-relay/bbs-relay-single-task/SKILL.md",
}
BBSREF = "/Users/shangjian.msj/Github/Avernet/src/backend/specs/2026-08-09-task-goal-driven-bbs-active-relay/bbs-relay-single-task/references"
RECODIR = "/Users/shangjian.msj/Desktop/task-recognition"

SEG_MARKER = {
    "recognition": "> 段1 · 任务识别(recognition;触发 /task 或 [RESUME_TASK] 或仅副屏标签)",
    "planning": "> 段2 · 任务规划(planning;触发 框架 [planning])",
    "search": "> 段3 · 任务派发搜推(search;触发 框架 [search])",
    "acceptance": "> 段4 · 任务验收(acceptance;worker 叶子自验收)",
    "bbs": "> 段5 · BBS 接力(bbs-relay-single-task;触发 引擎 BBS 通知;参考文档见 references/)",
}
ORDER = ["recognition", "planning", "search", "acceptance", "bbs"]


def strip_frontmatter(text):
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[i + 1:]).lstrip("\n")
    return text


def demote_one(text):
    out, fence = [], None
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):
            f = s[:3]
            fence = None if fence == f else f
            out.append(line)
            continue
        if fence is None and re.match(r"^#{1,5} ", line):
            out.append("#" + line)
        else:
            out.append(line)
    return "".join(out)


def body(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    return demote_one(strip_frontmatter(raw)).strip("\n")


HEADER = """---
name: task-loop
description: |
  任务目标驱动执行闭环预装 skill。整合任务识别 / 规划 / 派发搜推 / 验收 / BBS 接力五段为单一 skill,
  预装到所有 bot 即等同各段单独安装到对应 bot;每段按各自触发词自门控,仅命中段执行。
  触发: 用户面 /task 或 [RESUME_TASK] 或仅副屏标签 -> 任务识别; 框架 [planning] -> 规划;
  框架 [search] -> 派发搜推; worker 叶子执行后自验收 -> 验收; 引擎 BBS 通知 -> BBS 接力。
version: 1.0.0
author: avernet-task-framework
tags: [task, loop, orchestrate, task-recognition, task-planning, task-search, task-acceptance, bbs-relay]
---

# task-loop

预装到所有 bot 的任务目标驱动执行闭环 skill。本 skill 内含五段,**只执行被触发词命中的那一段**,其余段不参与;给任一 bot 预装本 skill 等同把对应段单独安装到该 bot。

- 任务识别(recognition):对话 bot 用户面——`/task` 或平台 `[RESUME_TASK]` 回传或仅副屏标签
- 任务规划(planning):owner bot——框架 prompt 头部 `[planning]`
- 任务派发搜推(search):owner bot——框架 prompt 头部 `[search]`
- 任务验收(acceptance):worker bot——叶子执行后自验收
- BBS 接力(bbs-relay-single-task):中继 bot——引擎主动通知

段体取自各段真源 SKILL.md,仅标题层级统一降一级并入;各段逻辑 / 规则 / 卡片格式 / 触发与输出契约保持原样。先读下面"路由规则"确定本该执行哪段,再只跑那一段。

## 路由规则(最先读,只跑命中段)

按**本次收到的触发词 / 上下文**分流(不按 bot 身份;同一 bot 按其收到的触发执行对应段):

| 触发 / 上下文 | 命中段 | 执行要点 |
|---|---|---|
| 用户消息以 `/task` 开头;或上下文含 `[RESUME_TASK]`;或消息仅 `<AixUI type="panel" component="task-loop" ...>` 副屏标签 | 段1 任务识别 | 出 AixUI 卡片(cardId 固定 card_3e31e1f1),到 task_ready 为止;执行由平台层调 POST /api/v1/collaboration/tasks/execute |
| prompt 头部标记 `[planning]`,含目标节点 node_id 与任务态快照 | 段2 任务规划 | 返回 JSON 对象 {tasks: List[TaskSpec], has_gap, gap_detail};tasks 为空即 gap 闭=验收通过 |
| prompt 头部标记 `[search]`,含子任务需求与候选集 catalog | 段3 任务派发搜推 | 返回 4 态 JSON(HIT_SINGLE / HIT_GROUP / HIT_MULTI_BOTS / MISS) |
| 你是 worker bot,刚执行完叶子子任务,需按其 goal.acceptances 自验收(已收到 goal / instruction / sibling_outputs / execute_output) | 段4 任务验收 | 折叠进回投 result={success, data, gaps};不独立回投 |
| 收到引擎主动发的 BBS 任务消息(含 task_id + 后端 base url + 自身 bot_id,且引擎已替你占根) | 段5 BBS 接力 | 跳过 scan / claim / 自判,直接 attach -> 执行 -> result |

**优先级**(实际多互斥,显式定义避免歧义):段5(引擎 BBS 通知) > 段2/段3(框架 `[planning]` / `[search]` 头) > 段4(worker 叶子自验收) > 段1(`/task` / `[RESUME_TASK]` / 副屏标签)。

**未命中任何段**:静默结束本轮——不虚构任务、不追问、不输出卡片、不乱执行(相当于 return / no-op)。反例:`[planning]` / `[search]` 是框架执行期 prompt,不是用户任务提交,不要走段1。

**只跑命中段**:命中某段即按该段全部规则执行,其余段规则不参与。

"""

def main():
    os.makedirs(REFS, exist_ok=True)
    parts = [HEADER]
    for k in ORDER:
        parts.append(SEG_MARKER[k] + "\n\n")
        parts.append(body(SRC[k]) + "\n\n")
    skillmd = "".join(parts).rstrip("\n") + "\n"
    with open(os.path.join(PKG, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(skillmd)

    for src, dst in [
        (os.path.join(BBSREF, "task-api.md"), os.path.join(REFS, "bbs-task-api.md")),
        (os.path.join(BBSREF, "judge-rubric.md"), os.path.join(REFS, "bbs-judge-rubric.md")),
        (os.path.join(BBSREF, "idempotency.md"), os.path.join(REFS, "bbs-idempotency.md")),
        (os.path.join(RECODIR, "卡片数据格式.md"), os.path.join(REFS, "recognition-card-format.md")),
        (os.path.join(RECODIR, "平台层接口协议.md"), os.path.join(REFS, "recognition-platform-protocol.md")),
    ]:
        shutil.copyfile(src, dst)

    README = """# task-loop 预装 skill 包

    任务目标驱动执行闭环的单一预装 skill,整合 5 段(识别 / 规划 / 派发搜推 / 验收 / BBS 接力),
    预装到所有 bot 即等同各段单独安装到对应 bot;每段按各自触发词自门控,仅命中段执行。

    ## 结构

    ```
    task-loop/
    ├── SKILL.md          # frontmatter + # task-loop + 路由规则 + 段1..段5
    ├── references/
    │   ├── bbs-task-api.md
    │   ├── bbs-judge-rubric.md
    │   ├── bbs-idempotency.md
    │   ├── recognition-card-format.md
    │   └── recognition-platform-protocol.md
    └── README.md
    ```

    ## 触发与段对照

    | 触发 | 段 | 来源真源 |
    |---|---|---|
    | /task 或 [RESUME_TASK] 或仅副屏标签 | 段1 任务识别 | ~/Desktop/task-recognition/SKILL.md |
    | 框架 [planning] | 段2 任务规划 | Avernet singlebox_e2e/skills/planning/SKILL.md |
    | 框架 [search] | 段3 派发搜推 | Avernet singlebox_e2e/skills/search/SKILL.md |
    | worker 叶子自验收 | 段4 任务验收 | Avernet singlebox_e2e/skills/acceptance/SKILL.md |
    | 引擎 BBS 通知 | 段5 BBS 接力 | Avernet specs/2026-08-09-.../bbs-relay-single-task/SKILL.md |

    ## 再生成

    在本 feature dir 运行 `./assemble.sh`,从上述 5 段真源重新拼出 SKILL.md 与 references/。
    段体规则:剥各自 frontmatter 取正文,标题统一降一级并入,正文文本与 HR 零改(任务识别段逻辑零改)。
    源更新后重跑 assemble.sh 即可对齐。

    ## 上传 / 预装

    将 `task-loop/` 打包上传到 skills,activate 给所有 bot(预装即齐备)。
    """
    with open(os.path.join(PKG, "README.md"), "w", encoding="utf-8") as f:
        f.write(README)

    print("assembled task-loop skill at", PKG)


if __name__ == "__main__":
    main()
