---
name: bcs-coordination
description: Public BCS multi-bot coordination skill. Uses bcs-cli to call a configured BCS HTTP endpoint for bot discovery, group creation, routing, and session operations.
---

# BCS Multi-Bot Coordination

Use this skill when a task needs multiple bots to collaborate, exchange context,
create a group, ask another bot for help, or route messages through BCS.

## Requirements

- `bcs-cli` is available on `PATH`.
- `BOT_DATA_DIR` points to the bot data directory.
- `BCS_API_BASE_URL` points to the BCS HTTP API, for example `http://127.0.0.1:21000`.
- A bot token is available through `BCN_BOT_TOKEN` or `$BOT_DATA_DIR/.bcs/session.json`.

## Session File

`$BOT_DATA_DIR/.bcs/session.json` may contain:

```json
{
  "bot_uuid": "bot-demo",
  "token": "replace-with-bot-token",
  "api_base_url": "http://127.0.0.1:21000"
}
```

## Command Pattern

```bash
BOT_DATA_DIR="$BOT_DATA_DIR" bcs-cli --url "$BCS_API_BASE_URL" <subcommand> [options]
```

Do not hard-code deployment URLs or credentials in prompts, scripts, or skill
files. Use environment variables or the session file instead.

## Common Commands

```bash
# Discover bots
bcs-cli --url "$BCS_API_BASE_URL" discover --query "database"

# Ask for group help
bcs-cli --url "$BCS_API_BASE_URL" request-group-help --topic "Investigate a production issue"

# Create a group directly when participants are known
bcs-cli --url "$BCS_API_BASE_URL" create-group \
  --driver "bot-coordinator" \
  --participants "bot-dba,bot-security" \
  --topic "Incident analysis"

# Create a manager-worker group; all participants are assigned the worker role
bcs-cli --url "$BCS_API_BASE_URL" create-group \
  --manager "bot-manager" \
  --participants "bot-worker-1,bot-worker-2" \
  --topic "Parallel implementation"

# Send a direct message to a bot
bcs-cli --url "$BCS_API_BASE_URL" chat \
  --bot-uuid "bot-dba" \
  --message "Please inspect the migration plan."
```

## References

Read the scenario-specific references under `references/` before choosing a
command:

- `bot.md`: bot discovery and direct bot chat
- `group.md`: group creation and group lifecycle
- `access-control.md`: friend requests and visibility
- `fuse.md`: multi-perspective coordination
- `session.md`: session-level operations
