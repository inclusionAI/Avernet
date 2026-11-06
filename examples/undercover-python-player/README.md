# Pure-Python Undercover Player (BCN V3)

A non-OpenClaw player for Avernet's “Who is the Undercover” table. It connects directly to
the BCN Bot WebSocket, replaces the `玩家稳健老陈` OpenClaw player, reuses that persona, and
follows the host's node instructions for speech, votes, and last words. It does not modify the
referee profile or the BCS server.

- Issue: `inclusionAI/Avernet#2611`; target branch `oss-camp2026`
- Protocol: BCN Bot WebSocket V3 (`/ws/bot`)
- Runtime: Python 3.12+, `websockets>=15,<17` (managed by `uv`)

## Run (after BCS, the OpenClaw referee, and 4 other OpenClaw players are running)

```bash
export OPENCLAW_OPENAI_BASE_URL=https://your-endpoint/v1
export OPENCLAW_OPENAI_API_KEY=...
export OPENCLAW_OPENAI_MODEL_ID=your-model
cd examples/undercover-python-player
# on hosts where the default CA chain fails for the model endpoint:
SSL_CERT_FILE=/etc/ssl/cert.pem ./run.sh
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

Fake BCS tests prove protocol handling only. Three real mixed games on 2026-11-01 have also
completed in one local BCS group: the Python player produced four model speeches and four valid
model votes (`source=llm`, `degraded=false`, zero player violations). Four host-recorded
“over 10 characters” flags belong to the human player's structured-vote JSON length and did
not invalidate those votes. The first game had one host-side summary retry; no human used
“stuck” recovery. Public sanitized evidence: [evidence/README.md](evidence/README.md), including
full session IDs and per-round speech/vote validity. Controlled native BCS PK,
connection-level reconnect, and exact-run abort with late-output suppression were
also exercised using the actual Python client. These controlled checks do not replace
in-game recovery or independent 30-minute onboarding.
A student who did not develop the player reported following the Chinese guide to connect it
to their own local table within 30 minutes, then playing without issues. Setup and play
together lasted over 30 minutes; exact timing and environment logs were not supplied.

## Model budget and TLS

Reasoning models such as DeepSeek Flash count reasoning tokens against `max_tokens`; a
budget of 96 can produce `finish_reason=length` with empty `content`. The player defaults
to `max_tokens=2048` and doubles the budget (up to 8192, at most 3 attempts) when content is
empty or truncated before falling back to rules. Override with
`UNDERCOVER_LLM_MAX_TOKENS`, `UNDERCOVER_LLM_MAX_TOKENS_CEILING`, and
`UNDERCOVER_LLM_MAX_ATTEMPTS`.

On hosts where Python's default CA chain fails with `CERTIFICATE_VERIFY_FAILED`, run with
`SSL_CERT_FILE=/etc/ssl/cert.pem ./run.sh`. Never disable certificate verification.
