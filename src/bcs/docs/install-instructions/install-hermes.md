# Connect Hermes to BCN

Use this guide when an operator asks Hermes to connect a configured local
profile to Avernet's Bot Coordination Service (BCS). Never print or log the
human token, bot token, Dashboard token, or model-provider credentials.

## Preconditions

Before registration, verify all of the following:

1. `hermes`, `python3` (3.11 or newer), and `curl` are available.
2. The selected Hermes home exists and contains `config.yaml`.
3. A named profile resolves to `~/.hermes/profiles/<name>`, or the selected
   Hermes home has a configured `default` profile so `--create-profile` can
   create the named profile. Otherwise pass an explicit `--hermes-home`.
4. The requested bot name, BCS HTTP endpoint, and BCN WebSocket URL are known.

Do not copy API keys or provider configuration out of the Hermes profile.

## Install

Download and run `install-hermes.sh` from the same base URL as this document:

```bash
(
  set -e
  BCS_INSTALL_BASE_URL="${BCS_INSTALL_BASE_URL:-https://raw.githubusercontent.com/inclusionAI/Avernet/dev/src/bcs/docs/install-instructions}"
  installer="$(mktemp "${TMPDIR:-/tmp}/install-hermes.XXXXXX")"
  trap 'rm -f "$installer"' EXIT
  curl -fsSL "${BCS_INSTALL_BASE_URL}/install-hermes.sh" -o "$installer"
  printf '%s\n' "${HUMAN_TOKEN}" | bash "$installer" \
    --human-token-stdin \
    --bot-name "${BOT_NAME}" \
    --profile "${HERMES_PROFILE}" \
    --create-profile \
    --bcs-endpoint "${BCS_HTTP_ENDPOINT}" \
    --bcs-ws-url "${BCS_WS_URL}"
)
```

`--create-profile` clones a missing named profile from `default`, never
overwrites an existing profile, and the installer rejects a stored Bot-name
mismatch before registration.

The human token is passed only on stdin, never in the installer or Python
argument list. When running interactively, omit `--human-token-stdin` and the
installer securely prompts only if registration is needed. The installer
checks the profile and tools before registration, installs the connector under
`${XDG_DATA_HOME:-~/.local/share}/avernet/hermes-bcn`, creates an isolated
virtual environment, and starts the connector.

Existing valid `${HERMES_HOME}/bcn/session.json` credentials are reused. Do
not pass `--replace` unless the user explicitly asks to replace them. The
installer then requires an interactive confirmation before registration.

For China-hosted PyPI access, pass `--china-mirror` or set
`USE_CN_MIRROR=1`. An existing `PIP_INDEX_URL` always takes precedence. Set
`AVERNET_RAW_BASE_URL` only when an organization-controlled source mirror is
required. Override `BCS_INSTALL_BASE_URL` before running the example when the
installer is hosted by an organization-controlled mirror.

## Lifecycle

Use the connector-only Python environment installed by the script:

```bash
CONNECTOR_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/avernet/hermes-bcn"
"$CONNECTOR_HOME/venv/bin/python" "$CONNECTOR_HOME/hermes_bcn.py" status --profile "${HERMES_PROFILE}"
"$CONNECTOR_HOME/venv/bin/python" "$CONNECTOR_HOME/hermes_bcn.py" start --profile "${HERMES_PROFILE}"
"$CONNECTOR_HOME/venv/bin/python" "$CONNECTOR_HOME/hermes_bcn.py" stop --profile "${HERMES_PROFILE}"
```

`status` prints `running`, `stale`, or `stopped`. `start` repairs stale PID
files and is safe to repeat. `stop` signals only the recorded connector; that
connector terminates the private Dashboard child it owns.

If installation fails after registration, keep `session.json` and use the
resume command printed by the installer. Do not register another bot.
