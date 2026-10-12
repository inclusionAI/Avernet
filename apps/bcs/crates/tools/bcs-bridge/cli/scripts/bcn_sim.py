#!/usr/bin/env python3
"""bcn_sim.py — bcs-bridge 本地调试台(扮演 BCS 一侧)。

一个终端跑 `serve` 启动 bridge(mock 或真实引擎均经 engine-tee.sh 采集),
另一个终端用 `send` 模拟 BCS 下行 chat.send,实时逐帧打印转换后的 SSE;
引擎原始事件(Claude Code stream-json / Codex app-server JSON-RPC)由 engine-tee.sh 落盘,
`logs` 随时查看,与 SSE 帧一一对照。

子命令:
  serve            构建+启动 bridge(默认 mock_cc.sh 引擎)
  send TEXT        模拟 BCS chat.send,流式打印 SSE 帧(交互请求时提示决策)
  resolve IID      对挂起的 interaction 发 interaction.resolve
  abort            对会话的活跃 run 发 chat.abort
  inject TEXT      发 chat.inject(上下文注入,不触发引擎)
  ping             发 bot.ping
  logs [TARGET]    看引擎原始 stdout / bridge→引擎 stdin / stderr / runs.log

常用:
  python3 scripts/bcn_sim.py serve                        # mock cc 引擎
  python3 scripts/bcn_sim.py send "讲个笑话"
  python3 scripts/bcn_sim.py logs stdout -f              # 原始引擎事件
  python3 scripts/bcn_sim.py logs converted -f           # bridge 转换事件
  python3 scripts/bcn_sim.py logs sse -f                 # 最终 SSE frame
  python3 scripts/bcn_sim.py serve --mock mock_cc_approval.sh   # HITL 审批流
  python3 scripts/bcn_sim.py send "部署" --auto-allow
  python3 scripts/bcn_sim.py serve --engine codex
  python3 scripts/bcn_sim.py serve --real [--engine-bin /path/to/claude]
"""

import argparse
import http.client
import json
import os
import subprocess
import sys
import time
import uuid

# This crate lives at apps/bcs/crates/tools/bcs-bridge/cli inside the BCS workspace.
CRATE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(CRATE)))  # apps/bcs
FIXTURES = os.path.join(CRATE, "..", "core", "tests", "fixtures")
BIN = os.path.join(WORKSPACE, "target", "debug", "bcs-bridge")
TEE = os.path.join(CRATE, "scripts", "engine-tee.sh")

DEV = os.environ.get("BRIDGE_DEV_DIR", "/tmp/bridge-dev")
LISTEN = os.environ.get("BRIDGE_LISTEN", "127.0.0.1:21100")
TOKEN = os.environ.get("BRIDGE_TOKEN", "tok-b2p")
PROVIDER_ID = os.environ.get("BRIDGE_PROVIDER_ID", "bridge-1")
BOT_REF = os.environ.get("BRIDGE_BOT_REF", "worker-1")

LOG_FILES = {
    "stdout": "engine.stdout.ndjson",  # 引擎原始事件(一行一个)
    "stdin": "engine.stdin.jsonl",     # bridge 写给引擎的行(user 消息/control_response)
    "stderr": "engine.stderr.log",
    "stderr-json": "engine.stderr.ndjson",  # 无 ANSI 的结构化 stderr
    "raw": "engine.raw.ndjson",             # bridge 读取到的原始 stdout
    "converted": "bridge.converted.ndjson", # StreamEvent 转换结果
    "sse": "bridge.sse.ndjson",             # 发给 BCS 的最终 SSE frame
    "runs": "runs.log",
}

TTY = sys.stdout.isatty()


def c(code: int, s: str) -> str:
    return f"\x1b[{code}m{s}\x1b[0m" if TTY else s


def dim(s): return c(2, s)
def green(s): return c(32, s)
def red(s): return c(31, s)
def blue(s): return c(34, s)
def yellow(s): return c(33, s)
def bold(s): return c(1, s)


def _hostport(listen=None):
    host, _, port = (listen or LISTEN).rpartition(":")
    return host or "127.0.0.1", int(port)


def post(payload: dict, headers: dict | None = None, timeout: int = 30, listen=None):
    """POST /webhook,返回 (conn, response)。body 用 utf-8 bytes(中文安全)。"""
    host, port = _hostport(listen)
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    h = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    conn.request("POST", "/webhook", body=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=h)
    return conn, conn.getresponse()


def req_shell(method: str, run: str, session: str | None, message=None, from_=None, params=None) -> dict:
    r = {"type": "req", "id": run, "method": method,
         "to_bot": {"provider_id": PROVIDER_ID, "provider_bot_ref": BOT_REF}}
    if session is not None:
        r["session_id"] = session
    if message is not None:
        r["message"] = message
    if from_ is not None:
        r["from"] = from_
    if params is not None:
        r["params"] = params
    return r


def msg(text: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------- serve

def cmd_serve(args):
    os.makedirs(DEV, exist_ok=True)
    ws = args.cwd
    os.makedirs(ws, exist_ok=True)

    engine_kind = args.engine
    if args.real:
        real = args.engine_bin or ("claude" if engine_kind == "claude-code" else "codex")
    else:
        fixture = args.mock or ("mock_cc.sh" if engine_kind == "claude-code" else "mock_codex_app_server.py")
        real = os.path.join(FIXTURES, fixture)
        if not os.path.exists(real):
            sys.exit(f"mock fixture 不存在: {real}")
        os.chmod(real, 0o755)
    os.chmod(TEE, 0o755)
    if not os.path.exists(TEE):
        sys.exit(f"缺少 engine-tee.sh: {TEE}")

    cfg = (
        f'provider_id = "{PROVIDER_ID}"\n'
        f'listen = "{args.listen}"\n'
        f'trace_dir = "{DEV}"\n\n'
        f'[[bot]]\n'
        f'provider_bot_ref = "{BOT_REF}"\n'
        f'engine = "{engine_kind}"\n'
        f'cwd = "{ws}"\n'
        f'engine_bin = "{TEE}"\n'
    )
    cfg_path = os.path.join(DEV, "bridge.toml")
    with open(cfg_path, "w") as f:
        f.write(cfg)

    print(bold(f">> bridge 配置 ({cfg_path})"))
    print(dim("   " + cfg.replace("\n", "\n   ")))
    print(bold(f">> 引擎: {engine_kind}  REAL_ENGINE={real}"))
    print(bold(f">> 引擎原始事件将落盘到 {DEV}/engine.stdout.ndjson"))
    print(dim(f">> bridge trace: {DEV}/engine.raw.ndjson / bridge.converted.ndjson / bridge.sse.ndjson"))
    print(dim(f">> 试发一条: python3 {sys.argv[0]} send \"讲个笑话\""))
    sys.stdout.flush()

    subprocess.run(["cargo", "build", "--manifest-path", os.path.join(BCS, "Cargo.toml"), "-p", "bcs-bridge"], check=True)
    if not os.path.exists(BIN):
        sys.exit(f"构建产物缺失: {BIN}")

    env = os.environ.copy()
    env.update({"BRIDGE_CONFIG": cfg_path, "REAL_ENGINE": real, "ENGINE_LOG_DIR": DEV,
                "RUST_LOG": args.log,
                "BCS_BRIDGE_PROVIDER_TOKENS": json.dumps({PROVIDER_ID: TOKEN})})
    os.execve(BIN, [BIN, "start"], env)


# ---------------------------------------------------------------- SSE 帧渲染

EVENT_STYLE = {
    "chat": green,
    "agent": blue,
    "interaction": yellow,
    "ping": dim,
}


def render_frame(idx: int, event: str, fid: str, data: str, pretty: bool) -> dict | None:
    """打印一帧,返回解析后的 dict(解析失败返回 None)。"""
    style = EVENT_STYLE.get(event, str)
    label = f"  #{idx:<3} {style(event or 'data')}"
    if fid:
        label += dim(f" id={fid}")
    obj = None
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        pass
    print(label, end="", flush=True)
    if pretty and obj is not None:
        print()
        print(dim("      " + json.dumps(obj, ensure_ascii=False, indent=2).replace("\n", "\n      ")))
    elif data:
        print("  " + data)
    else:
        print()
    return obj


def summarize_terminal(obj: dict) -> str:
    state = obj.get("state")
    if state == "final":
        content = (obj.get("message") or {}).get("content") or []
        texts = [p.get("text", "") for p in content if isinstance(p, dict)]
        return green("✔ final") + dim(f"  text={(''.join(texts))[:120]!r} stopReason={obj.get('stopReason')}")
    if state == "error":
        return red(f"✖ error  {obj.get('errorMessage')} kind={obj.get('errorKind')}")
    if state == "aborted":
        return yellow(f"⊘ aborted  stopReason={obj.get('stopReason')}")
    return ""


def fmt_interaction_detail(obj: dict) -> str:
    out = []
    if obj.get("kind") == "exec":
        out.append(dim(f"      command: {obj.get('command')!r}"))
        for opt in obj.get("options") or []:
            out.append(dim(f"      option: {opt.get('decision'):<10} {opt.get('label')}"))
    for q in obj.get("questions") or []:
        out.append(dim(f"      Q: {q.get('question')} options={[o.get('label') for o in q.get('options') or []]}"))
    return "\n  ".join(out)


# ---------------------------------------------------------------- send

def resolve_interaction(run: str, session: str | None, iid: str, kind: str, decision: str) -> str:
    payload = req_shell("interaction.resolve", f"resolve-{uuid.uuid4().hex[:6]}", session,
                        params={"bcsRunId": run, "runId": run, "interactionId": iid, "kind": kind,
                                "idempotencyKey": f"key-{iid}-{int(time.time())}", "decision": decision})
    conn, resp = post(payload, timeout=30)
    body = resp.read().decode("utf-8", "replace")
    ok = '"ok":true' in body or '"ok": true' in body
    print(yellow(f"     ↳ interaction.resolve[{iid}] decision={decision} → HTTP {resp.status} {body.strip()}")
          if ok else red(f"     ↳ interaction.resolve[{iid}] 失败: HTTP {resp.status} {body.strip()}"))
    conn.close()
    return body


def cmd_send(args):
    run = args.run or f"run-{uuid.uuid4().hex[:8]}"
    payload = req_shell("chat.send", run, args.session, message=msg(args.text))
    print(bold(f">> BCN chat.send  run={run}  session={args.session}"))
    print(dim(f">> to_bot={PROVIDER_ID}/{BOT_REF}  text={args.text!r}"))
    sys.stdout.flush()

    conn, resp = post(payload, headers={"X-BCN-Protocol-Version": "2.0", "Accept": "text/event-stream"},
                      timeout=args.timeout)
    ctype = resp.getheader("Content-Type") or ""
    if resp.status != 200 or "text/event-stream" not in ctype:
        body = resp.read().decode("utf-8", "replace")
        print(red(f"<< HTTP {resp.status} {ctype}\n{body}"))
        conn.close()
        return 1

    idx = 0
    counts: dict[str, int] = {}
    terminal = ""
    cur: dict[str, str] = {}
    started = time.time()
    try:
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith(":"):  # SSE comment = heartbeat
                print(dim("  · heartbeat"), flush=True)
                continue
            if not line:
                if cur:
                    idx += 1
                    counts[cur.get("event", "?")] = counts.get(cur.get("event", "?"), 0) + 1
                    obj = render_frame(idx, cur.get("event", ""), cur.get("id", ""), cur.get("data", ""), args.pretty)
                    # interaction/requested → 发起 HITL 决策(模拟 BCS 路由给 Human)
                    if cur.get("event") == "interaction" and isinstance(obj, dict) and obj.get("phase") == "requested":
                        print(yellow(bold(f"     ⏸  交互等待 Human 决策 (interactionId={obj.get('interactionId')} kind={obj.get('kind')})")))
                        detail = fmt_interaction_detail(obj)
                        if detail:
                            print(detail)
                        decision = _pick_decision(args)
                        resolve_interaction(run, args.session, obj["interactionId"], obj.get("kind") or "exec", decision)
                    if cur.get("event") == "chat" and isinstance(obj, dict):
                        term = summarize_terminal(obj)
                        if term:
                            terminal = term
                cur = {}
            else:
                key, _, val = line.partition(":")
                val = val.lstrip(" ")
                if key == "event":
                    cur["event"] = val
                elif key == "id":
                    cur["id"] = val
                elif key == "data":
                    cur["data"] = cur.get("data", "") + val
    except (TimeoutError, OSError) as e:
        print(red(f"\n<< 流中断: {e}"))
    finally:
        conn.close()

    elapsed = time.time() - started
    stats = "  ".join(f"{k}×{v}" for k, v in counts.items())
    print(bold("――――――――――――――――――――――――――――――"))
    print(f"{terminal or dim('(未读到终态帧)')}  {dim(f'帧数={idx}  {stats}  {elapsed:.1f}s')}")
    print(dim(f"run={run}  session={args.session}"))
    print(dim(f"引擎原始事件:  {DEV}/engine.stdout.ndjson   (tail -f 对照)"))
    print(dim(f"bridge→引擎:   {DEV}/engine.stdin.jsonl"))
    return 0 if terminal else 2


def _pick_decision(args) -> str:
    if args.auto_allow:
        return "allow_once"
    if args.auto_deny:
        return "deny"
    while True:
        answer = input(c(33, "     Human 决策? [allow_once/deny] ")).strip().lower()
        if answer in ("allow_once", "allow", "deny"):
            return "allow_once" if answer == "allow" else answer
        print(dim("     请输入 allow_once 或 deny"))


# ---------------------------------------------------------------- 其余子命令

def _simple_json_call(payload: dict, label: str, headers=None):
    conn, resp = post(payload, headers=headers)
    body = resp.read().decode("utf-8", "replace")
    conn.close()
    try:
        pretty = json.dumps(json.loads(body), ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        pretty = body
    print(f">> {label}  id={payload['id']}")
    print(f"<< HTTP {resp.status}\n{pretty}")
    return 0 if resp.status == 200 else 1


def cmd_resolve(args):
    payload = req_shell("interaction.resolve", f"resolve-{uuid.uuid4().hex[:6]}", args.session,
                        params={"bcsRunId": args.run, "runId": args.run, "interactionId": args.iid,
                                "kind": args.kind, "idempotencyKey": f"key-{args.iid}-{int(time.time())}",
                                "decision": args.decision})
    return _simple_json_call(payload, "interaction.resolve")


def cmd_abort(args):
    payload = req_shell("chat.abort", f"abort-{uuid.uuid4().hex[:6]}", args.session)
    return _simple_json_call(payload, "chat.abort")


def cmd_inject(args):
    payload = req_shell("chat.inject", f"inj-{uuid.uuid4().hex[:6]}", args.session,
                        message=msg(args.text),
                        from_={"kind": "bot", "name": args.from_name})
    return _simple_json_call(payload, "chat.inject")


def cmd_ping(_args):
    payload = req_shell("bot.ping", f"ping-{uuid.uuid4().hex[:6]}", None)
    return _simple_json_call(payload, "bot.ping")


def cmd_logs(args):
    name = LOG_FILES[args.target]
    path = os.path.join(DEV, name)
    print(dim(f">> {args.target}: {path}"))
    if not os.path.exists(path):
        print(dim(f"   (尚未生成 — 先 serve + send 一次)"))
        return 0
    cmd = ["tail", f"-n", str(args.lines)]
    if args.follow:
        cmd.append("-f")
    cmd.append(path)
    os.execvp("tail", cmd)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="bcs-bridge 本地调试台(模拟 BCS)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="构建并启动 bridge(前台, Ctrl-C 优雅退出)")
    p.add_argument("--engine", default="claude-code", choices=["claude-code", "codex"])
    p.add_argument("--mock", default=None, help="mock 引擎 fixture 名(默认按 engine 选)")
    p.add_argument("--real", action="store_true", help="用真实引擎(--engine 选 claude-code/codex)")
    p.add_argument("--engine-bin", default=None, help="真实引擎可执行文件(默认 PATH 里的 claude/codex)")
    p.add_argument("--listen", default=LISTEN)
    p.add_argument("--cwd", default=os.path.join(DEV, "workspace"))
    p.add_argument("--log", default=os.environ.get("RUST_LOG", "info"))
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("send", help="模拟 BCS chat.send 并逐帧打印 SSE")
    p.add_argument("text")
    p.add_argument("--session", default="s-1")
    p.add_argument("--run", default=None)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--auto-allow", action="store_true", help="interaction/requested 自动 allow_once")
    p.add_argument("--auto-deny", action="store_true", help="interaction/requested 自动 deny")
    p.add_argument("--pretty", action="store_true", help="data JSON 缩进展开")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("resolve", help="对挂起 interaction 发 interaction.resolve")
    p.add_argument("iid")
    p.add_argument("--decision", default="allow_once", choices=["allow_once", "deny"])
    p.add_argument("--session", default="s-1")
    p.add_argument("--run", default="run-1")
    p.add_argument("--kind", default="exec")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("abort", help="chat.abort 当前会话活跃 run")
    p.add_argument("--session", default="s-1")
    p.set_defaults(func=cmd_abort)

    p = sub.add_parser("inject", help="chat.inject 上下文(不触发引擎)")
    p.add_argument("text")
    p.add_argument("--session", default="s-1")
    p.add_argument("--from-name", default="observer")
    p.set_defaults(func=cmd_inject)

    p = sub.add_parser("ping", help="bot.ping")
    p.set_defaults(func=cmd_ping)

    p = sub.add_parser("logs", help="查看引擎原始输入/输出日志")
    p.add_argument("target", nargs="?", default="stdout", choices=list(LOG_FILES))
    p.add_argument("-f", "--follow", action="store_true")
    p.add_argument("--lines", type=int, default=60)
    p.set_defaults(func=cmd_logs)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
