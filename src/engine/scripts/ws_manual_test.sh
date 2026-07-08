#!/bin/bash
#
# ws_manual_test.sh — 交互式 WebSocket 手动测试
#
# Usage:
#   ./scripts/ws_manual_test.sh              # 进入交互模式（wscat）
#   ./scripts/ws_manual_test.sh handshake    # 只做握手验证
#   ./scripts/ws_manual_test.sh chat "你好"  # 握手+发送一条消息+等响应
#   ./scripts/ws_manual_test.sh abort-test   # 自动执行 abort-then-continue 场景
#
# Prerequisites:
#   - engine running on :20003
#   - npx wscat available

set -e

WS_URL="${WS_URL:-ws://localhost:20003/api/claude-code/ws}"
SESSION_KEY="${SESSION_KEY:-agent:claude-code-ws:session:manual-test:user:claude-code-ws}"

GREEN=$'\033[0;32m'
CYAN=$'\033[1;36m'
YELLOW=$'\033[1;33m'
NC=$'\033[0m'

# ── 消息模板 ──

msg_connect() {
  cat <<'EOF'
{"type":"req","id":"h1","method":"connect","params":{"client":{"id":"manual-test","version":"1.0.0","platform":"terminal","mode":"test"},"minProtocol":3,"maxProtocol":3}}
EOF
}

msg_chat() {
  local message="${1:-你好}"
  local idem_key="idem-$(date +%s)"
  cat <<EOF
{"type":"req","id":"c1","method":"chat.send","params":{"sessionKey":"${SESSION_KEY}","message":"${message}","idempotencyKey":"${idem_key}","cwd":"/tmp"}}
EOF
}

msg_abort() {
  cat <<EOF
{"type":"req","id":"a1","method":"chat.abort","params":{"sessionKey":"${SESSION_KEY}"}}
EOF
}

msg_reset() {
  cat <<EOF
{"type":"req","id":"r1","method":"sessions.reset","params":{"sessionKey":"${SESSION_KEY}"}}
EOF
}

# ── 模式 ──

do_interactive() {
  echo ""
  echo -e "${CYAN}═══ WebSocket Interactive Mode ═══${NC}"
  echo ""
  echo -e "  URL: ${GREEN}${WS_URL}${NC}"
  echo -e "  Session: ${SESSION_KEY}"
  echo ""
  echo -e "  ${YELLOW}连接后复制以下消息发送:${NC}"
  echo ""
  echo -e "  ${GREEN}1. 握手 (必须第一个发):${NC}"
  echo "  $(msg_connect)"
  echo ""
  echo -e "  ${GREEN}2. 发送聊天:${NC}"
  echo "  $(msg_chat '你的基础模型是什么')"
  echo ""
  echo -e "  ${GREEN}3. 中止:${NC}"
  echo "  $(msg_abort)"
  echo ""
  echo -e "  ${GREEN}4. 重置会话:${NC}"
  echo "  $(msg_reset)"
  echo ""
  echo -e "${YELLOW}  Ctrl+C 退出${NC}"
  echo ""

  npx wscat -c "$WS_URL"
}

do_handshake() {
  echo -e "${CYAN}Testing handshake...${NC}"
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  ENGINE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
  local PYTHON="${ENGINE_DIR}/.venv/bin/python"
  "$PYTHON" -c "
import asyncio, json, websockets
async def t():
    ws = await websockets.connect('${WS_URL}', max_size=16*1024*1024)
    await ws.send(json.dumps({'type':'req','id':'h1','method':'connect','params':{'client':{'id':'test','version':'1.0.0','platform':'terminal','mode':'test'},'minProtocol':3,'maxProtocol':3}}))
    r = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    if r.get('ok'):
        srv = r.get('payload',{}).get('server',{})
        print(f'[PASS] Handshake OK — conn_id={srv.get(\"conn_id\",\"-\")}, version={srv.get(\"version\",\"-\")}')
    else:
        print(f'[FAIL] {r.get(\"error\",{})}')
    await ws.close()
asyncio.run(t())
"
}

do_chat() {
  local message="${1:-你好}"
  echo -e "${CYAN}Sending chat: ${message}${NC}"
  echo -e "${YELLOW}Session: ${SESSION_KEY}${NC}"
  echo ""

  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  ENGINE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
  local PYTHON="${ENGINE_DIR}/.venv/bin/python"
  "$PYTHON" -c "
import asyncio, json, uuid, time, websockets

async def chat():
    ws = await websockets.connect('${WS_URL}', max_size=16*1024*1024)
    # handshake
    await ws.send(json.dumps({'type':'req','id':'h1','method':'connect','params':{'client':{'id':'chat-test','version':'1.0.0','platform':'terminal','mode':'test'},'minProtocol':3,'maxProtocol':3}}))
    r = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    if not r.get('ok'):
        print(f'Handshake failed: {r}')
        return
    print('[OK] Connected')

    # chat.send
    await ws.send(json.dumps({'type':'req','id':'c1','method':'chat.send','params':{'sessionKey':'${SESSION_KEY}','message':'''${message}''','idempotencyKey':uuid.uuid4().hex,'cwd':'/tmp'}}))
    print('[..] Waiting for response...')

    t0 = time.monotonic()
    text = ''
    while time.monotonic() - t0 < 300:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
        except asyncio.TimeoutError:
            print('[timeout]')
            break
        frame = json.loads(raw)
        if frame.get('type') == 'res':
            if not frame.get('ok'):
                err = frame.get('error',{})
                msg = err.get('message','unknown')
                if 'token' in msg.lower():
                    print(f'[OK] Reached relay (auth required: {msg})')
                else:
                    print(f'[ERROR] {msg}')
                break
            print(f'[OK] Accepted ({time.monotonic()-t0:.1f}s)')
        elif frame.get('type') == 'event':
            payload = frame.get('payload',{})
            state = payload.get('state','')
            stream = payload.get('stream','')
            if state == 'final':
                msg = payload.get('message',{})
                for blk in msg.get('content',[]):
                    if blk.get('type') == 'text':
                        text += blk['text']
                break
            elif state in ('error','aborted'):
                print(f'[{state}]')
                break
            else:
                # 打印进度
                elapsed = time.monotonic() - t0
                print(f'  [{elapsed:.0f}s] event: stream={stream} state={state}', flush=True)

    elapsed = time.monotonic() - t0
    if text:
        print(f'\\n--- Response ({elapsed:.1f}s) ---')
        print(text)
        print('---')
    await ws.close()

asyncio.run(chat())
"
}

do_abort_test() {
  echo -e "${CYAN}═══ Abort-then-Continue Test ═══${NC}"
  echo ""
  echo "This test requires manual timing. Steps:"
  echo ""
  echo -e "  1. ${GREEN}Open terminal A:${NC}"
  echo "     npx wscat -c $WS_URL"
  echo ""
  echo -e "  2. ${GREEN}Send handshake:${NC}"
  echo "     $(msg_connect)"
  echo ""
  echo -e "  3. ${GREEN}Send long task:${NC}"
  local long_msg='{"type":"req","id":"c-long","method":"chat.send","params":{"sessionKey":"'${SESSION_KEY}'","message":"创建3个文件,并每个文件打印hello world","idempotencyKey":"abort-test-'$(date +%s)'","cwd":"/tmp"}}'
  echo "     ${long_msg}"
  echo ""
  echo -e "  4. ${YELLOW}Wait for ok=true, then send abort:${NC}"
  echo "     $(msg_abort)"
  echo ""
  echo -e "  5. ${YELLOW}Wait for state=aborted, wait 3 seconds, then:${NC}"
  local continue_msg='{"type":"req","id":"c-continue","method":"chat.send","params":{"sessionKey":"'${SESSION_KEY}'","message":"你好","idempotencyKey":"continue-'$(date +%s)'","cwd":"/tmp"}}'
  echo "     ${continue_msg}"
  echo ""
  echo -e "  6. ${GREEN}Verify: should get ok=true → events → state=final${NC}"
  echo ""
  echo "─────────────────────────────────────────"
  echo ""
  echo -e "Or run the automated version:"
  echo -e "  ${GREEN}python scripts/test_abort_then_continue.py${NC}"
  echo ""
}

case "${1:-}" in
  handshake)
    do_handshake
    ;;
  chat)
    do_chat "${2:-你好}"
    ;;
  abort-test)
    do_abort_test
    ;;
  *)
    do_interactive
    ;;
esac
