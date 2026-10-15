# Public API — acli-managed CLIs on a bot

## Summary

Give API callers create, read, update and delete control over the acli-managed
command-line tools a bot can use. A caller can find tools in the acli market,
add one to a bot and have it usable right away, change whose identity it runs
under, and remove it. New tools need no platform release: anything published
to the acli market can be added.

## Motivation

Today a bot's acli-managed CLIs come only from the deployment's default
profile: every matching bot gets the same tools (currently Dataphin and
DeepInsight), and nobody can add or remove one. Adding a tool to that profile
means editing configuration in this repository and shipping a release. The
public API exposes only one piece of the lifecycle — switching a tool between
owner and caller identity. Removal exists only on an internal route, and it
revokes permission without removing the tool from the container.

Teams want to equip individual bots with the specific internal tools their work
needs, through the same public API they already use for a bot's MCP servers,
without waiting for a release each time a new tool is published.

## Terms

- **acli market** — the internal CLI market that the `acli` client searches and
  installs from. It is the only catalog: the platform keeps no list of its own.
- **Grant** — the bot's recorded permission to use a tool, held in AgentPass.
  AgentPass is the single source of truth for which tools a bot has.
- **Installed** — the tool's program is present and runnable in the bot's
  container. Installed programs live on the container's persisted storage, so
  they survive restarts.
- **Default tool** — a tool the deployment's default profile gives every
  matching bot; the platform re-adds it to AgentPass at every container start.

## User Stories

- As a bot owner, I want to search the acli market from my bot, so that I know
  what I can add.
- As a bot owner, I want to list the tools my bot has, with which identity each
  runs under and whether it is a default, so that I know what the bot can do.
- As a bot owner, I want to add a market tool to my bot and use it without
  restarting the bot.
- As a bot owner, I want to switch a tool between running as me and running as
  the person chatting with the bot.
- As a bot owner, I want to remove a tool, so that the bot can no longer run it.

## Acceptance Criteria

**Search the market**
- [x] Searching by keyword returns a page of acli market tools, each with its
      market name, command name, description and latest version, as reported
      by acli; the caller can request further pages.
- [x] Search requires a running bot container, because acli runs there.

**List a bot's tools**
- [x] Returns every tool in the bot's AgentPass grant, whether or not it was
      added through this API, each with code, name, description, identity mode
      (`owner` or `caller`) and a `default` flag.
- [x] `default` is `true` only for tools the bot's default profile provides;
      it is computed when the list is read and never stored.

**Add**
- [x] Adding a market tool grants it to the bot with identity `owner`, installs
      it in the running container, and succeeds only if both succeed.
- [x] If the install fails, the grant is removed again and the add returns an
      error saying why. The bot ends up as it was before the request.
- [x] Adding to a bot with no running container is refused, and nothing
      changes.
- [x] An add succeeds only once AgentPass reads the grant back. A write that
      is accepted but not kept (a Passport that stores no scope, or a
      concurrent whole-scope write that overwrote it) uninstalls the program
      again and returns an error.
- [x] Tools are identified by their acli market name (for example
      `deepinsight-cli`), which is also the code stored in AgentPass. Passing
      another alias, such as the command name `di`, is refused with an error
      naming the market name, and nothing changes.
- [x] Adding a name the acli market does not know is refused, and nothing
      changes.
- [x] Adding a tool the bot already has is refused as a conflict, and nothing
      changes.
- [x] Adding a tool never changes the bot's other tools, its MCP servers, or
      any identity mode already set.

**Update identity**
- [x] Switching a tool between `owner` and `caller` behaves exactly as the
      existing identity switch does today: same permission checks, same
      results, same rollback when the platform update fails.

**Remove**
- [x] Removing a tool revokes its grant, deletes its program from the
      container, and succeeds only if both succeed.
- [x] If the program cannot be deleted, the grant is restored with the same
      identity mode, and the remove returns an error saying why. When the
      container's answer never arrives (timeout, unreachable), the tool stays
      revoked and the error names the state `revoked_install_unknown`, since
      re-granting could authorize a tool that is already gone.
- [x] Removing a default tool is refused with an error that says it is a
      default, and nothing changes.
- [x] Removing a tool the bot does not have is refused as not found.
- [x] Removing a granted tool that was never installed through this API (for
      example, one granted at bot creation) revokes the grant and succeeds;
      there is no program of ours to delete.
- [x] After a successful remove, re-adding the same tool starts again at
      identity `owner` — unless the tool was the bot's last `caller`
      resource: the platform never lets a bot fall back from `caller` to
      `owner`, so the override is kept and a re-add comes back as `caller`.
- [x] Removing a tool never changes the bot's other tools or MCP servers.

**Rollback failures**
- [x] If an add or remove has to undo its first step and the undo itself fails,
      the response says exactly which state the bot was left in, and the event
      is logged with enough detail to repair it by hand.

**All operations**
- [x] Only a caller allowed to change the bot may add, remove or update; the
      permission rules match the existing identity switch.
- [x] Bots whose engine cannot run acli get a clear "not supported" error.
- [x] No response or log carries tokens, credentials, or raw tool output that
      has not been masked.

## In Scope

- Public API operations for: market search, a bot's tool listing, add,
  identity update, and remove.
- Installing and uninstalling in a running bot's container at request time.
- Keeping the existing identity switch working at its current address.

## Out of Scope

- Reinstalling tools after a container restart: installed programs sit on
  persisted storage, which the engine container keeps across restarts.
- A platform-side catalog, allowlist, or any platform-side record of tools
  beyond AgentPass.
- Migrating tools that were granted outside this API; they are listed and can
  be managed, and their owners will move to this API separately.
- Letting a bot opt out of a default tool.
- The separate upload-a-binary tool mechanism (bot configuration manifest
  "CLI tools"). It is unchanged and unrelated.
- User-interface changes.

## Open Questions

- **Who may add and remove?** Default: the same rule as the identity switch —
  the owner of an active service bot. Collaborator admins are refused.
