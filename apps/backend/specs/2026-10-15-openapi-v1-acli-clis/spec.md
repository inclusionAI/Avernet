# Public API — acli-managed CLIs on a bot

## Summary

Give API callers create, read, update and delete control over the acli-managed
command-line tools a bot can use. A caller can see which tools are available,
add one to a bot and have it usable right away, change whose identity it runs
under, and remove it — instead of the set being fixed by deployment
configuration.

## Motivation

Today a bot's acli-managed CLIs come only from the deployment's default
profile: every matching bot gets the same tools (currently Dataphin and
DeepInsight) at container start, and nobody can add or remove one. The public
API exposes only one piece of the lifecycle — switching a tool between owner
and caller identity. Removal exists only on an internal route, and it revokes
permission without removing the tool from the container.

Teams want to equip individual bots with the specific internal tools their work
needs, through the same public API they already use for a bot's MCP servers.

## Terms

- **Catalog** — the deployment-curated list of tools the platform knows how to
  install. Only catalog tools can be added through this API.
- **Default tool** — a catalog tool that the deployment's profile gives every
  matching bot automatically.
- **Grant** — the bot's recorded permission to use a tool. It lives on the
  platform and survives container restarts.
- **Installed** — the tool's program is present and runnable inside the bot's
  running container.

## User Stories

- As an API caller, I want to list the tools in the catalog, so that I know
  what I can add to a bot.
- As a bot owner, I want to list the tools my bot has, with whether each is a
  default and which identity it runs under, so that I know what the bot can do.
- As a bot owner, I want to add a catalog tool to my bot and use it without
  restarting the bot.
- As a bot owner, I want to switch a tool between running as me and running as
  the person chatting with the bot.
- As a bot owner, I want to remove a tool I added, so that the bot can no longer
  run it.

## Acceptance Criteria

**Catalog**
- [ ] Listing the catalog returns every catalog tool with its code, display
      name and description, and nothing about install commands or download
      locations.

**List a bot's tools**
- [ ] Returns every tool the bot holds a grant for, each with code, name,
      description, identity mode (`owner` or `caller`) and a `default` flag.
- [ ] Tools the bot holds that are not in the catalog (for example, ones
      granted at bot creation) are listed too, marked as not managed by this
      API.

**Add**
- [ ] Adding a catalog code grants it to the bot with identity `owner`, then
      installs it in the bot's running container before responding.
- [ ] The response reports the grant and, separately, the install outcome:
      `installed`, `failed` (with a reason safe to show the caller), or
      `not_running` when the bot has no running container.
- [ ] A failed install leaves the grant in place; repeating the same add
      retries the install and changes nothing else.
- [ ] Adding a tool the bot already has is not an error; it re-runs the
      install and reports the outcome.
- [ ] Adding a code that is not in the catalog is refused, and nothing changes.
- [ ] Adding a tool never changes the bot's other tools, its MCP servers, or
      any identity mode already set.

**Update identity**
- [ ] Switching a tool between `owner` and `caller` behaves exactly as the
      existing identity switch does today: same permission checks, same
      results, same rollback when the platform update fails.

**Remove**
- [ ] Removing a tool revokes its grant and deletes its program from the
      running container; afterwards the bot cannot run it.
- [ ] Removing a default tool is refused with an error that says it is a
      default, and nothing changes.
- [ ] Removing a tool the bot does not have is refused as not found.
- [ ] If the grant is revoked but the program cannot be deleted, the response
      says so; the tool is still no longer authorized.
- [ ] Removing a tool never changes the bot's other tools or MCP servers.

**All operations**
- [ ] Only a caller allowed to change the bot may add, remove or update; the
      permission rules match the existing identity switch.
- [ ] No response or log carries tokens, credentials, or raw tool output that
      has not been masked.

## In Scope

- Public API operations for: catalog listing, a bot's tool listing, add,
  identity update, and remove.
- Installing and uninstalling in a running bot's container at request time.
- Keeping the existing identity switch working at its current address.

## Out of Scope

- Reinstalling tools after a container restart. The existing start-up installer
  owns that. It is currently failing in production (its copy of the
  deployment's tool list has drifted from the platform's), and fixing it is
  tracked separately by another team. Until that fix ships, a tool added
  through this API keeps its grant across a restart but must be re-added to be
  runnable again.
- Adding tools that are not in the catalog, or editing the catalog through the
  API. New catalog tools are added by changing deployment configuration.
- Letting a bot opt out of a default tool.
- The separate upload-a-binary tool mechanism (bot configuration manifest
  "CLI tools"). It is unchanged and unrelated.
- User-interface changes.

## Open Questions

- **Which bots may hold catalog tools?** Default: only bots whose engine and
  template match a default profile (today: OpenClaw, and Claude Code except the
  `normalCC` template), because only those containers have the installer.
  Others are refused.
- **Who may add and remove?** Default: the same rule as the identity switch —
  the owner of an active service bot. Collaborator admins are refused.
