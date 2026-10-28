# Platform-owned UI extensions

Public defaults keep the editor and conversation UI unchanged. Deployment composition
may opt into the following capabilities without adding platform policy to Open Core.

## Editor panels

`getBotEditorPanels` returns an empty array by default. Extensions provide an ID,
label, icon and panel. IDs must be unique lowercase identifiers and cannot shadow
built-in tabs. Panels receive the explicit Bot, owner, space, service mode,
runtime stage and editability context. Missing owner or space shows an unavailable
state rather than inferring a target from the viewer. Context changes reset the tab
and mounted panel state. Lazy panels render inside a Suspense boundary.

## Conversation composer

`getConversationComposer` defaults to null. The optional render-prop decorator can
extend commands, render an input header and wrap sending. The Host resets its state
when Bot, owner, session, space, type or runtime stage changes. Missing identity,
missing session and desktop targets retain the original composer. Extensions must
preserve existing command and submit-context semantics, including file references.
No new server message fields or platform-specific commands are defined here.

## Opt-in request limits and navigation

`BackendRequestOptions.maxResponseBytes` bounds streamed preview reads and cancels
the reader when finished or oversized. Existing requests remain unlimited unless
explicitly opted in. Bounded reads keep `responseType` semantics for caller-facing
data (text previews stay raw text) while JSON authentication and error envelopes
are still parsed for inspection; HTTP status handling (e.g. a 401 login prompt)
wins over the size limit, and combining the limit with `responseType: 'blob'` is
rejected.

A navigation item can set `placement: 'last'` to follow ordinary items within its
section. Items without it retain existing ordering.

## Verification

Conformance tests live in `test/capabilities`, `test/services/backendApi` and
`test/shell`. Deployment-specific business adapters and tests belong downstream.
