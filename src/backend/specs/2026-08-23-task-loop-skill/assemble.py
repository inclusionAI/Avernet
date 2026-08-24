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
    "arch_analysis": "/Users/shangjian.msj/Github/Avernet/src/backend/tests/community/core/task/singlebox_e2e/skills/arch-analysis/SKILL.md",
    "planning_arch": "/Users/shangjian.msj/Github/Avernet/src/backend/tests/community/core/task/singlebox_e2e/skills/planning-arch/SKILL.md",
}
BBSREF = "/Users/shangjian.msj/Github/Avernet/src/backend/specs/2026-08-09-task-goal-driven-bbs-active-relay/bbs-relay-single-task/references"
RECODIR = "/Users/shangjian.msj/Desktop/task-recognition"

SEG_MARKER = {
    "recognition": "> 段1 · 任务识别(recognition;触发 /task 或 [RESUME_TASK] 或仅副屏标签)",
    "planning": "> 段2 · 任务规划(planning;触发 框架 [planning],非 arch 场景)",
    "search": "> 段3 · 任务派发搜推(search;触发 框架 [search])",
    "acceptance": "> 段4 · 任务验收(acceptance;worker 叶子自验收)",
    "bbs": "> 段5 · BBS 接力(bbs-relay-single-task;触发 引擎 BBS 通知;参考文档见 references/)",
    "arch_analysis": "> 段6 · 架构师名册 mock(arch-analysis;触发 叶子 instruction 含「某某某公司」;不联网返伪造名册)",
    "planning_arch": "> 段7 · 任务规划·arch 场景(planning-arch;触发 框架 [planning] 且 prompt 含「某某某公司」;确定式按根验收交付物集合 + done_children 查表)",
}
ORDER = ["recognition", "planning", "search", "acceptance", "bbs", "arch_analysis", "planning_arch"]

# 部署变体源(DEPLOY_GATEWAY 置非空=预发部署包时覆盖):acceptance 段改用 push 变体
# (协作群 driver/owner 验收+push 上报);本地 e2e(DEPLOY_GATEWAY=None)用原 poll 源。
SRC_DEPLOY = {
    "acceptance": os.path.join(FEAT, "segments", "acceptance-push.md"),
}
SEG_MARKER_DEPLOY = {
    "acceptance": "> 段4 · 任务验收(acceptance;协作群 driver/owner 验收+push 上报)",
}

# 部署期清洗:预发部署包剥 recognition 段 execute 行的 host 联调注释,留纯路径(skill 不写死 url,host 由平台层运行时解析);
# 置 None 则保留源 localhost 联调注释(本地 e2e 变体)。DEPLOY_GATEWAY 仅作变体开关,其 URL 不再注入 skill。
DEPLOY_GATEWAY = "https://teamclawgw-pre.alipay.com"


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


_HOST_COMMENT = re.compile(r"(POST /api/v1/collaboration/tasks/execute)\s*#[^\n]*")


def deploy_strip(text):
    """部署期清洗:剥 recognition 段 execute 行的 host 联调注释,留纯路径 `POST /api/v1/collaboration/tasks/execute`
    (skill 不写死 url,host 由平台层运行时解析;适配最新调用方式:recognition 仅出路径,acceptance 走 poll 输出 JSON,
    bbs 走 push 从引擎消息取 {backend})。DEPLOY_GATEWAY=None 时透传(本地 e2e 变体保留 localhost 注释)。"""
    if not DEPLOY_GATEWAY:
        return text
    return _HOST_COMMENT.sub(r"\1", text)


# acceptance 段 poll→push 文案替换对(仅部署变体启用;HEADER/README 内的 acceptance 描述随之翻转)
_ACC_PAIRS = [
    ("- 任务验收(acceptance):worker bot——叶子执行后自验收",
     "- 任务验收(acceptance):协作群 driver/owner bot——群产出后自验收并 push 上报(single_bot 叶子由框架内联 JSON 走 poll,不走本段)"),
    ("| 你是 worker bot,刚执行完叶子子任务,需按其 goal.acceptances 自验收(已收到 goal / instruction / sibling_outputs / execute_output) | 段4 任务验收 | 折叠进回投 result={success, data, gaps};不独立回投 |",
     "| 你是协作群 driver/owner bot,群已跑完叶子并产出交付物,需按其 goal.acceptances 自验收并上报(从群上下文取 {backend}/{loop_task_id}) | 段4 任务验收 | push:POST {backend}/api/v1/collaboration/tasks/callback/report {loop_task_id,result{success,data,gaps}}→on_report;single_bot 叶子不走本段(框架内联 JSON→poll) |"),
    ("段4(worker 叶子自验收)",
     "段4(协作群 driver/owner 验收+push)"),
    ("| worker 叶子自验收 | 段4 任务验收 | Avernet singlebox_e2e/skills/acceptance/SKILL.md |",
     "| 协作群 driver/owner 验收+push | 段4 任务验收 | 部署变体 segments/acceptance-push.md(本地 e2e 用源 acceptance/SKILL.md,poll) |"),
]


def src_for(k):
    """部署变体(DEPLOY_GATEWAY 非空)用 SRC_DEPLOY 覆盖;否则原 SRC。"""
    if DEPLOY_GATEWAY and k in SRC_DEPLOY:
        return SRC_DEPLOY[k]
    return SRC[k]


def marker_for(k):
    """部署变体的段 marker 覆盖(acceptance 段 poll→push 描述)。"""
    if DEPLOY_GATEWAY and k in SEG_MARKER_DEPLOY:
        return SEG_MARKER_DEPLOY[k]
    return SEG_MARKER[k]


def acceptance_replace(text):
    """部署变体:把 HEADER/README 内 acceptance 的 poll 描述翻为 push(段体已由 SRC_DEPLOY 提供 push 变体)。"""
    if not DEPLOY_GATEWAY:
        return text
    for poll, push in _ACC_PAIRS:
        text = text.replace(poll, push)
    return text


HEADER = """---
name: task-loop
description: 任务目标驱动执行闭环预装 skill,整合任务识别/规划/派发搜推/验收/BBS 接力/arch 场景规划变体(planning-arch)与架构师名册 mock(arch-analysis)共七段为单一 skill,预装到所有 bot 等同各段单独安装到对应 bot;各段按各自触发词自门控仅命中段执行(用户面 /task 或 [RESUME_TASK] 或副屏标签命中识别;框架 [planning] 命中规划,arch 场景含「某某某公司」命中 planning-arch 变体;框架 [search] 命中派发搜推;worker 叶子自验收命中验收;引擎 BBS 通知命中接力,其 scoped 叶子 instruction 含「某某某公司」时按 arch-analysis 产架构师名册)。
version: 1.0.0
author: avernet-task-framework
tags: [task, loop, orchestrate, task-recognition, task-planning, task-search, task-acceptance, bbs-relay, arch-analysis, task-planning-arch]
---

# task-loop

预装到所有 bot 的任务目标驱动执行闭环 skill。本 skill 内含七段,**只执行被触发词命中的那一段**,其余段不参与;给任一 bot 预装本 skill 等同把对应段单独安装到该 bot。

- 任务识别(recognition):对话 bot 用户面——`/task` 或平台 `[RESUME_TASK]` 回传或仅副屏标签
- 任务规划(planning):owner bot——框架 prompt 头部 `[planning]`(非 arch 场景)
- 任务规划·arch 场景(planning-arch):owner bot——框架 `[planning]` 且 prompt 含「某某某公司」,按根验收交付物集合 + done_children 确定式查表
- 任务派发搜推(search):owner bot——框架 prompt 头部 `[search]`
- 任务验收(acceptance):worker bot——叶子执行后自验收
- BBS 接力(bbs-relay-single-task):中继 bot——引擎主动通知
- 架构师名册 mock(arch-analysis):中继/worker bot——叶子 instruction 含「某某某公司」,返伪造架构师名册

段体取自各段真源 SKILL.md,仅标题层级统一降一级并入;各段逻辑 / 规则 / 卡片格式 / 触发与输出契约保持原样。先读下面"路由规则"确定本该执行哪段,再只跑那一段。

## 路由规则(最先读,只跑命中段)

按**本次收到的触发词 / 上下文**分流(不按 bot 身份;同一 bot 按其收到的触发执行对应段):

| 触发 / 上下文 | 命中段 | 执行要点 |
|---|---|---|
| 用户消息以 `/task` 开头;或上下文含 `[RESUME_TASK]`;或消息仅 `<AixUI type="panel" component="task-loop" ...>` 副屏标签 | 段1 任务识别 | 出 AixUI 卡片(cardId 固定 card_3e31e1f1),到 task_ready 为止;执行由平台层调 POST /api/v1/collaboration/tasks/execute |
| prompt 头部标记 `[planning]`,含目标节点 node_id 与任务态快照,**且 prompt 不含「某某某公司」**(非 arch 场景) | 段2 任务规划 | 返回 JSON 对象 {tasks: List[TaskSpec], has_gap, gap_detail};tasks 为空即 gap 闭=验收通过 |
| prompt 头部标记 `[planning]`,**且 prompt 含「某某某公司」**(arch 场景;交付物含架构师名册/技术栈概览/双视角分析等) | 段7 任务规划·arch 场景 | 同段2 输出契约;按根验收交付物集合 + done_children 确定式查表产 N_tech_stack / N_dual_view / N_architects |
| prompt 头部标记 `[search]`,含子任务需求与候选集 catalog | 段3 任务派发搜推 | 返回 4 态 JSON(HIT_SINGLE / HIT_GROUP / HIT_MULTI_BOTS / MISS) |
| 你是 worker bot,刚执行完叶子子任务,需按其 goal.acceptances 自验收(已收到 goal / instruction / sibling_outputs / execute_output) | 段4 任务验收 | 折叠进回投 result={success, data, gaps};不独立回投 |
| 收到引擎主动发的 BBS 任务消息(含 task_id + 后端 base url + 自身 bot_id,且引擎已替你占根) | 段5 BBS 接力 | 跳过 scan / claim / 自判,直接 attach -> 执行 -> result |
| 叶子执行输入(instruction)含关键词「某某某公司」,且非框架 `[planning]`/`[search]` 头 | 段6 架构师名册 mock | 不联网,直接返 mock 伪造架构师名册 JSON(domain/architects[]/note) |

**优先级**(实际多互斥,显式定义避免歧义):段5(引擎 BBS 通知) > 段7/段2/段3(框架 `[planning]`/`[search]` 头;arch 场景「某某某公司」命中段7,优先于段2) > 段4(worker 叶子自验收) > 段1(`/task` / `[RESUME_TASK]` / 副屏标签) > 段6(叶子 instruction 含「某某某公司」)。

**段5 ↔ 段6 并用(arch 接力链路唯一例外)**:段5 命中(BBS 通知)时,其 attach 的 scoped 叶子若 instruction 含「某某某公司」,叶子产出按**段6 arch-analysis** 规则(mock 名册),段5 仍管 attach / result 协议。除此之外严格"只跑命中段"。

**未命中任何段**:静默结束本轮——不虚构任务、不追问、不输出卡片、不乱执行(相当于 return / no-op)。反例:`[planning]` / `[search]` 是框架执行期 prompt,不是用户任务提交,不要走段1。

**只跑命中段**:命中某段即按该段全部规则执行,其余段规则不参与(段5↔段6 arch 接力并用为唯一例外)。

## 场景叠加层(泛化主干 + 演示案例优化)

本 skill 分两层,在保证泛化的同时承载演示案例的针对性优化:

- **泛化主干**:段1~段5(识别 / 规划 / 派发搜推 / 验收 / BBS 接力),默认行为,段体逐字节取自通用真源、不可改;未命中任何案例信号时即纯主干,可处理任意任务(泛化性来源)。
- **案例叠加段**:段6 arch-analysis / 段7 planning-arch,由**案例信号**(当前为 prompt/instruction 含「某某某公司」)门控,命中才激活——段7 在 arch 场景覆盖段2(同输出契约,换确定式剧本),段6 为 arch 专用叶子执行器(段5 接力时并用)。未命中案例信号 → 叠加段不参与,主干照常运行。
- **扩展契约**:新增演示案例 = 追加其叠加段 + 注册其案例信号(强特征关键词/上下文),不改段1~段5;移除全部叠加段即回退为纯泛化 task-loop。案例信号须用强特征(如「某某某公司」)以免误命中通用任务。

> 泛化性由主干保证(默认 + 不可改);针对性优化由叠加段提供(信号门控、可插拔、零侵入主干)。

"""

def main():
    os.makedirs(REFS, exist_ok=True)
    parts = [HEADER]
    for k in ORDER:
        parts.append(marker_for(k) + "\n\n")
        parts.append(body(src_for(k)) + "\n\n")
    skillmd = "".join(parts).rstrip("\n") + "\n"
    skillmd = deploy_strip(skillmd)  # 部署期清洗:剥 recognition 段 execute 行 host 注释(skill 不写死 url,平台层解析 host)
    skillmd = acceptance_replace(skillmd)  # 部署变体:acceptance 段 poll→push(marker/段体/路由表文案)
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

任务目标驱动执行闭环的单一预装 skill,整合 7 段(识别 / 规划 / arch 场景规划变体 / 派发搜推 / 验收 / BBS 接力 / 架构师名册 mock),
预装到所有 bot 即等同各段单独安装到对应 bot;每段按各自触发词自门控,仅命中段执行(段5↔段6 arch 接力链路并用为唯一例外)。

## 结构

```
task-loop/
├── SKILL.md          # frontmatter + # task-loop + 路由规则 + 段1..段7
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
| 框架 [planning](非 arch 场景) | 段2 任务规划 | Avernet singlebox_e2e/skills/planning/SKILL.md |
| 框架 [planning] 且 prompt 含「某某某公司」 | 段7 任务规划·arch 场景 | Avernet singlebox_e2e/skills/planning-arch/SKILL.md |
| 框架 [search] | 段3 派发搜推 | Avernet singlebox_e2e/skills/search/SKILL.md |
| worker 叶子自验收 | 段4 任务验收 | Avernet singlebox_e2e/skills/acceptance/SKILL.md |
| 引擎 BBS 通知 | 段5 BBS 接力 | Avernet specs/2026-08-09-.../bbs-relay-single-task/SKILL.md |
| 叶子 instruction 含「某某某公司」 | 段6 架构师名册 mock | Avernet singlebox_e2e/skills/arch-analysis/SKILL.md |

## 再生成

在本 feature dir 运行 `./assemble.sh`,从上述 7 段真源重新拼出 SKILL.md 与 references/。
段体规则:剥各自 frontmatter 取正文,标题统一降一级并入,正文文本与 HR 零改(任务识别段逻辑零改;arch-analysis/planning-arch 段原样嵌入)。
源更新后重跑 assemble.sh 即可对齐。

## 上传 / 预装

将 `task-loop/` 打包上传到 skills,activate 给所有 bot(预装即齐备;arch e2e 场景:owner 用段7 规划、中继 bot 用段5+段6 产架构师名册)。

## 部署变体(预发 / 本地 e2e)

`assemble.py` 常量 `DEPLOY_GATEWAY` 控制变体:置预发网关 `https://teamclawgw-pre.alipay.com` 则把 recognition 段本地联调 URL 重定向到预发(生成预发部署包);置 `None` 则保留源 localhost(本地 e2e 变体)。源 SKILL.md 不动,两变体一键再生。
"""
    with open(os.path.join(PKG, "README.md"), "w", encoding="utf-8") as f:
        f.write(acceptance_replace(README))

    print("assembled task-loop skill at", PKG)


if __name__ == "__main__":
    main()
