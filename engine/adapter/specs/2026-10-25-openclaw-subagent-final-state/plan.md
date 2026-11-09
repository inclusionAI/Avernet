# Plan: OpenClaw subagent completion state

1. Add regression tests for rejected spawns and parent-session resumption.
2. Parse the nested `sessions_spawn` result and count only complete accepted
   child identities.
3. Record when a foreground final is deferred; on a trusted same-session
   continuation, consume one pending child and authorize that continuation run.
4. Run the focused gateway-client tests, the engine CI gate, static checks, and
   an independent review of stream isolation and terminal-state behavior.
5. Rebase the topic branch on the latest GitHub `dev`, push only to GitHub, and
   open a PR targeting `dev`.
