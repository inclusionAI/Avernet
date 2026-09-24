# BCS Session Member Message Target Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow `message(action=send, channel=bcs, target=<current-session-actor>)` to send a directed @mention into the current BCS Session without enabling cross-Session delivery.

**Architecture:** Extend OpenClaw's transport-neutral target-resolution contract with the host-owned `currentSessionKey`, and preserve opaque provider metadata through the outbound send preparation path. The BCS plugin resolves exact Human/Bot membership from its existing `sessionKey` caches, normalizes transport to the current OpenClaw Session key, and prepares the message with a canonical `@<actor_id>` prefix. BCS core routing, persistence, Human Notify APIs, and database schema remain unchanged.

**Tech Stack:** TypeScript, OpenClaw channel-plugin contracts, OpenClaw outbound target/send pipeline, BCN WebSocket plugin, Mocha/egg-bin tests, npm build/lint, BCS mock WebSocket protocol tests.

**Specification:** `src/bcs/docs/superpowers/specs/2026-09-23-bcs-session-member-message-target-design.md`

**Repository note:** The host contract changes live in `/Users/zony/workspace/ai/openclaw` and the BCS plugin changes live in `/Users/zony/workspace/ai/Avernet`. Keep commits separate in the two Git repositories; do not copy OpenClaw implementation files into Avernet.

---

## File Map

### OpenClaw host repository

- Modify: `/Users/zony/workspace/ai/openclaw/src/channels/plugins/types.core.ts`
  - Add `currentSessionKey` to the target resolver input.
  - Add opaque `providerMetadata` to the resolved messaging target type.
  - Add the resolved target to `ChannelMessagePreparedSendPayloadContext`.
- Modify: `/Users/zony/workspace/ai/openclaw/src/infra/outbound/target-resolver.ts`
  - Accept and propagate `currentSessionKey`.
  - Preserve provider metadata when the plugin resolves a target.
- Modify: `/Users/zony/workspace/ai/openclaw/src/infra/outbound/targets-resolve-shared.ts`
  - No behavior change expected; verify the shared outbound target path does not erase the normalized target or metadata contract.
- Modify: `/Users/zony/workspace/ai/openclaw/src/infra/outbound/message-action-runner.ts`
  - Pass `input.sessionKey` through `resolveActionTarget()` into target resolution.
  - Pass the resolved target into outbound send execution.
- Modify: `/Users/zony/workspace/ai/openclaw/src/infra/outbound/outbound-send-service.ts`
  - Preserve the resolved target when calling `actions.prepareSendPayload`.
- Test: `/Users/zony/workspace/ai/openclaw/src/infra/outbound/target-resolver.test.ts`
  - Verify current-session context and provider metadata reach plugin resolvers.
- Test: `/Users/zony/workspace/ai/openclaw/src/infra/outbound/message-action-runner.core-send.test.ts`
  - Verify the message action passes the current session key and resolved target into send preparation.
- Test: `/Users/zony/workspace/ai/openclaw/src/infra/outbound/outbound-send-service.test.ts`
  - Verify provider metadata reaches `prepareSendPayload` and does not affect unrelated channels.

### Avernet / BCS repository

- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/src/inbound-handler.ts`
  - Reuse existing `sessionKey -> BCS Session ID` and `sessionKey -> participants` state.
  - Export a narrow session-member lookup helper if the new resolver module needs it.
- Create: `src/bcs/crates/plugins/openclaw-channel-bcn/src/session-member-target.ts`
  - Own exact actor membership validation and provider metadata construction.
  - Keep target conversion independent from WebSocket send details.
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/src/channel.ts`
  - Register the BCS target resolver.
  - Register the BCS send-payload preparation hook.
  - Keep `outbound.sendText` on the existing current-run/current-session path.
- Create: `src/bcs/crates/plugins/openclaw-channel-bcn/test/session-member-target.test.ts`
  - Cover Human/Bot member resolution, missing context, non-member rejection, and concurrent Session isolation.
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/test/index.test.ts`
  - Cover the plugin-level send path and the final `@actor_id` payload.
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/README.md`
  - Document the current-Session actor target syntax and its restrictions.
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/package.json`
  - Bump the plugin version from `1.0.24` to `1.0.25` when the host/plugin contract is released together.
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/package-lock.json`
  - Keep the package version metadata consistent with `package.json`.

---

## Task 1: Extend the OpenClaw Target Resolution Contract

**Repository:** `/Users/zony/workspace/ai/openclaw`

**Files:**
- Modify: `src/channels/plugins/types.core.ts`
- Modify: `src/infra/outbound/target-resolver.ts`
- Test: `src/infra/outbound/target-resolver.test.ts`

- [ ] **Step 1: Add the host session context and opaque metadata types.**

In `types.core.ts`, update the target-resolver callback contract so its input
includes the host-owned session context and its result can carry opaque provider
metadata. The `currentSessionKey` and `currentSenderActorId` fields belong on
the resolver input; `providerMetadata` belongs on the result and must be
documented as opaque to host routing logic.

The target-resolver callback must become:

```ts
resolveTarget?: (params: {
  cfg: OpenClawConfig;
  accountId?: string | null;
  input: string;
  normalized: string;
  preferredKind?: ChannelDirectoryEntryKind | "channel";
  currentSessionKey?: string | null;
  currentSenderActorId?: string | null;
}) => Promise<{
  to: string;
  kind: ChannelDirectoryEntryKind | "channel";
  display?: string;
  source?: "normalized" | "directory";
  providerMetadata?: Record<string, unknown>;
} | null>;
```

Do not make either context field required; all non-session and non-BCS channel
callers must remain source-compatible.

- [ ] **Step 2: Thread current-session and sender context through `resolveChannelTarget()`.**

In `target-resolver.ts`, add `currentSessionKey?: string | null` and
`currentSenderActorId?: string | null` to the `resolveChannelTarget()` and
internal `resolveMessagingTarget()` parameter objects. Pass both into
`maybeResolvePluginMessagingTarget()` and from there into
`resolver.resolveTarget()`.

The plugin call must include:

```ts
currentSessionKey: params.currentSessionKey,
currentSenderActorId: params.currentSenderActorId,
```

and the returned provider metadata must be copied into the host-owned
`ResolvedMessagingTarget` rather than discarded by `maybeResolvePluginMessagingTarget()`.
Do not pass model-controlled `params.args.to` as a substitute for the current
session key.

- [ ] **Step 3: Write the failing host contract test.**

In `target-resolver.test.ts`, add a test with a plugin resolver spy:

```ts
it("passes currentSessionKey and preserves provider metadata", async () => {
  mocks.resolveTarget.mockResolvedValue({
    to: "agent:main:bcs:current",
    kind: "group",
    source: "normalized",
    providerMetadata: {
      route: "bcs-session-member-mention",
      actorId: "human_378611",
    },
  });

  const result = await resolveMessagingTarget({
    cfg: {} as never,
    channel: "testchat" as never,
    input: "human_378611",
    currentSessionKey: "agent:main:bcs:current",
    currentSenderActorId: "bot-driver",
    plugin: pluginWithTargetResolver,
  });

  expect(mocks.resolveTarget).toHaveBeenCalledWith(
    expect.objectContaining({
      currentSessionKey: "agent:main:bcs:current",
      currentSenderActorId: "bot-driver",
    }),
  );
  expect(result).toMatchObject({
    ok: true,
    target: {
      providerMetadata: {
        route: "bcs-session-member-mention",
        actorId: "human_378611",
      },
    },
  });
});
```

Use the existing test plugin/mocks in that file rather than introducing a new
channel fixture.

- [ ] **Step 4: Run the focused test and confirm it fails before implementation.**

Run from `/Users/zony/workspace/ai/openclaw`:

```bash
pnpm vitest run src/infra/outbound/target-resolver.test.ts -t "passes currentSessionKey and preserves provider metadata"
```

Expected result: FAIL because the resolver callback does not yet receive
`currentSessionKey`/`currentSenderActorId` and the resolved target does not yet
expose provider metadata.

- [ ] **Step 5: Implement the minimal contract propagation.**

Update only the resolver types and call chain required by the test. Do not add
BCS-specific branching to OpenClaw core.

- [ ] **Step 6: Add the metadata-preservation normalization test.**

In `target-normalization.test.ts`, add a plugin resolver result containing
`providerMetadata` and assert that `maybeResolvePluginMessagingTarget()` returns
that field unchanged. This catches the existing normalization layer as a
separate loss point from `target-resolver.ts`.

- [ ] **Step 7: Run the focused tests and the complete target-resolver suite.**

```bash
pnpm vitest run \
  src/infra/outbound/target-resolver.test.ts \
  src/infra/outbound/target-normalization.test.ts
```

Expected result: all target-resolver and target-normalization tests PASS.

- [ ] **Step 8: Commit the host contract change.**

```bash
git add src/channels/plugins/types.core.ts \
  src/infra/outbound/target-resolver.ts \
  src/infra/outbound/target-normalization.ts \
  src/infra/outbound/target-resolver.test.ts \
  src/infra/outbound/target-normalization.test.ts
git commit -m "feat(openclaw): pass current session to target resolvers"
```

---

## Task 2: Preserve Resolved Target Metadata Through Message Send

**Repository:** `/Users/zony/workspace/ai/openclaw`

**Files:**
- Modify: `src/channels/plugins/types.core.ts`
- Modify: `src/infra/outbound/message-action-runner.ts`
- Modify: `src/infra/outbound/outbound-send-service.ts`
- Test: `src/infra/outbound/message-action-runner.core-send.test.ts`
- Test: `src/infra/outbound/outbound-send-service.test.ts`

- [ ] **Step 1: Add `resolvedTarget` to the prepared-send context.**

Change `ChannelMessagePreparedSendPayloadContext` so the plugin receives the
resolved target, including `providerMetadata`:

```ts
type ChannelMessagePreparedSendPayloadContext = {
  ctx: ChannelMessageActionContext;
  to: string;
  payload: ReplyPayload;
  resolvedTarget?: ResolvedMessagingTarget;
  replyToId?: string | null;
  replyToIdSource?: "explicit" | "implicit";
  threadId?: string | number | null;
};
```

Use the existing resolved-target type; do not duplicate the metadata shape in
`types.core.ts` and the outbound service.

- [ ] **Step 2: Pass `input.sessionKey` into action target resolution.**

In `message-action-runner.ts`, extend the internal helper signatures:

```ts
async function resolveActionTarget(params: {
  cfg: OpenClawConfig;
  channel: ChannelId;
  action: ChannelMessageActionName;
  args: Record<string, unknown>;
  accountId?: string | null;
  currentSessionKey?: string | null;
})
```

and:

```ts
async function resolveResolvedTargetOrThrow(params: {
  cfg: OpenClawConfig;
  channel: ChannelId;
  input: string;
  accountId?: string;
  preferredKind?: "group" | "user" | "channel";
  currentSessionKey?: string | null;
  currentSenderActorId?: string | null;
  validateResolvedTarget?: (target: ResolvedMessagingTarget) => string | undefined;
})
```

Pass `input.sessionKey` and `input.requesterSenderId` from `runMessageAction()`
when resolving `params.to` and `params.channelId`. The BCS feature consumes both
only for `params.to`; the existing `channelId` path must continue to work
unchanged.

- [ ] **Step 3: Pass the resolved target into `prepareSendPayload`.**

In `outbound-send-service.ts`, add `resolvedTarget?: ResolvedMessagingTarget`
to `OutboundSendContext` and the `executeSendAction()` input, then pass it here:

```ts
const payload = await prepareSendPayload({
  ctx: createChannelActionContext({ ctx: params.ctx, action: "send" }),
  to: params.to,
  payload: params.payload,
  resolvedTarget: params.resolvedTarget,
  replyToId: params.replyToId,
  replyToIdSource: params.replyToIdSource,
  threadId: params.threadId,
});
```

The value must be the same resolved target returned by `resolveActionTarget()`;
do not reconstruct metadata from `params.to`.

- [ ] **Step 4: Write failing message-action tests.**

Add a test to `message-action-runner.core-send.test.ts` that configures a target
resolver returning:

```ts
{
  to: "agent:main:bcs:current",
  kind: "group",
  source: "normalized",
  providerMetadata: {
    route: "bcs-session-member-mention",
    actorId: "human_378611",
  },
}
```

Assert that the resolver receives `currentSessionKey` and the send preparation
callback receives the same metadata.

Add a test to `outbound-send-service.test.ts` that asserts
`prepareSendPayload` receives:

```ts
expect.objectContaining({
  resolvedTarget: expect.objectContaining({
    providerMetadata: expect.objectContaining({
      route: "bcs-session-member-mention",
    }),
  }),
})
```

Also assert an unrelated channel with no metadata still receives the existing
payload shape.

- [ ] **Step 5: Run the new tests before implementation.**

```bash
cd /Users/zony/workspace/ai/openclaw
pnpm vitest run \
  src/infra/outbound/message-action-runner.core-send.test.ts \
  src/infra/outbound/outbound-send-service.test.ts \
  -t "currentSessionKey|provider metadata"
```

Expected result: FAIL because the action runner does not forward the session key
and the send service does not forward `resolvedTarget`.

- [ ] **Step 6: Implement the minimal propagation and run the focused suites.**

```bash
pnpm vitest run src/infra/outbound/message-action-runner.core-send.test.ts
pnpm vitest run src/infra/outbound/outbound-send-service.test.ts
```

Expected result: all tests PASS.

- [ ] **Step 7: Commit the host send-path change.**

```bash
git add src/channels/plugins/types.core.ts \
  src/infra/outbound/message-action-runner.ts \
  src/infra/outbound/outbound-send-service.ts \
  src/infra/outbound/message-action-runner.core-send.test.ts \
  src/infra/outbound/outbound-send-service.test.ts
git commit -m "feat(openclaw): preserve provider target metadata on sends"
```

---

## Task 3: Add BCS Session-Member Resolution Helpers

**Repository:** `/Users/zony/workspace/ai/Avernet`

**Files:**
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/src/inbound-handler.ts`
- Create: `src/bcs/crates/plugins/openclaw-channel-bcn/src/session-member-target.ts`
- Create: `src/bcs/crates/plugins/openclaw-channel-bcn/test/session-member-target.test.ts`

- [ ] **Step 1: Expose a narrow read-only session context lookup.**

In `inbound-handler.ts`, add a helper that returns only the data needed by the
resolver:

```ts
export interface BcsSessionMemberContext {
  bcsSessionId: string;
  participants: readonly string[];
}

export function resolveBcsSessionMemberContext(
  sessionKey: string,
): BcsSessionMemberContext | undefined {
  const bcsSessionId = sessionKeyToBcsSessionId.get(sessionKey);
  const participants = sessionTaskGroupInfo.get(sessionKey)?.participants;
  if (!bcsSessionId || !participants) return undefined;
  return { bcsSessionId, participants };
}
```

Do not expose the mutable Maps themselves. Preserve the existing write points in
`handleChatSend()` and `handleChatInject()`.

- [ ] **Step 2: Define the BCS provider metadata constants and result helper.**

In `session-member-target.ts`, create:

```ts
export const BCS_SESSION_MEMBER_MENTION_ROUTE = "bcs-session-member-mention" as const;

export type BcsSessionMemberTargetMetadata = {
  route: typeof BCS_SESSION_MEMBER_MENTION_ROUTE;
  bcsSessionId: string;
  actorId: string;
  actorKind: "human" | "bot";
};

export function isBcsSessionMemberTargetMetadata(
  value: unknown,
): value is BcsSessionMemberTargetMetadata {
  if (!value || typeof value !== "object") return false;
  const metadata = value as Record<string, unknown>;
  return metadata.route === BCS_SESSION_MEMBER_MENTION_ROUTE
    && typeof metadata.bcsSessionId === "string"
    && typeof metadata.actorId === "string"
    && (metadata.actorKind === "human" || metadata.actorKind === "bot");
}
```

Implement a pure resolver:

```ts
export function resolveSessionMemberTarget(params: {
  input: string;
  currentSessionKey?: string | null;
  context?: BcsSessionMemberContext;
  currentSenderActorId?: string | null;
}):
  | { ok: true; actorId: string; actorKind: "human" | "bot"; metadata: BcsSessionMemberTargetMetadata }
  | { ok: false; code: "context_unavailable" | "unknown_member" | "self_target" | "not_claimed" };
```

Rules:

- trim the input once;
- require a non-empty `currentSessionKey` and context;
- compare exact participant IDs only;
- classify `human_`-prefixed IDs as Human and all other participant IDs as Bot;
- reject a target equal to `currentSenderActorId` with `self_target`;
- return `not_claimed` for ordinary non-actor strings so existing BCS target
  behavior can continue through the generic resolver;
- treat `human_`-prefixed inputs as actor targets and return
  `unknown_member` when they are not in the current Session;
- never match display names or substrings.

- [ ] **Step 3: Write failing unit tests for the pure resolver.**

Cover these exact cases:

```ts
it("resolves a Human participant in the current Session", () => {
  expect(resolveSessionMemberTarget({
    input: "human_378611",
    currentSessionKey: "agent:main:bcs:session-a",
    context: {
      bcsSessionId: "group-1:session-a",
      participants: ["bot-driver", "human_378611"],
    },
  })).toMatchObject({
    ok: true,
    actorId: "human_378611",
    actorKind: "human",
  });
});

it("resolves a Bot participant in the current Session", () => {
  expect(resolveSessionMemberTarget({
    input: "bot-worker",
    currentSessionKey: "agent:main:bcs:session-a",
    context: {
      bcsSessionId: "group-1:session-a",
      participants: ["bot-driver", "bot-worker"],
    },
  })).toMatchObject({ ok: true, actorKind: "bot" });
});

it("rejects a member of another Session", () => {
  expect(resolveSessionMemberTarget({
    input: "human_2",
    currentSessionKey: "agent:main:bcs:session-a",
    context: {
      bcsSessionId: "group-1:session-a",
      participants: ["human_1"],
    },
  })).toEqual({ ok: false, code: "unknown_member" });
});

it("fails closed without current Session context", () => {
  expect(resolveSessionMemberTarget({ input: "human_1" })).toEqual({
    ok: false,
    code: "context_unavailable",
  });
});
```

Also test self-target rejection, `not_claimed` for an ordinary group-like
string, and exact-match behavior for a display-name-like substring.

- [ ] **Step 4: Run the tests before implementation.**

```bash
cd /Users/zony/workspace/ai/Avernet/src/bcs/crates/plugins/openclaw-channel-bcn
npm test -- --grep "current Session|another Session|self-target"
```

Expected result: FAIL because the new helper and test module do not exist yet.

- [ ] **Step 5: Implement the helper and run the focused test file.**

```bash
npm test -- --grep "session member"
```

Expected result: PASS for all new resolver tests.

- [ ] **Step 6: Commit the BCS context helper.**

```bash
cd /Users/zony/workspace/ai/Avernet
git add src/bcs/crates/plugins/openclaw-channel-bcn/src/inbound-handler.ts \
  src/bcs/crates/plugins/openclaw-channel-bcn/src/session-member-target.ts \
  src/bcs/crates/plugins/openclaw-channel-bcn/test/session-member-target.test.ts
git commit -m "feat(bcn): resolve current session member targets"
```

---

## Task 4: Register the BCS Target Resolver and Prepare @Mentions

**Repository:** `/Users/zony/workspace/ai/Avernet`

**Files:**
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/src/channel.ts`
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/src/session-member-target.ts`
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/test/session-member-target.test.ts`
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/test/index.test.ts`

- [ ] **Step 1: Register `messaging.targetResolver` in `createBcsPlugin()`.**

Add the BCS messaging surface beside the existing config/security/outbound
surfaces:

```ts
messaging: {
  targetResolver: {
    resolveTarget: async ({ input, currentSessionKey, currentSenderActorId }) => {
      const context = currentSessionKey
        ? resolveBcsSessionMemberContext(currentSessionKey)
        : undefined;
      const result = resolveSessionMemberTarget({
        input,
        currentSessionKey,
        currentSenderActorId,
        context,
      });
      if (!result.ok) {
        if (result.code === "not_claimed") return null;
        if (!currentSessionKey && result.code === "context_unavailable") return null;
        throw new Error(`BCS session member target ${result.code}: ${input}`);
      }
      return {
        to: currentSessionKey!,
        kind: "group" as const,
        display: result.actorId,
        source: "normalized" as const,
        providerMetadata: result.metadata,
      };
    },
  },
},
```

The adapter must throw a stable BCS error when the input looks like a
session-member target and the current context is unavailable or the actor is not
a current participant. Throwing is intentional: it stops the generic resolver from
falling through to a global directory lookup. If no current Session context is
provided at all, preserve the existing generic unknown-target behavior for
backward compatibility.

- [ ] **Step 2: Add the BCS payload preparation hook.**

Register `actions.prepareSendPayload` and only transform payloads with the exact
metadata route:

```ts
prepareSendPayload: async ({ payload, resolvedTarget }) => {
  const metadata = resolvedTarget?.providerMetadata;
  if (!isBcsSessionMemberTargetMetadata(metadata)) {
    return payload;
  }

  const text = payload.text ?? "";
  const prefix = `@${metadata.actorId}`;
  const nextText = text.startsWith(`${prefix} `) || text === prefix
    ? text
    : `${prefix} ${text}`;

  return { ...payload, text: nextText };
},
```

Preserve media and attachment fields. If the payload has no text but has media,
use the same BCS attachment fallback behavior already used by the plugin rather
than dropping the payload. Keep the prefix operation idempotent.

- [ ] **Step 3: Keep `outbound.sendText` unchanged except for compatibility assertions.**

The resolved `to` must be the current OpenClaw session key, so the existing code
continues to work:

```ts
const runId = resolveActiveRunId(to);
const sessionId = resolveBcsSessionIdFromSessionKey(to);
```

Do not add a second direct-send branch for `human_<id>`.

- [ ] **Step 4: Add failing plugin tests.**

Add tests that instantiate `createBcsPlugin()` with test account/client state and
assert:

1. `targetResolver.resolveTarget({ input: "human_1", currentSessionKey })`
   returns `to === currentSessionKey` and Human metadata.
2. A Bot participant returns Bot metadata.
3. A non-member returns no resolved target and does not call any global lookup.
4. `prepareSendPayload` prefixes `@human_1` exactly once.
5. The prepared payload retains `mediaUrl`, `mediaUrls`, and presentation fields.
6. The existing `outbound.sendText` receives the current Session key rather than
   `human_1` when the resolved target is used.

- [ ] **Step 5: Run the focused plugin tests before implementation.**

```bash
cd /Users/zony/workspace/ai/Avernet/src/bcs/crates/plugins/openclaw-channel-bcn
npm test -- --grep "session member target|session member mention"
```

Expected result: FAIL until the plugin surfaces are registered.

- [ ] **Step 6: Implement the plugin surfaces and run the focused tests.**

```bash
npm test -- --grep "session member target|session member mention"
```

Expected result: PASS.

- [ ] **Step 7: Commit the BCS plugin behavior.**

```bash
cd /Users/zony/workspace/ai/Avernet
git add src/bcs/crates/plugins/openclaw-channel-bcn/src/channel.ts \
  src/bcs/crates/plugins/openclaw-channel-bcn/src/session-member-target.ts \
  src/bcs/crates/plugins/openclaw-channel-bcn/test/session-member-target.test.ts \
  src/bcs/crates/plugins/openclaw-channel-bcn/test/index.test.ts
git commit -m "feat(bcn): route session member message targets"
```

---

## Task 5: Add Cross-Session and WebSocket Integration Coverage

**Repository:** `/Users/zony/workspace/ai/Avernet`

**Files:**
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/test/index.test.ts`
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/test/gateway-protocol.test.ts` if the existing protocol harness is the narrowest integration point
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/test/compat/mock_bcs.mjs` only if the mock must record the outgoing chat event

- [ ] **Step 1: Add two isolated Session fixtures.**

Create test state with:

```ts
const sessionA = {
  sessionKey: "agent:main:bcs:session-a",
  bcsSessionId: "group-1:session-a",
  participants: ["bot-driver", "human_1"],
};
const sessionB = {
  sessionKey: "agent:main:bcs:session-b",
  bcsSessionId: "group-1:session-b",
  participants: ["bot-driver", "human_1"],
};
```

Register both using `rememberTaskToolSession()` and assign distinct active run
IDs. The test must prove that the same actor ID resolves independently in both
Sessions.

- [ ] **Step 2: Assert the emitted event uses the current Session.**

Invoke the plugin send flow for `target=human_1` in Session A and assert:

```ts
expect(event.sessionId).toBe("group-1:session-a");
expect(event.runId).toBe("run-a");
expect(event.content).toContain("@human_1");
```

Repeat for Session B and assert the event uses `group-1:session-b` and `run-b`.

- [ ] **Step 3: Add negative cross-Session tests.**

Attempt `target=human_2` in Session A when `human_2` exists only in Session B.
Assert that:

- target resolution fails;
- no WebSocket chat event is emitted;
- the error identifies a current-session membership failure;
- no fallback directory or `route.resolve` request is sent.

- [ ] **Step 4: Add retry/idempotency coverage.**

Send a payload already beginning with `@human_1` and assert the emitted text has
one prefix, not `@human_1 @human_1 ...`. Repeat the preparation step with the
same resolved metadata to represent a retry.

- [ ] **Step 5: Run the complete BCN plugin suite.**

```bash
cd /Users/zony/workspace/ai/Avernet/src/bcs/crates/plugins/openclaw-channel-bcn
npm test
```

Expected result: all plugin tests PASS.

- [ ] **Step 6: Commit the integration coverage.**

```bash
cd /Users/zony/workspace/ai/Avernet
git add src/bcs/crates/plugins/openclaw-channel-bcn/test/index.test.ts \
  src/bcs/crates/plugins/openclaw-channel-bcn/test/gateway-protocol.test.ts \
  src/bcs/crates/plugins/openclaw-channel-bcn/test/compat/mock_bcs.mjs
git commit -m "test(bcn): cover session member target isolation"
```

Only include files that actually changed; do not stage unrelated generated
`dist/` output unless the repository's release process requires it.

---

## Task 6: Document the Target Syntax and Bump the Plugin Package

**Repository:** `/Users/zony/workspace/ai/Avernet`

**Files:**
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/README.md`
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/package.json`
- Modify: `src/bcs/crates/plugins/openclaw-channel-bcn/package-lock.json`

- [ ] **Step 1: Document the supported target form.**

Add a section stating:

```text
message(action=send, channel=bcs, target=human_<id>)
```

is supported only when `human_<id>` is an exact participant of the current BCS
Session. Bot actor IDs follow the same rule. The target is converted to an
@mention in the current Session; it is not a private message and cannot target
another Session.

Document these failure cases:

- no current BCS Session context;
- actor is not in the current Session;
- active BCS run/session context is unavailable.

- [ ] **Step 2: Bump the package version.**

Change the plugin version from `1.0.24` to `1.0.25` in `package.json` and the
root package metadata in `package-lock.json`. Do not alter dependency versions.

- [ ] **Step 3: Run package lint and build checks.**

```bash
cd /Users/zony/workspace/ai/Avernet/src/bcs/crates/plugins/openclaw-channel-bcn
npm run lint
npm run prepublishOnly
```

Expected result: lint and TypeScript packaging complete without errors.

- [ ] **Step 4: Commit documentation and package metadata.**

```bash
cd /Users/zony/workspace/ai/Avernet
git add src/bcs/crates/plugins/openclaw-channel-bcn/README.md \
  src/bcs/crates/plugins/openclaw-channel-bcn/package.json \
  src/bcs/crates/plugins/openclaw-channel-bcn/package-lock.json
git commit -m "docs(bcn): document session member message targets"
```

---

## Task 7: Run Cross-Repository Verification

**Repositories:** `/Users/zony/workspace/ai/openclaw` and `/Users/zony/workspace/ai/Avernet`

- [ ] **Step 1: Run the OpenClaw focused contract suites.**

```bash
cd /Users/zony/workspace/ai/openclaw
pnpm vitest run \
  src/infra/outbound/target-resolver.test.ts \
  src/infra/outbound/message-action-runner.core-send.test.ts \
  src/infra/outbound/outbound-send-service.test.ts
```

Expected result: PASS, including the new current-session and metadata cases.

- [ ] **Step 2: Run the complete BCN plugin checks.**

```bash
cd /Users/zony/workspace/ai/Avernet/src/bcs/crates/plugins/openclaw-channel-bcn
npm run lint
npm test
npm run prepublishOnly
```

Expected result: all checks PASS.

- [ ] **Step 3: Run the BCS protocol-adjacent Rust tests.**

From `/Users/zony/workspace/ai/Avernet`:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-ws
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-message-flow
```

Expected result: existing BCS WebSocket and message-flow contract tests PASS.
Do not run `cargo fmt` or `cargo fmt --all`.

- [ ] **Step 4: Run the architecture and repository checks required by the touched boundaries.**

```bash
cd /Users/zony/workspace/ai/Avernet
scripts/install_git_hooks.sh
```

Then run the closest BCS CI checks for plugin/API boundary and protocol
compatibility that are available locally:

```bash
cd src/bcs
scripts/ci/check-protocol-compat.sh
scripts/ci/check-import-rules.sh
```

If a script requires unavailable external infrastructure, record the exact
command and reason in the final validation notes; do not weaken the check.

- [ ] **Step 5: Verify file-size and working-tree hygiene.**

```bash
cd /Users/zony/workspace/ai/Avernet
wc -l \
  src/bcs/crates/plugins/openclaw-channel-bcn/src/channel.ts \
  src/bcs/crates/plugins/openclaw-channel-bcn/src/inbound-handler.ts \
  src/bcs/crates/plugins/openclaw-channel-bcn/src/session-member-target.ts

git status --short
```

Confirm no modified source file exceeds 1,000 lines, no generated cache or
runtime state is staged, and no private endpoint or credential has been added.

- [ ] **Step 6: Perform the manual smoke scenario.**

With a BCS Session containing `human_378611`, invoke:

```text
message(action=send, channel=bcs, target=human_378611)
```

with a non-empty message. Confirm:

1. no generic `Unknown target` error is returned;
2. the event is sent to the current BCS Session;
3. the visible text contains exactly one `@human_378611`;
4. a target outside the current Session fails without an emitted chat event;
5. the Human Mention Notify path receives the existing actor ID when enabled.

- [ ] **Step 7: Record the final verification result in the implementation PR.**

Include separate command/result lines for the OpenClaw repository and Avernet
repository. State explicitly which cross-repository checks were not run and why.

---

## Commit and Review Order

Use this order to keep each change reviewable:

1. OpenClaw target-resolution contract and tests.
2. OpenClaw send metadata propagation and tests.
3. BCS session-member pure resolver and tests.
4. BCS plugin target resolver and payload preparation.
5. BCS cross-Session/WebSocket integration tests.
6. BCS documentation and package version.
7. Cross-repository verification and review.

The OpenClaw commits must land before publishing the BCS plugin version that
requires the new resolver context and metadata preservation. If deployment is
not atomic, retain the fail-closed behavior and do not advertise the new target
syntax until both sides are available.
