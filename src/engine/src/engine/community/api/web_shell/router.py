"""
Web Terminal Service — debug PTY over WebSocket.

Engine-specific PTY work (fork + setuid + exec) lives in the engine's
``web_shell`` plugin (e.g.
:class:`engine.community.engines.openclaw.web_shell.OpenClawWebShellService`).
The router still owns:

* the static ``/terminal`` HTML page (xterm.js bootstrap; engine-agnostic)
* the ``/terminal/health`` health endpoint
* WebSocket accept + the gather() of pump tasks
* JSON resize-message parsing

When the active engine doesn't declare ``WEB_SHELL_OPEN`` the HTTP
endpoints 501 and the WebSocket closes with code 4501.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from engine.community.api.caps import check_capability
from engine.community.api.web_shell.schemas import ResizeMessage  # noqa: F401 — type doc
from engine.community.core.engine.capability import Capability
from engine.community.core.engine.exceptions import CapabilityNotSupportedError

log = logging.getLogger("api-web-shell")

router = APIRouter(tags=["webshell"])


def _web_shell_plugin():
    from engine.community.manager import EngineManager
    return EngineManager.get_instance().web_shell


def _try_check_token(token: str) -> tuple[bool, bool]:
    """Returns (token_ok, plugin_available)."""
    try:
        plugin = _web_shell_plugin()
    except CapabilityNotSupportedError:
        return (False, False)
    return (plugin.check_token(token), True)


def build_html(token: str = "") -> str:
    """Generate the xterm.js debug-terminal page.

    Engine-agnostic: only reads pod identity from the host environment,
    so kept in the router rather than the plugin. The frontend wires
    itself to ``/ws/terminal`` (relative to the page path).
    """
    pod_name = os.environ.get("POD_NAME", os.uname().nodename)
    namespace = os.environ.get("POD_NAMESPACE", "unknown")
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Terminal · {pod_name}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#1e1e1e; display:flex; flex-direction:column; height:100vh; overflow:hidden; }}

  #topbar {{
    background:#252526; border-bottom:1px solid #3c3c3c;
    padding:6px 16px; display:flex; align-items:center; gap:12px;
    font-family:monospace; font-size:13px; color:#ccc; flex-shrink:0;
  }}
  #topbar .title {{ color:#4ec9b0; font-weight:bold; }}
  #topbar .meta  {{ color:#888; font-size:12px; }}
  #topbar .dot   {{
    width:8px; height:8px; border-radius:50%;
    background:#888; margin-left:auto; transition:background .3s;
  }}
  #topbar .dot.ok  {{ background:#4ec9b0; }}
  #topbar .dot.err {{ background:#f44747; }}
  #topbar .status  {{ font-size:12px; color:#888; }}

  #toolbar {{
    background:#2d2d2d; padding:4px 16px; display:flex; gap:8px; flex-shrink:0;
  }}
  .btn {{
    background:#3c3c3c; color:#ccc; border:none; border-radius:3px;
    padding:3px 10px; font-size:12px; cursor:pointer; font-family:monospace;
  }}
  .btn:hover {{ background:#505050; color:#fff; }}
  .btn.danger {{ color:#f44747; }}
  .btn.danger:hover {{ background:#5a1d1d; }}

  #terminal-wrap {{ flex:1; overflow:hidden; padding:4px; }}
  .xterm {{ height:100% !important; }}
  .xterm-viewport {{ overflow-y:auto !important; }}
</style>
</head>
<body>

<div id="topbar">
  <span class="title">⬡ Web Terminal</span>
  <span class="meta">pod: <b style="color:#9cdcfe">{pod_name}</b></span>
  <span class="meta">ns: <b style="color:#9cdcfe">{namespace}</b></span>
  <div class="dot" id="dot"></div>
  <span class="status" id="status">connecting…</span>
</div>

<div id="toolbar">
  <button class="btn" onclick="sendText('ls -la\\n')">ls</button>
  <button class="btn" onclick="sendText('pwd\\n')">pwd</button>
  <button class="btn" onclick="sendText('env\\n')">env</button>
  <button class="btn" onclick="sendText('df -h\\n')">df</button>
  <button class="btn" onclick="sendText('free -h\\n')">free</button>
  <button class="btn" onclick="sendText('ps aux\\n')">ps</button>
  <button class="btn" onclick="term.clear()">clear</button>
  <button class="btn danger" onclick="reconnect()">reconnect</button>
</div>

<div id="terminal-wrap"></div>

<script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xterm-addon-web-links@0.9.0/lib/xterm-addon-web-links.min.js"></script>
<script>
const TOKEN = new URLSearchParams(location.search).get('token') || '';

const term = new Terminal({{
  cursorBlink : true,
  fontSize    : 14,
  fontFamily  : 'Menlo, Monaco, "Courier New", monospace',
  scrollback  : 5000,
  theme       : {{
    background  : '#1e1e1e',
    foreground  : '#d4d4d4',
    cursor      : '#d4d4d4',
    black       : '#1e1e1e',
    red         : '#f44747',
    green       : '#4ec9b0',
    yellow      : '#dcdcaa',
    blue        : '#569cd6',
    magenta     : '#c678dd',
    cyan        : '#56b6c2',
    white       : '#d4d4d4',
    brightBlack : '#808080',
  }}
}});

const fitAddon      = new FitAddon.FitAddon();
const webLinksAddon = new WebLinksAddon.WebLinksAddon();
term.loadAddon(fitAddon);
term.loadAddon(webLinksAddon);
term.open(document.getElementById('terminal-wrap'));
fitAddon.fit();

let ws = null;

function setStatus(text, state) {{
  document.getElementById('status').textContent = text;
  const dot = document.getElementById('dot');
  dot.className = 'dot' + (state ? ' ' + state : '');
}}

function connect() {{
  const proto    = location.protocol === 'https:' ? 'wss' : 'ws';
  const basePath = location.pathname.replace(/\\/terminal\\/?$/, '');
  const urlParams = location.search;
  const url = `${{proto}}://${{location.host}}${{basePath}}/ws/terminal${{urlParams}}`;
  console.log('Connecting to:', url);
  ws = new WebSocket(url);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {{ setStatus('connected', 'ok'); sendResize(); }};
  ws.onmessage = (e) => {{
    if (e.data instanceof ArrayBuffer) term.write(new Uint8Array(e.data));
    else term.write(e.data);
  }};
  ws.onclose = (e) => {{
    setStatus(`closed (${{e.code}})`, 'err');
    term.write('\\r\\n\\x1b[31m── session closed ──\\x1b[0m\\r\\n');
  }};
  ws.onerror = () => {{ setStatus('error', 'err'); }};
}}

function sendResize() {{
  if (ws && ws.readyState === WebSocket.OPEN) {{
    ws.send(JSON.stringify({{ type:'resize', cols:term.cols, rows:term.rows }}));
  }}
}}

function sendText(text) {{
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(text);
}}

function reconnect() {{
  if (ws) ws.close();
  term.write('\\r\\n\\x1b[33m── reconnecting… ──\\x1b[0m\\r\\n');
  setTimeout(connect, 500);
}}

term.onData(data => {{
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(data);
}});

window.addEventListener('resize', () => {{ fitAddon.fit(); sendResize(); }});

connect();
</script>
</body>
</html>"""


@router.get("/terminal")
async def terminal_index(token: str = ""):
    """Render the xterm.js debug page (or 403 / 501)."""
    try:
        check_capability(Capability.WEB_SHELL_OPEN)
    except Exception:
        return HTMLResponse("<h3>501 Not Implemented</h3>", status_code=501)

    token_ok, plugin_ok = _try_check_token(token)
    if not plugin_ok:
        return HTMLResponse("<h3>501 Not Implemented</h3>", status_code=501)
    if not token_ok:
        return HTMLResponse("<h3>403 Forbidden</h3>", status_code=403)
    return HTMLResponse(build_html(token))


@router.get("/terminal/health")
async def terminal_health():
    """Health endpoint — always responds, surfaces pod identity for ops."""
    return {
        "status": "ok",
        "pod": os.environ.get("POD_NAME", os.uname().nodename),
    }


@router.websocket("/ws/terminal")
async def ws_terminal(websocket: WebSocket, token: str = ""):
    """WebSocket pump — opens a PTY session via plugin, then shovels
    bytes both ways until the WS disconnects."""
    try:
        check_capability(Capability.WEB_SHELL_OPEN)
    except Exception:
        await websocket.close(code=4501, reason="Not Implemented")
        return

    token_ok, plugin_ok = _try_check_token(token)
    if not plugin_ok:
        await websocket.close(code=4501, reason="Not Implemented")
        return
    if not token_ok:
        await websocket.close(code=4003, reason="Forbidden")
        return

    await websocket.accept()
    client = websocket.client
    log.info("Terminal session open client=%s:%s", client.host, client.port)

    plugin = _web_shell_plugin()
    session = await plugin.open_session()

    async def pty_to_ws():
        try:
            while True:
                data = await session.read()
                if not data:
                    break
                await websocket.send_bytes(data)
        except (OSError, WebSocketDisconnect):
            pass

    async def ws_to_pty():
        try:
            while True:
                raw = await websocket.receive_text()
                if raw.startswith("{"):
                    try:
                        msg = json.loads(raw)
                        if msg.get("type") == "resize":
                            cols = int(msg.get("cols", 80))
                            rows = int(msg.get("rows", 24))
                            session.resize(cols, rows)
                        continue
                    except (json.JSONDecodeError, ValueError):
                        pass
                session.write(raw.encode("utf-8", errors="replace"))
        except (WebSocketDisconnect, OSError):
            pass

    try:
        await asyncio.gather(pty_to_ws(), ws_to_pty())
    finally:
        session.close()
        log.info(
            "Terminal session closed client=%s:%s", client.host, client.port,
        )
