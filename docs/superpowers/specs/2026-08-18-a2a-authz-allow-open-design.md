# A2A Authz Allow-Open Design

## Goal

Direct A2A chat delivery must remain available when the runtime `AuthzContext` builder cannot produce a context. A successful builder result is still injected into `params.extensions.authz_context`; a builder error means the direct `chat.send` is delivered without that extension.

## Background

BCS message-flow optionally calls `AuthzContextBuilderCoreService` before sending direct A2A chat frames. The builder owns grant selection and policy details, including new A2A authz grants and `originator_policy` runtime gating. Message-flow's responsibility is narrower: request a context, inject it when available, and route the outbound frame.

## Required behavior

- When the authz context builder is absent, preserve the existing legacy pass-through path.
- When the authz context builder returns `Ok(AuthzContext)`, preserve successful injection into `params.extensions.authz_context` on the outgoing `chat.send` frame.
- When the authz context builder returns `Err` during direct A2A chat delivery:
  - do not return the builder error from direct chat;
  - do not mark the direct chat run failed for that builder error;
  - continue normal delivery of the `chat.send` frame;
  - leave `params.extensions.authz_context` absent.
- Existing downstream hard blocks outside authz-context construction, such as outbound interceptor blocks and bot unavailable delivery failures, remain unchanged.

## Out of scope

This change does not modify:

- collaboration runtime;
- state-machine permission or state-machine runs;
- group flow authorization;
- friendship checks or visibility checks;
- domain authorization structs;
- service-api authorization contracts;
- stores or migrations;
- `bcs-authorization` core semantics;
- A2A grant-building policy semantics other than message-flow's response to builder failure.

## Compatibility and risk

This is an allow-open runtime delivery behavior for direct A2A chat only. Consumers that already receive `extensions.authz_context` when the builder succeeds continue to receive it. Consumers must tolerate the extension being absent when context construction fails.
