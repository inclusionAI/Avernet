# Pure-Python Undercover Player (BCN V3)

A non-OpenClaw player for Avernet's “Who is the Undercover” table. It connects directly to
the BCN Bot WebSocket, replaces the `玩家稳健老陈` OpenClaw player, reuses that persona, and
follows the host's node instructions for speech, votes, and last words. It does not modify the
referee profile or the BCS server.

- Issue: `inclusionAI/Avernet#2611`; target branch `oss-camp2026`
- Protocol: BCN Bot WebSocket V3 (`/ws/bot`)
- Runtime: Python 3.12+, `websockets>=15,<17` (managed by `uv`)

## Run (after BCS is up and 4 other OpenClaw players are running)

```bash
export OPENCLAW_OPENAI_BASE_URL=https://your-endpoint/v1
export OPENCLAW_OPENAI_API_KEY=...
export OPENCLAW_OPENAI_MODEL_ID=your-model
cd examples/undercover-python-player
./run.sh
```

Then create a task-collaboration group in the web UI, select the referee plus the four
OpenClaw players and `玩家稳健老陈`, join the session, and send `开始`.

The full Chinese onboarding guide, protocol summary, 3-game evidence collection, and
troubleshooting live in [README.zh-CN.md](README.zh-CN.md).

## Offline tests (fake BCS, no model key needed)

```bash
uv run --with websockets --with pytest pytest -q
uv run --with ruff ruff check undercover_player tests
```

The suite covers the V3 handshake, onboard/visibility HTTP, `chat.send` ACK/final,
`chat.inject`, `chat.history`, exact-run `chat.abort`, late-output suppression, expired
results, duplicate frames, rules fallback without credentials, LLM repair/fallback, and
differential checks against the real referee parser (`undercover.py`).

Fake BCS tests prove protocol handling only; they are not a substitute for the required
three real mixed games, which have not been run yet.
