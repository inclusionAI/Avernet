# Resolve MCP URL and user-explicit Headers independently

Status: accepted for the REL20261009 scoped-config URL extension.

ADR 0015 established per-name merging of user-default and Bot-explicit Headers,
but excluded inherited user Headers whenever a Bot selected a custom URL. That
exception conflated two independent product controls: where a Bot connects and
which user-declared Headers it sends. This ADR supersedes only that custom-URL
exception in ADR 0015.

For a registered MCP server, the effective URL is the Bot's explicit URL,
otherwise the user's global URL, otherwise the MCP Center-selected endpoint.
The effective **user-explicit** Header map is always the case-insensitive merge
of user defaults and Bot overrides, with the Bot value winning. Neither URL
source changes this Header scope. In particular, a Bot custom URL receives
user-default Headers unless overridden by that Bot. A URL rule does not install
an MCP.

Platform default Headers, platform-managed credential bindings, and the legacy
`api_key` are separate from user-explicit Header rules. An arbitrary custom URL
must not receive those Center-bound values automatically. A user who explicitly
configured `Authorization` or another credential-like Header as a normal
Header has declared that it follows the effective URL; product UI must make
that consequence visible when saving a custom URL. Container-wide mcporter
`headerPolicies` are host-matched runtime policy and require separate review;
removing credentials from a server entry alone does not disable them.

The scoped-config aggregate accepts `url_rules` independently of the required
complete `params` Header snapshot. Omitted `url_rules` preserves every
existing URL rule for compatibility with older clients; `[]` clears them;
a nonempty list replaces the complete user/Bot URL snapshot. One user-default
URL and at most one Bot-specific URL per Bot are allowed. Writes preserve
unrelated fields in both existing tables and project accepted state
best-effort. No new table or DDL is required.
