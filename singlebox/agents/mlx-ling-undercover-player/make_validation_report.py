"""生成单局对局验证报告（validation/game-0X.zh-CN.md）。

数据源三件套，全部可复现：
  1. validation-logs/game-0X.jsonl —— 脚本裁判的逐步事件账本（S0–S5/SX、唤醒、异常）；
  2. ~/.openclaw/undercover-game/*.json —— uc 事实层的对局状态（座位/词/身份/每轮发言与票面/违规判定）；
  3. bcs-cli collaboration query —— 每个协作 run 的节点时间线（谁、何时、状态、attempt）。

用法：
    python3 make_validation_report.py --log validation-logs/game-01.jsonl \
        --bcs-cli <Avernet>/apps/bcs/target/debug/bcs-cli \
        --engines "bot_331e67b7=Apple MLX 本地端侧（Ling-3.0-tiny LoRA）" \
        --our-bot bot_331e67b7 --out validation/game-01.zh-CN.md

词与身份在对局结束后才出现在报告里（事后复盘视角），不构成对局中的信息泄露。
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

# uc check_text / parse_vote 的违规原因 → 报告分类
LEAK_REASONS = ("说出了自己的词", "连续的两个字", "拆开说了", "说出了对家的词")
VOTE_VOID_NOTE = "投票内容违规，本票作废"


def classify_violation(reason):
    if not reason:
        return None
    if any(k in reason for k in LEAK_REASONS):
        return "leak"
    if "超过" in reason and "字" in reason:
        return "overlong"
    return "other"


def ts_ms(ms):
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, CST).strftime("%H:%M:%S.%f")[:-3]


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def query_run(bcs_cli, run_id, token):
    env = {**os.environ, "BCN_BOT_TOKEN": token or ""}
    try:
        out = subprocess.run(
            [bcs_cli, "collaboration", "query", "--run", run_id, "--graph"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        return json.loads(out.stdout)
    except Exception as e:  # 服务没起/查不到都容忍，报告里如实标注
        return {"error": str(e)[:120]}


def fmt_dt(ms):
    return datetime.fromtimestamp(ms / 1000, CST).strftime("%Y-%m-%d %H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True, help="validation-logs/game-0X.jsonl")
    ap.add_argument("--state-dir", default=os.path.expanduser("~/.openclaw/undercover-game"))
    ap.add_argument("--bcs-cli", default="bcs-cli")
    ap.add_argument("--token-file", default=os.path.expanduser("~/.mlx-referee.token"))
    ap.add_argument("--engines", action="append", default=[],
                    help="bot_uuid=引擎名，可重复；未列出的座位记 OpenClaw")
    ap.add_argument("--our-bot", required=True, help="新玩家的 bot_uuid（重点统计对象）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    engines = dict(e.split("=", 1) for e in args.engines)
    # 运行时凭据只从本地文件读取（仓库不含任何凭据字面量）
    bcs_auth = ""
    if os.path.exists(args.token_file):
        with open(args.token_file) as fh:
            bcs_auth = fh.read().strip()

    events = load_jsonl(args.log)
    session = next((e.get("session") for e in events if e.get("session")), None)
    if not session:
        sys.exit("game log 里找不到 session id")
    state = json.load(open(
        os.path.join(args.state_dir, session.replace(":", "_") + ".json"), encoding="utf-8"))

    # run 时间线：按 open-round / open-vote / SX recover 里出现的 run_id 顺序去重
    runs, seen = [], set()
    anomalies = []
    for e in events:
        out = e.get("out") or {}
        rid = out.get("run_id")
        if isinstance(rid, str) and rid.startswith("sm-") and rid not in seen:
            seen.add(rid)
            runs.append((rid, e.get("step", "?")))
        if e.get("wake") in ("WATCHDOG", "ERROR") or (e.get("step") or "").startswith("SX"):
            anomalies.append(e)

    run_infos = [(rid, step, query_run(args.bcs_cli, rid, bcs_auth)) for rid, step in runs]

    seats = {s["seat"]: s for s in state["seats"]}
    config = state["config"]
    result = state.get("result") or {}
    words = state.get("words") or {}

    lines = []
    w = lines.append
    w(f"# 第 {args.out[-6:-3]} 局：详细对局记录")
    w("")
    w(f"Session：`{session}`。时间使用 Asia/Shanghai（UTC+08:00）。")
    w(f"整理脚本：`make_validation_report.py`（由 validation-logs + uc 状态 + collaboration query 三方对账生成）。")
    w("")
    w("## 环境与终局")
    w("")
    w(f"- 主持人：脚本裁判 `referee_driver.py`（SKILL.md 阶段机 S0–S5 确定性实现）；uc 事实层 "
      f"`undercover.py`（原样复用，发牌/计票/裁决逻辑未改动）。")
    w(f"- 默认牌桌：{config['players']} 人、{config['undercover']} 个卧底、最多 {config['max_rounds']} 轮、"
      f"{config['difficulty']} 难度；5 个 OpenClaw 玩家和 1 个 MLX 玩家（全 Bot 对局，无人类席位）。")
    w(f"- 终局相位：`{state['phase']}`；词语：平民「{words.get('civilian')}」/ 卧底「{words.get('undercover')}」。")
    w(f"- 协作运行：{len(run_infos)} 个，状态 "
      + "、".join(f"`{i[2].get('run', {}).get('status', '?') if 'run' in i[2] else 'query-failed'}`" for i in run_infos) + "。")
    w("")
    w("## 座位与执行者")
    w("")
    w("| 座位 | 公开名称 | 引擎 | 词语 | 身份 | 结局 |")
    w("| --- | --- | --- | --- | --- | --- |")
    for seat in sorted(seats):
        s = seats[seat]
        engine = engines.get(s.get("bot_uuid") or "", "OpenClaw")
        fate = "存活" if s["alive"] else f"第 {s['eliminated_round']} 轮出局"
        w(f"| {seat} | {s['display']} | {engine} | {s['word']} | {s['role']} | {fate} |")
    w("")
    w("## 协作节点与公开产物")
    w("")
    w("每行对应一个 BCS 节点记录，attempt 从 0 起算；产物列展示 uc 校验后的公开文本（泄词已遮蔽）。")
    w("")
    vstats = {seat: {"overlong": 0, "leak": 0, "badvote": 0} for seat in seats}
    for rnd in state["rounds"]:
        rn = rnd["round"]
        for rid, step, info in run_infos:
            run = info.get("run") or {}
            inp = run.get("input") or {}
            if inp.get("round") != rn:
                continue
            is_vote = "vote" in str(step)
            w(f"### 第 {rn} 轮 · {'投票' if is_vote else '发言'}")
            w("")
            w(f"Run：`{rid}`。状态：`{run.get('status', 'query-failed')}`。")
            w("")
            w("| node_id | 执行者 | 开始 | 完成 | 状态 | attempt | 公开产物/动作 | 主持人校验 |")
            w("| --- | --- | --- | --- | --- | --- | --- | --- |")
            for n in info.get("nodes", []):
                nid = n["node_id"]
                product, check = "—", "—"
                if nid.startswith("speak_"):
                    seat = int(nid.split("_")[1])
                    entry = rnd["speeches"].get(str(seat)) or {}
                    product = entry.get("display", "—")
                    v = classify_violation(entry.get("violation"))
                    check = entry.get("violation") or "通过"
                    if v:
                        vstats[seat]["overlong" if v == "overlong" else "leak"] += 1
                elif nid.startswith("vote_"):
                    seat = int(nid.split("_")[1])
                    entry = rnd["votes"].get(str(seat)) or {}
                    product = entry.get("display", "—")
                    check = entry.get("violation") or "通过"
                    if entry.get("target") is None and entry.get("note") != "弃权":
                        vstats[seat]["badvote"] += 1
                        check = check if check != "通过" else "无效票（格式无法解析）"
                    elif classify_violation(entry.get("violation")):
                        vstats[seat]["leak" if classify_violation(entry["violation"]) == "leak" else "overlong"] += 1
                elif nid == "collect":
                    product, check = "汇总全部公开发言", "—"
                elif nid == "tally":
                    product, check = "计票并公布结果", "—"
                w(f"| {nid} | {n.get('assignee_display_name', '—')} | {ts_ms(n.get('started_at'))} | "
                  f"{ts_ms(n.get('completed_at'))} | {n.get('status')} | {n.get('attempt', 0)} | "
                  f"{product} | {check} |")
            w("")
            if is_vote:
                counts = rnd.get("counts") or {}
                w(f"计票：{counts}；平票：{'是' if rnd.get('tie') else '否'}；"
                  f"出局：{rnd.get('eliminated') if rnd.get('eliminated') is not None else '无'}。")
                w("")
        # 每轮小结
        n_speech = len(rnd["speeches"])
        n_viol = sum(1 for v in rnd["speeches"].values() if v.get("violation"))
        w(f"本阶段：{n_speech} 条发言；主持人记录的发言违规：{n_viol}。")
        w("")

    w("## 违规统计")
    w("")
    w("| 座位 | 玩家 | 超字数 | 泄词 | 投票格式无法解析 |")
    w("| --- | --- | --- | --- | --- |")
    for seat in sorted(seats):
        s, v = seats[seat], vstats[seat]
        mark = "（新玩家）" if (s.get("bot_uuid") == args.our_bot) else ""
        w(f"| {seat} | {s['display']}{mark} | {v['overlong']} | {v['leak']} | {v['badvote']} |")
    w("")

    # 新玩家票面可解析率（验收第 2 条）
    our = args.our_bot
    our_seat = next((sid for sid, s in seats.items() if s.get("bot_uuid") == our), None)
    if our_seat is not None:
        total = good = 0
        for rnd in state["rounds"]:
            entry = rnd["votes"].get(str(our_seat))
            if not entry:
                continue
            total += 1
            if entry.get("target") is not None or entry.get("note") == "弃权":
                good += 1
        w(f"新玩家票面可解析率：{good}/{total}"
          + ("（100%，全部为有效票或明确弃权）" if total and good == total else "（存在无法解析票面）"))
        w("")

    if anomalies:
        w("## 异常记录")
        w("")
        for e in anomalies:
            w(f"- `{e.get('ts') or ''}` {e.get('wake') or e.get('step')}："
              f"{json.dumps({k: v for k, v in e.items() if k not in ('ts', 'session')}, ensure_ascii=False)[:400]}")
        w("")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"written: {args.out} ({len(lines)} lines, {len(run_infos)} runs)")


if __name__ == "__main__":
    main()
