# Open API: Bot Access Check

## Summary
A public Open API operation that answers one question for an approved
integrating application: "does user U have access to Bot B, and at what
level?" It returns a yes/no answer plus the user's level on that Bot, so the
integrating app can make access decisions without copying Avernet's
collaboration rules.

This is a **transitional** operation built for one integration's interim
needs. It is not part of the documented public contract and is expected to be
removed once that integration moves to a long-term solution.

## Motivation
A customer integration needs to check whether its end users may use a given
Bot before sending them to it. Today the answer is only available through an
internal workbench route that relies on a logged-in browser session. The Open
API has no equivalent. Listing a Bot's editors is not a substitute: the caller
must already collaborate on the Bot, and the list leaves out the owner and
access that comes from Space membership.

## User Stories
- As an approved integrating application, I want to ask whether a named user
  has access to a named Bot, so that I can show or hide that Bot for them.
- As an approved integrating application, I want to know the user's level
  (none / member / admin / owner), so that I can tell "can use it" apart from
  "can manage it".
- As a deployment operator, I want to say exactly which applications may call
  this operation, so that the Bot's collaborator list cannot be read by every
  Open API client.

## Acceptance Criteria
- [ ] An application on the deployment's allowlist can ask about any user and
      any Bot. The caller names the Bot by its Bot id and entity id, and names
      the user to check. The answer says whether the user has access and gives
      the user's level on that Bot.
- [ ] Access is per Bot. A service Bot's stages (draft, verify, online) all
      share the same answer, so the operation takes no stage input.
- [ ] By default, "has access" means at least member level. The caller may
      ask for a higher bar (admin or owner) instead.
- [ ] Access and level use the same rules the rest of the product uses when it
      lets that user open the Bot: the owner is owner; explicit collaborators
      get their role; members of the Bot's Space get member access; a
      collaborator removed from the Space loses access right away.
- [ ] If no Bot matches the given Bot id and entity id, the answer is a normal
      success with "no access" and level "none". It is not an error.
- [ ] A caller that is not an allowlisted application is refused with a
      permission error. This includes a person calling directly and any
      application not on the list. The refusal is the same whichever Bot or
      user is asked about.
- [ ] The allowlist is part of the deployment configuration and ships empty,
      so nobody can call the operation until a deployment lists an app.
- [ ] If the access check itself cannot be completed (for example the
      collaborator data is unavailable), the caller gets an error, never a
      made-up "yes" or "no".
- [ ] The operation does **not** appear in the published Open API
      description. Approved integrators are told about it directly.

## In Scope
- One read-only operation answering for a single (user, Bot) pair.
- A deployment-configured allowlist of calling applications.
- Optional required-level input.

## Out of Scope
- Checking many users or many Bots in one call.
- Listing which Bots a user can access.
- Granting, requesting or changing access.
- Per-Bot or per-tenant restrictions on the allowlisted apps. An allowlisted
  app may ask about any Bot in the deployment.
- Changing the existing internal check-permission route.
- Per-stage access. Access is a property of the Bot, not of its runtimes.
- Publishing or documenting the operation in the Open API description.

## Open Questions
- None. The allowlist identifies applications by their application name, as
  carried on the caller's verified credential.
