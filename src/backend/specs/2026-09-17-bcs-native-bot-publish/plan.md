# BCS-Native Bot Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `POST /openapi/v1/collaboration/bots/{bot_uuid}/public` work for BCS-native bots by running the whole publication flow on the caller's BCS V1 identity instead of the Provider Admin channel.

**Architecture:** The Backend re-addresses the inbound gateway principal (`resign_principal_for_bcn`) for the synchronous path and mints a 60-second owner principal for the identity-less antprocess callback. All BCS reads/writes go to `GET/PATCH /openapi/v1/collaboration/bots/{bot_id}`, whose application layer enforces `created_by == caller`. No BCS code changes. A pending-approval-block state machine plus corroboration with antprocess guards the callback.

**Tech Stack:** Python 3.12 / FastAPI / injector DI / pytest; PyJWT (HS256); BCS Rust V1 OpenAPI (unmodified); singlebox shell scripts.

**Spec:** `src/backend/specs/2026-09-17-bcs-native-bot-publish/spec.md`

**Repository rules that bind this plan:** read `src/bcs/AGENTS.md` before touching BCS (this plan does not); do not run global formatters; a source file must not exceed 1,000 lines, so all new logic lands in new focused modules and edits to `bot_public_service.py` (already 1,868 lines, pre-existing) stay surgical.

---

## File Structure

**New files**

| Path | Responsibility |
| --- | --- |
| `src/backend/src/agentclaw/community/core/gateway_principal/minter.py` | Mint the one identity-less callback credential; no config reads |
| `src/backend/src/agentclaw/community/core/bot_public/publication_channel.py` | Approval-block constants, block parsing, decision validation, merge, and the verify-and-rewrite `friend_ext` writer over the V1 channel |
| `src/backend/tests/community/core/gateway_principal/test_minter.py` | Minter unit tests incl. verifier round-trip |
| `src/backend/tests/community/core/bot_public/test_publication_channel.py` | Block parsing / validation / merge / write-loop tests |

**Modified files**

| Path | Change |
| --- | --- |
| `src/backend/src/agentclaw/community/utils/gateway_principal_config.py` | Add `mint_owner_principal_for_bcs(owner_id)` |
| `src/backend/src/agentclaw/community/core/bot_management/services/bcn_service.py` | Add `get_bot_as_user` / `patch_bot_as_user` + typed 404 |
| `src/backend/src/agentclaw/community/adapters/http/openapi_v1/dependencies.py` | Add `PrincipalTokenDep` |
| `src/backend/src/agentclaw/community/adapters/http/openapi_v1/collaboration_bots/router.py` | Pass the raw token into `public_bcs_bot` |
| `src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py` | Read fallback, sync-write migration, callback state machine + corroboration, orphan prevention |
| `src/backend/src/agentclaw/community/di/modules/bot_public_module.py` | Inject `resign_principal` / `mint_principal` callables |
| `src/backend/src/agentclaw/community/plugin_api/approval_workflow.py` | Typed `query_approval_status` result contract |
| `src/backend/src/agentclaw/community/plugins/community/approval_workflow.py` | Shaped result |
| `src/backend/src/agentclaw/community/plugins/local/antprocess.py` | Shaped result |
| `src/backend/src/agentclaw/community/adapters/http/openapi_v1/admission.py` | `OPEN` → `REFUSED` for the publish route |
| `src/backend/tests/community/contracts/test_approval_workflow.py` | Conformance for the new query contract |
| `src/backend/tests/community/adapters/http/openapi_v1/test_app_only_refusals.py` | App-only refusal case |
| `src/backend/tests/community/core/bot_public/test_bot_public_service.py` | `_make_service` gains the two callables; new behaviour tests |
| `src/backend/tests/community/endpoints/test_openapi_public_bcs.py` | Human-row / created_by / admission cases |
| `scripts/ci/singlebox_coverage.sh` | One principal signing key for Backend, Gateway and BCS |

**Pre-flight (do once, before Task 1)**

- [ ] Run `scripts/install_git_hooks.sh` in this worktree (AGENTS.md requirement).
- [ ] Confirm the worktree is clean: `git status --short`.
- [ ] Confirm the test baseline: `cd src/backend && uv run pytest tests/community/core/bot_public -q` passes before any change.

All pytest commands below run from `src/backend`; the conftest sets `DEPLOY_PROFILE=test` by default.

---

### Task 1: Mint an owner principal for the identity-less callback

**Files:**
- Create: `src/backend/src/agentclaw/community/core/gateway_principal/minter.py`
- Modify: `src/backend/src/agentclaw/community/utils/gateway_principal_config.py` (append after `resign_principal_for_bcn`)
- Test: `src/backend/tests/community/core/gateway_principal/test_minter.py`

- [ ] **Step 1: Write the failing test**

Create `src/backend/tests/community/core/gateway_principal/test_minter.py`:

```python
"""The minted callback principal: shape, lifetime, and that BCS would accept it."""

import jwt
import pytest

from agentclaw.community.core.gateway_principal.errors import (
    PrincipalVerificationError,
)
from agentclaw.community.core.gateway_principal.minter import mint_user_principal
from agentclaw.community.core.gateway_principal.signer import PrincipalSignerConfig
from agentclaw.community.core.gateway_principal.verifier import (
    PrincipalVerifierConfig,
    verify_principal_token,
)

_KEY = "minter-test-signing-key-at-least-32-bytes-long"


def _signer() -> PrincipalSignerConfig:
    return PrincipalSignerConfig(
        signing_key=_KEY, audience="bcs", issuer="backend", key_id="bare"
    )


def test_minted_claims_carry_only_the_named_user():
    token = mint_user_principal("327325", signer=_signer())
    claims = jwt.decode(token, _KEY, algorithms=["HS256"], audience="bcs")

    assert claims["iss"] == "backend"
    assert claims["aud"] == "bcs"
    assert claims["principals"] == [
        {"type": "user", "subject": {"id": "327325", "username": "327325"}}
    ]
    assert isinstance(claims["iat"], int)
    assert isinstance(claims["exp"], int)
    assert claims["exp"] - claims["iat"] == 60


def test_minted_header_names_the_key_bcs_requires():
    token = mint_user_principal("327325", signer=_signer())
    header = jwt.get_unverified_header(token)

    assert header["kid"] == "bare"
    assert header["typ"] == "JWT"


def test_minted_token_verifies_under_the_bcs_audience():
    """The round-trip the whole design rests on: BCS must accept this token."""
    token = mint_user_principal("327325", signer=_signer())
    caller = verify_principal_token(
        token,
        PrincipalVerifierConfig(
            signing_key=_KEY, audience="bcs", issuer="backend"
        ),
    )

    assert caller.user_id == "327325"


def test_refuses_an_empty_owner_id():
    with pytest.raises(PrincipalVerificationError, match="owner id"):
        mint_user_principal("", signer=_signer())


def test_refuses_when_no_signing_key_is_configured():
    signer = PrincipalSignerConfig(
        signing_key="", audience="bcs", issuer="backend", key_id="bare"
    )
    with pytest.raises(PrincipalVerificationError, match="signing key"):
        mint_user_principal("327325", signer=signer)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/backend && uv run pytest tests/community/core/gateway_principal/test_minter.py -q`

Expected: collection error — `ModuleNotFoundError: No module named 'agentclaw.community.core.gateway_principal.minter'`.

- [ ] **Step 3: Write the minter**

Create `src/backend/src/agentclaw/community/core/gateway_principal/minter.py`:

```python
"""Mint a user principal for the one path that has no inbound credential.

``signer.resign_principal_token`` re-addresses a credential the gateway already
vouched for; it deliberately cannot create one. Exactly one flow needs a
credential the gateway never issued: the antprocess approval callback, which
arrives as a server-to-server form POST days after the request that opened the
ticket, carrying no principal at all. This module is that flow's credential
source, and it is the only minting site in the codebase.

Its blast radius is bounded outside this file: the only caller is the approval
decision application, which refuses to mint unless antprocess corroborates the
decision and a matching ``PROCESSING`` approval block already exists for that
bot and puid -- and BCS independently re-checks ``created_by`` against the
identity minted here.
"""

from __future__ import annotations

import time
from typing import Any, Dict

import jwt

from agentclaw.community.core.gateway_principal.errors import (
    PrincipalVerificationError,
)
from agentclaw.community.core.gateway_principal.signer import PrincipalSignerConfig

# Pinned as in ``signer.py``: every peer on this contract verifies HS256 with
# the shared key, and signing anything else would be rejected by BCS.
_ALGORITHM = "HS256"

# Long enough for one BCS round trip, short enough that a leaked token is stale
# before it can be useful. The gateway's own tokens live 60 seconds; a minted
# one gets no longer.
_MINT_TTL_SECONDS = 60


def mint_user_principal(owner_id: str, *, signer: PrincipalSignerConfig) -> str:
    """Return a short-lived user principal naming ``owner_id``.

    The claims are the minimum BCS admits: one ``user`` principal whose
    ``subject.id`` and ``subject.username`` are both the owner id, the issuer
    and audience the target requires, and an integer ``iat``/``exp`` pair.

    Raises:
        PrincipalVerificationError: for a blank owner id, or when no signing key
            is configured -- an empty-key signature is not a weaker credential
            but a forgeable one, so this refuses rather than emits.
    """
    owner = (owner_id or "").strip()
    if not owner:
        raise PrincipalVerificationError(
            "cannot mint a principal: owner id must not be empty"
        )
    if not signer.signing_key:
        raise PrincipalVerificationError(
            "no principal signing key is configured, so no token can be minted "
            f"for aud={signer.audience!r}"
        )

    now = int(time.time())
    claims: Dict[str, Any] = {
        "principals": [
            {"type": "user", "subject": {"id": owner, "username": owner}}
        ],
        "iss": signer.issuer,
        "aud": signer.audience,
        "iat": now,
        "exp": now + _MINT_TTL_SECONDS,
    }
    return jwt.encode(
        claims,
        signer.signing_key,
        algorithm=_ALGORITHM,
        headers={"kid": signer.key_id, "typ": "JWT"},
    )
```

- [ ] **Step 4: Add the config-bound entry point**

In `src/backend/src/agentclaw/community/utils/gateway_principal_config.py`, update the import block from `agentclaw.community.core.gateway_principal` to include `mint_user_principal`, then append after `resign_principal_for_bcn`:

```python
def mint_owner_principal_for_bcs(owner_id: str) -> str:
    """Mint the approval callback's BCS credential for one bot owner.

    Bound at the composition root and handed to ``BotPublicService`` the same
    way ``resign_principal_for_bcn`` is handed to the work-order callbacks: the
    core seam needs "a credential BCS accepts for this owner" and has no
    business knowing which key or which audience that takes (Rule 7).

    This is the only production caller of the minting primitive. It reads the
    same process-wide key the verifier installed, so a deployment that cannot
    verify a principal cannot mint one either.

    Raises:
        PrincipalVerificationError: for a blank owner id, or when this
            deployment resolved no signing key.
    """
    return mint_user_principal(owner_id, signer=get_bcn_principal_signer_config())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd src/backend && uv run pytest tests/community/core/gateway_principal -q`

Expected: all pass (the new file plus the existing signer/verifier tests).

- [ ] **Step 6: Commit**

```bash
git add src/backend/src/agentclaw/community/core/gateway_principal/minter.py \
        src/backend/src/agentclaw/community/utils/gateway_principal_config.py \
        src/backend/tests/community/core/gateway_principal/test_minter.py
git commit -m "feat(backend): mint the approval callback BCS principal"
```

---

### Task 2: Read and write a bot as the caller over BCS V1

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_management/services/bcn_service.py`
- Test: `src/backend/tests/community/core/bot_management/services/test_bcn_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `src/backend/tests/community/core/bot_management/services/test_bcn_service.py` (it already drives `BcnService` with the `LocalHttpClient` double — use whatever local helper the file defines for the double and its responses; append these three tests):

```python
def test_get_bot_as_user_sends_the_principal_header_and_unwraps_data():
    http = _make_http(
        {"code": 20000, "message": "OK",
         "data": {"bot_id": "bot_x", "kind": "bot", "created_by": "u1"}}
    )
    service = _make_service(http)

    bot = service.get_bot_as_user(bot_uuid="bot_x", principal_token="tok")

    assert bot == {"bot_id": "bot_x", "kind": "bot", "created_by": "u1"}
    assert http.calls[0].headers["X-Avernet-Principal"] == "tok"
    assert http.calls[0].path == "/openapi/v1/collaboration/bots/bot_x"


def test_get_bot_as_user_raises_bot_not_found_on_404():
    http = _make_http({"code": 404000, "message": "Not found", "data": None}, status=404)
    service = _make_service(http)

    with pytest.raises(BcnBotNotFoundError):
        service.get_bot_as_user(bot_uuid="nope", principal_token="tok")


def test_patch_bot_as_user_posts_the_patch_body_as_the_caller():
    http = _make_http({"code": 20000, "message": "OK", "data": {"bot_id": "bot_x"}})
    service = _make_service(http)

    service.patch_bot_as_user(
        bot_uuid="bot_x",
        principal_token="tok",
        body={"friend_ext": {"a": "b"}},
    )

    assert http.calls[0].method == "PATCH"
    assert http.calls[0].json == {"friend_ext": {"a": "b"}}
    assert http.calls[0].headers["X-Avernet-Principal"] == "tok"
```

If the file has no `_make_http` / `_make_service` helpers, add them at module scope following the local `LocalHttpClient` usage already in that file:

```python
def _make_http(payload, status=200):
    return LocalHttpClient(responses=[(status, payload)])


def _make_service(http) -> BcnService:
    return BcnService(http_client=http, config=BcnConfig())
```

And add `BcnBotNotFoundError` to the `from ...bcn_service import ...` line.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/backend && uv run pytest tests/community/core/bot_management/services/test_bcn_service.py -q`

Expected: FAIL — `AttributeError: 'BcnService' object has no attribute 'get_bot_as_user'`.

- [ ] **Step 3: Implement the two methods and the typed 404**

In `src/backend/src/agentclaw/community/core/bot_management/services/bcn_service.py`, add near the existing `BcnServiceError` definition:

```python
class BcnBotNotFoundError(BcnServiceError):
    """BCS answered 404 for a bot the V1 user channel addressed."""


#: The header the gateway injects and BCS V1 verifies.
_PRINCIPAL_HEADER = "X-Avernet-Principal"

#: The V1 user-scoped bot route the publication flow uses.
_V1_BOT_PATH = "/openapi/v1/collaboration/bots/{bot_uuid}"
```

Then add these two methods to `BcnService` (place them after `patch_attributes`):

```python
    def get_bot_as_user(self, *, bot_uuid: str, principal_token: str) -> Dict[str, Any]:
        """Read one bot over the V1 user channel as the principal's owner.

        Unwraps the V1 envelope and returns its ``data`` object. BCS answers
        404 for an unknown bot; that is a domain outcome (``BcnBotNotFoundError``),
        not a transport failure, so the caller can map it to its own not-found
        contract instead of a 500.
        """
        path = _V1_BOT_PATH.format(bot_uuid=bot_uuid)
        try:
            response = self._http.get(
                path,
                headers={_PRINCIPAL_HEADER: principal_token},
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 404:
                raise BcnBotNotFoundError(f"BCS bot not found: {bot_uuid}") from e
            raise self._http_error("BCS V1 bot get", e) from e
        except httpx.TimeoutException as e:
            raise BcnServiceError(f"BCS V1 bot get timeout: {e}") from e
        except Exception as e:
            raise BcnServiceError(f"BCS V1 bot get error: {e}") from e
        return self._unwrap_v1_data(response)

    def patch_bot_as_user(
        self, *, bot_uuid: str, principal_token: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Patch one bot over the V1 user channel as the principal's owner.

        BCS authorizes on ``record.created_by == caller`` and answers 403 when
        the minted or re-addressed identity does not own this bot; that check is
        the ownership backstop for the whole publication flow.
        """
        path = _V1_BOT_PATH.format(bot_uuid=bot_uuid)
        try:
            response = self._http.patch(
                path,
                json=body,
                headers={
                    _PRINCIPAL_HEADER: principal_token,
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 404:
                raise BcnBotNotFoundError(f"BCS bot not found: {bot_uuid}") from e
            raise self._http_error("BCS V1 bot patch", e) from e
        except httpx.TimeoutException as e:
            raise BcnServiceError(f"BCS V1 bot patch timeout: {e}") from e
        except Exception as e:
            raise BcnServiceError(f"BCS V1 bot patch error: {e}") from e
        return self._unwrap_v1_data(response)

    @staticmethod
    def _unwrap_v1_data(response: Any) -> Dict[str, Any]:
        """Return the V1 envelope's ``data`` object, or an empty object."""
        payload = response.json()
        if not isinstance(payload, dict):
            raise BcnServiceError("BCS V1 response was not an object")
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _http_error(op: str, error: "httpx.HTTPStatusError") -> BcnServiceError:
        """One message shape for both V1 calls' non-404 HTTP failures."""
        status = error.response.status_code if error.response else "N/A"
        body = error.response.text[:500] if error.response else "No response"
        return BcnServiceError(f"{op} HTTP error: {status} - {body}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/backend && uv run pytest tests/community/core/bot_management/services/test_bcn_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/core/bot_management/services/bcn_service.py \
        src/backend/tests/community/core/bot_management/services/test_bcn_service.py
git commit -m "feat(backend): add BCS V1 user-channel bot read and patch"
```

---

### Task 3: Approval-block parsing, decision validation, and merge

**Files:**
- Create: `src/backend/src/agentclaw/community/core/bot_public/publication_channel.py`
- Test: `src/backend/tests/community/core/bot_public/test_publication_channel.py`

- [ ] **Step 1: Write the failing tests**

Create `src/backend/tests/community/core/bot_public/test_publication_channel.py`:

```python
"""The pending-approval block: parsing, the decision gate, and the merge."""

import pytest

from agentclaw.community.core.bot_public.publication_channel import (
    BLOCK_KEY_BY_SCOPE,
    ConflictingDecision,
    PendingBlockMissing,
    PendingBlockNotProcessing,
    PendingBlockPuidMismatch,
    parse_pending_block,
    validate_decision,
)
from agentclaw.community.core.bot_public.services.bot_public_service import (
    BotPublicServiceError,
)

_PUID = "global-ticket-1"


def _processing_block(puid=_PUID, visibility="public"):
    return {
        "puid": puid,
        "approval_url": "https://approval/x",
        "view_friend_deps": [],
        "status": "PROCESSING",
        "visibility": visibility,
    }


def test_parse_returns_none_when_the_scope_block_is_absent():
    assert parse_pending_block({}, "user") is None


def test_parse_reads_the_scope_specific_key():
    friend_ext = {"public_agent_approval": _processing_block()}

    assert parse_pending_block(friend_ext, "agent").puid == _PUID
    assert parse_pending_block(friend_ext, "user") is None
    assert BLOCK_KEY_BY_SCOPE["agent"] == "public_agent_approval"


def test_validate_accepts_a_processing_block_with_the_matching_puid():
    block = parse_pending_block({"public_user_approval": _processing_block()}, "user")

    validate_decision(block, "AGREE", _PUID)  # does not raise


def test_validate_rejects_a_missing_block():
    with pytest.raises(PendingBlockMissing):
        validate_decision(None, "AGREE", _PUID)


def test_validate_rejects_a_non_processing_block():
    block = parse_pending_block(
        {"public_user_approval": {**_processing_block(), "status": "AGREE"}}, "user"
    )

    with pytest.raises(PendingBlockNotProcessing):
        validate_decision(block, "AGREE", _PUID)


def test_validate_rejects_a_puid_mismatch():
    block = parse_pending_block({"public_user_approval": _processing_block()}, "user")

    with pytest.raises(PendingBlockPuidMismatch):
        validate_decision(block, "AGREE", "some-other-ticket")


def test_validate_rejects_an_unknown_decision():
    block = parse_pending_block({"public_user_approval": _processing_block()}, "user")

    with pytest.raises(BotPublicServiceError, match="decision"):
        validate_decision(block, "MAYBE", _PUID)


def test_validate_is_terminal_aware_so_a_replay_is_distinguishable():
    """A terminal block raises ``ConflictingDecision`` only for a *different*
    decision; the same decision is the caller's idempotent-replay signal."""
    block = parse_pending_block(
        {"public_user_approval": {**_processing_block(), "status": "AGREE"}}, "user"
    )

    with pytest.raises(ConflictingDecision):
        validate_decision(block, "DISAGREE", _PUID)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public/test_publication_channel.py -q`

Expected: collection error — module does not exist.

- [ ] **Step 3: Implement parsing and validation**

Create `src/backend/src/agentclaw/community/core/bot_public/publication_channel.py` with the constants, dataclass, errors and pure functions (the writer class follows in Task 4):

```python
"""The BCS publication channel: approval blocks and the V1 friend_ext writes.

Everything the publication flow writes lands in one ``friend_ext`` object and
one scope visibility field on the bot, reached over the V1 user channel as the
bot's owner. This module owns what those writes mean: the scope-keyed
approval blocks, the gate a callback must pass before it may apply a decision,
and the merge that produces the patch body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from agentclaw.community.core.bot_public.services.bot_public_service import (
    BotPublicServiceError,
)

#: The ``friend_ext`` sub-block each publish scope records its ticket in. The
#: frontend reads these keys to show "publication pending" state.
BLOCK_KEY_BY_SCOPE: Dict[str, str] = {
    "user": "public_user_approval",
    "agent": "public_agent_approval",
}

#: The BCS visibility field each scope flips when a decision is AGREE.
VISIBILITY_FIELD_BY_SCOPE: Dict[str, str] = {
    "user": "user_visibility",
    "agent": "visibility",
}

#: Where an AGREE'd block's ``view_friend_deps`` is promoted to, per scope.
VIEW_SCOPE_DEPS_KEY_BY_SCOPE: Dict[str, str] = {
    "user": "view_scope_user_friend_deps",
    "agent": "view_scope_agent_friend_deps",
}

PROCESSING_STATUS = "PROCESSING"
TERMINAL_STATUSES = frozenset({"AGREE", "DISAGREE", "CANCEL"})
ALLOWED_DECISIONS = ("AGREE", "DISAGREE", "CANCEL")

#: The visibility a block carries when the publisher named none.
DEFAULT_VISIBILITY = "protected"


class PendingBlockMissing(BotPublicServiceError):
    """No approval block exists for this bot and scope."""


class PendingBlockNotProcessing(BotPublicServiceError):
    """The block exists but is not ``PROCESSING``."""


class PendingBlockPuidMismatch(BotPublicServiceError):
    """The block belongs to a different ticket than the callback names."""


class ConflictingDecision(BotPublicServiceError):
    """A terminal block is being asked to accept a different decision."""


@dataclass(frozen=True)
class PendingBlock:
    """One scope's approval block, as read from ``friend_ext``."""

    scope: str
    puid: Optional[str]
    status: str
    visibility: Optional[str]
    view_friend_deps: list


def parse_pending_block(friend_ext: Dict[str, Any], scope: str) -> Optional[PendingBlock]:
    """Return the scope's block, or ``None`` when it is absent or malformed."""
    key = BLOCK_KEY_BY_SCOPE.get(scope)
    raw = (friend_ext or {}).get(key) if key else None
    if not isinstance(raw, dict):
        return None
    return PendingBlock(
        scope=scope,
        puid=raw.get("puid"),
        status=str(raw.get("status") or "").upper(),
        visibility=raw.get("visibility"),
        view_friend_deps=list(raw.get("view_friend_deps") or []),
    )


def validate_decision(
    block: Optional[PendingBlock], decision: str, puid: Optional[str]
) -> None:
    """The gate a callback must pass before any BCS write.

    ``decision`` is the antprocess-corroborated decision, never the callback's
    claim. A terminal block that already holds *the same* decision is a replay
    and passes; one holding a different decision raises
    :class:`ConflictingDecision`.

    Raises:
        BotPublicServiceError: for a decision outside the allowed set.
        PendingBlockMissing: when no block exists.
        PendingBlockNotProcessing: when the block is terminal-other or unknown.
        PendingBlockPuidMismatch: when the block is another ticket's.
        ConflictingDecision: when a terminal block holds a different decision.
    """
    normalized = (decision or "").upper()
    if normalized not in ALLOWED_DECISIONS:
        raise BotPublicServiceError(
            f"unknown approval decision: {decision!r}; "
            f"expected one of {', '.join(ALLOWED_DECISIONS)}"
        )
    if block is None:
        raise PendingBlockMissing("no approval block for this bot and scope")
    if block.status in TERMINAL_STATUSES:
        if block.status != normalized:
            raise ConflictingDecision(
                f"block is already {block.status}, refusing {normalized}"
            )
        return
    if block.status != PROCESSING_STATUS:
        raise PendingBlockNotProcessing(
            f"block status is {block.status!r}, expected {PROCESSING_STATUS!r}"
        )
    if (block.puid or None) != (puid or None):
        raise PendingBlockPuidMismatch(
            f"block puid {block.puid!r} does not match {puid!r}"
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public/test_publication_channel.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/core/bot_public/publication_channel.py \
        src/backend/tests/community/core/bot_public/test_publication_channel.py
git commit -m "feat(backend): add approval-block parsing and decision gate"
```

---

### Task 4: The verify-and-rewrite V1 writer

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_public/publication_channel.py`
- Test: `src/backend/tests/community/core/bot_public/test_publication_channel.py`

- [ ] **Step 1: Write the failing tests**

Append to `src/backend/tests/community/core/bot_public/test_publication_channel.py`:

```python
from agentclaw.community.core.bot_public.publication_channel import (
    BcsPublicationChannel,
    PublicationWriteFailed,
)


class _FakeBcn:
    """Scripted V1 channel: each ``patch_bot_as_user`` call consumes one script
    entry, which is the full ``friend_ext`` the next read will return."""

    def __init__(self, reads, patch_effects):
        self._reads = list(reads)
        self._patch_effects = list(patch_effects)
        self.patch_bodies = []
        self.last_read = None

    def get_bot_as_user(self, *, bot_uuid, principal_token):
        self.last_read = self._reads.pop(0)
        return self.last_read

    def patch_bot_as_user(self, *, bot_uuid, principal_token, body):
        self.patch_bodies.append(body)
        self._reads.append(self._patch_effects.pop(0))
        return {"bot_id": bot_uuid}


def test_write_patches_once_when_the_block_survives():
    body = {"public_user_approval": {"puid": "p", "status": "PROCESSING"}}
    bcn = _FakeBcn(reads=[], patch_effects=[{"friend_ext": body}])
    channel = BcsPublicationChannel(bcn, max_attempts=3, retry_delay=0.0)

    channel.write(
        bot_uid="bot_x",
        principal_token="tok",
        friend_ext=body,
        expect_block_key="public_user_approval",
        expect_block_status="PROCESSING",
    )

    assert len(bcn.patch_bodies) == 1
    assert bcn.patch_bodies[0]["friend_ext"] == body


def test_write_rewrites_onto_the_latest_value_when_a_writer_clobbered_it():
    """The agent-scope writer wins the first round; the verify loop re-merges
    the user block onto the latest value instead of losing it."""
    user_block = {"puid": "p-user", "status": "PROCESSING"}
    agent_block = {"puid": "p-agent", "status": "PROCESSING"}
    # Round 1 read-back carries only the agent block (our write was clobbered);
    # round 2 read-back carries both.
    bcn = _FakeBcn(
        reads=[],
        patch_effects=[
            {"friend_ext": {"public_agent_approval": agent_block}},
            {
                "friend_ext": {
                    "public_agent_approval": agent_block,
                    "public_user_approval": user_block,
                }
            },
        ],
    )
    channel = BcsPublicationChannel(bcn, max_attempts=3, retry_delay=0.0)

    channel.write(
        bot_uid="bot_x",
        principal_token="tok",
        friend_ext={"public_agent_approval": agent_block, "public_user_approval": user_block},
        expect_block_key="public_user_approval",
        expect_block_status="PROCESSING",
    )

    assert len(bcn.patch_bodies) == 2
    # The retry carried the concurrent agent block forward instead of dropping it.
    assert bcn.patch_bodies[1]["friend_ext"]["public_agent_approval"] == agent_block
    assert bcn.patch_bodies[1]["friend_ext"]["public_user_approval"] == user_block


def test_write_raises_when_the_block_never_survives():
    bcn = _FakeBcn(
        reads=[],
        patch_effects=[
            {"friend_ext": {}},
            {"friend_ext": {}},
            {"friend_ext": {}},
        ],
    )
    channel = BcsPublicationChannel(bcn, max_attempts=3, retry_delay=0.0)

    with pytest.raises(PublicationWriteFailed):
        channel.write(
            bot_uid="bot_x",
            principal_token="tok",
            friend_ext={"public_user_approval": {"puid": "p", "status": "PROCESSING"}},
            expect_block_key="public_user_approval",
            expect_block_status="PROCESSING",
        )

    assert len(bcn.patch_bodies) == 3


def test_load_returns_the_bots_friend_ext_and_visibility_fields():
    bcn = _FakeBcn(
        reads=[{"bot_id": "bot_x", "kind": "bot", "created_by": "u1",
                "friend_ext": {"old": "x"}, "user_visibility": "private",
                "visibility": "protected"}],
        patch_effects=[],
    )
    channel = BcsPublicationChannel(bcn, max_attempts=3, retry_delay=0.0)

    view = channel.load(bot_uid="bot_x", principal_token="tok")

    assert view.friend_ext == {"old": "x"}
    assert view.user_visibility == "private"
    assert view.visibility == "protected"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public/test_publication_channel.py -q`

Expected: FAIL — `ImportError: cannot import name 'BcsPublicationChannel'`.

- [ ] **Step 3: Implement the writer**

Append to `src/backend/src/agentclaw/community/core/bot_public/publication_channel.py`:

```python
import time

from agentclaw.community.core.bot_management.services.bcn_service import BcnService


class PublicationWriteFailed(BotPublicServiceError):
    """A ``friend_ext`` write could not be made to survive concurrent writers."""


@dataclass(frozen=True)
class BotView:
    """The slice of a bot the publication flow reads."""

    friend_ext: Dict[str, Any]
    user_visibility: Optional[str]
    visibility: Optional[str]


class BcsPublicationChannel:
    """``friend_ext`` and visibility writes over the BCS V1 user channel.

    ``friend_ext`` is replaced wholesale on every patch and the V1 route has no
    revision precondition, so a concurrent writer (the other scope's publish, or
    a callback) can clobber a write between our read and our patch. Rather than
    claim atomicity the route does not offer, every write is verified: after
    patching we read the bot back and, if the intended block did not survive,
    re-merge it onto the *latest* value and retry. Two concurrent writers
    therefore converge instead of silently dropping one another's block.
    """

    def __init__(
        self,
        bcn_service: BcnService,
        *,
        max_attempts: int = 3,
        retry_delay: float = 0.05,
    ) -> None:
        self._bcn = bcn_service
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay

    def load(self, *, bot_uid: str, principal_token: str) -> BotView:
        """Read the bot's current publication-relevant state."""
        bot = self._bcn.get_bot_as_user(
            bot_uuid=bot_uid, principal_token=principal_token
        )
        friend_ext = bot.get("friend_ext")
        return BotView(
            friend_ext=dict(friend_ext) if isinstance(friend_ext, dict) else {},
            user_visibility=bot.get("user_visibility"),
            visibility=bot.get("visibility"),
        )

    def write(
        self,
        *,
        bot_uid: str,
        principal_token: str,
        friend_ext: Dict[str, Any],
        visibility_field: Optional[str] = None,
        visibility_value: Optional[str] = None,
        expect_block_key: str,
        expect_block_status: Optional[str],
    ) -> None:
        """Patch ``friend_ext`` (and optionally one visibility field) until it sticks.

        ``expect_block_key`` names the block this write exists to land. The
        predicate is the block's status when ``expect_block_status`` is given;
        when it is ``None`` (a write with no block, such as the private
        direct-apply) the predicate is instead ``visibility_field`` holding
        ``visibility_value``. Either way it is a durability check, not a
        re-assertion of the caller's authorization.

        Raises:
            PublicationWriteFailed: when the predicate still does not hold after
                ``max_attempts`` write/read rounds.
        """
        pending = dict(friend_ext)
        for attempt in range(1, self._max_attempts + 1):
            body: Dict[str, Any] = {"friend_ext": pending}
            if visibility_field and visibility_value:
                body[visibility_field] = visibility_value
            self._bcn.patch_bot_as_user(
                bot_uuid=bot_uid, principal_token=principal_token, body=body
            )

            latest = self.load(bot_uid=bot_uid, principal_token=principal_token)
            if expect_block_status is None:
                survived = (
                    visibility_field is not None
                    and getattr(latest, visibility_field) == visibility_value
                )
            else:
                block = latest.friend_ext.get(expect_block_key)
                survived = isinstance(block, dict) and (
                    str(block.get("status") or "").upper() == expect_block_status
                )
            if survived:
                return

            # Someone replaced the value out from under us. Re-merge onto the
            # latest friend_ext (preserving their keys) and try again.
            carried = pending.get(expect_block_key)
            if carried is not None:
                pending = {**latest.friend_ext, expect_block_key: carried}
            if attempt < self._max_attempts:
                time.sleep(self._retry_delay)

        raise PublicationWriteFailed(
            f"publication write for {expect_block_key} did not survive "
            f"{self._max_attempts} attempts on bot {bot_uid}"
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public/test_publication_channel.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/core/bot_public/publication_channel.py \
        src/backend/tests/community/core/bot_public/test_publication_channel.py
git commit -m "feat(backend): add verify-and-rewrite friend_ext writer"
```

---

### Task 5: Expose the raw principal token and thread it into the publish call

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/dependencies.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/collaboration_bots/router.py`
- Test: `src/backend/tests/community/endpoints/test_openapi_public_bcs.py`

- [ ] **Step 1: Write the failing test**

Append to `src/backend/tests/community/endpoints/test_openapi_public_bcs.py`:

```python
@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="forwards_the_verified_principal_token_to_the_service",
    input=CaseInput(
        path_params={"bot_uuid": "bcs-target-bot"},
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={"user_id": _CALLER},
        json_body={"public_scope": "user"},
    ),
    seed=lambda world: (
        _boot_verifier(world),
        bind_overrides(
            world,
            BotPublicServiceProtocol,
            {"public_bcs_bot": _capture_principal_token},
        ),
    ),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "message": "OK"}),
)
def public_bcs_passes_the_principal_token():
    """The service needs the raw header to re-address it for BCS; the verified
    caller alone cannot be turned back into a credential."""
```

and the stand-in plus a module-level capture list, next to `_happy`:

```python
_CAPTURED: dict = {}


def _capture_principal_token(_self, *args, **kwargs) -> BcsPublishResult:
    _CAPTURED.update(kwargs)
    return BcsPublishResult(success=True, puid="p", state="PROCESSING")
```

Add `bind_overrides` to the `from tests.community.framework import ...` line (or its `di_seams` module import) already used by the file.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/backend && uv run pytest tests/community/endpoints/test_openapi_public_bcs.py -q -k principal_token`

Expected: FAIL — the captured kwargs have no `principal_token` key.

- [ ] **Step 3: Add the dependency**

In `src/backend/src/agentclaw/community/adapters/http/openapi_v1/dependencies.py`, after `require_principal`, add:

```python
async def require_principal_token(connection: HTTPConnection) -> str:
    """Return the raw, **already verified** ``X-Avernet-Principal`` value.

    This exists for the one thing a :class:`VerifiedCaller` cannot do: be
    re-addressed. A handler that must call a second upstream on the caller's
    behalf needs the credential itself, because the gateway addresses each token
    to a single audience (``signer.py``). Verification is not re-done here --
    ``require_principal`` is a declared dependency of every route that takes
    this, so the value is trusted exactly as far as it is there.
    """
    await require_principal(connection)
    return connection.headers.get(PRINCIPAL_HEADER, "").strip()
```

- [ ] **Step 4: Thread it through the handler**

In `src/backend/src/agentclaw/community/adapters/http/openapi_v1/collaboration_bots/router.py`, add the import and parameter:

```python
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    require_principal_token,
)
```

and pass it into the service call inside `publish_bcs_bot_external`:

```python
async def publish_bcs_bot_external(
    bot_uuid: BotIdPath,
    actor_id: UserIdDep,
    principal_token: Annotated[str, Depends(require_principal_token)],
    request: Request,
    req: BcsPublicRequest,
    service: BotPublicServiceProtocol = Injected(BotPublicServiceProtocol),
) -> Envelope[BcsPublishResult]:
```

```python
    result = service.public_bcs_bot(
        bot_uid=bot_uuid,
        owner_id=actor_id,
        public_scope=req.public_scope,
        view_depts=[dept.model_dump() for dept in req.view_depts] if req.view_depts else None,
        visibility=req.visibility,
        operator=operator,
        principal_token=principal_token,
    )
```

(the `Annotated`/`Depends` imports already exist in the module's FastAPI import line; extend it to include `Depends` if absent).

- [ ] **Step 5: Run the endpoint tests to verify they pass**

Run: `cd src/backend && uv run pytest tests/community/endpoints/test_openapi_public_bcs.py -q`

Expected: PASS, including the new capture case.

- [ ] **Step 6: Commit**

```bash
git add src/backend/src/agentclaw/community/adapters/http/openapi_v1/dependencies.py \
        src/backend/src/agentclaw/community/adapters/http/openapi_v1/collaboration_bots/router.py \
        src/backend/tests/community/endpoints/test_openapi_public_bcs.py
git commit -m "feat(backend): thread the verified principal token into publish"
```

---

### Task 6: Resolve a BCS-native bot from BCS when `ac_bots` has no row

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py`
- Modify: `src/backend/tests/community/core/bot_public/test_bot_public_service.py`

- [ ] **Step 1: Write the failing tests**

In `src/backend/tests/community/core/bot_public/test_bot_public_service.py`, add to `class TestPublicBcsBot`:

```python
    def test_bcs_native_bot_is_resolved_from_bcs_when_ac_bots_has_no_row(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        bcn = MagicMock()
        bcn.get_bot_as_user.return_value = {
            "bot_id": "bot_a9c86fe3",
            "kind": "bot",
            "name": "Native Bot",
            "created_by": "u1",
            "friend_ext": {},
        }
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True, "puid": "p1", "state": "PROCESSING",
        }
        svc = _make_service(
            process_service=process, bot_repository=bot_repo, bcn_service=bcn
        )

        result = svc.public_bcs_bot(
            bot_uid="bot_a9c86fe3",
            owner_id="u1",
            public_scope="user",
            operator=_make_operator(),
            principal_token="resigned-token",
        )

        assert result["puid"] == "p1"
        bcn.get_bot_as_user.assert_called_once_with(
            bot_uuid="bot_a9c86fe3", principal_token="resigned-token"
        )
        ctx = process.start_approval.call_args.kwargs["context"]
        assert "Native Bot" in ctx["publishHint"]
        assert "botSkills" in ctx

    def test_bcs_native_bot_owned_by_someone_else_is_not_found(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        bcn = MagicMock()
        bcn.get_bot_as_user.return_value = {
            "bot_id": "bot_a9c86fe3", "kind": "bot", "created_by": "someone-else",
        }
        svc = _make_service(bot_repository=bot_repo, bcn_service=bcn)

        with pytest.raises(BotNotFoundError):
            svc.public_bcs_bot(
                bot_uid="bot_a9c86fe3", owner_id="u1", public_scope="user",
                operator=_make_operator(), principal_token="t",
            )

    def test_bcs_human_row_is_treated_as_not_found(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        bcn = MagicMock()
        bcn.get_bot_as_user.return_value = {
            "bot_id": "human_1", "kind": "human", "created_by": "u1",
        }
        svc = _make_service(bot_repository=bot_repo, bcn_service=bcn)

        with pytest.raises(BotNotFoundError):
            svc.public_bcs_bot(
                bot_uid="human_1", owner_id="u1", public_scope="user",
                operator=_make_operator(), principal_token="t",
            )

    def test_unknown_in_both_stores_is_not_found(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        bcn = MagicMock()
        bcn.get_bot_as_user.side_effect = BcnBotNotFoundError("nope")
        svc = _make_service(bot_repository=bot_repo, bcn_service=bcn)

        with pytest.raises(BotNotFoundError):
            svc.public_bcs_bot(
                bot_uid="bot_x", owner_id="u1", public_scope="user",
                operator=_make_operator(), principal_token="t",
            )
```

Add the imports this file needs: `BcnBotNotFoundError` from `agentclaw.community.core.bot_management.services.bcn_service`, and update `_make_service` to accept and forward the two new callables:

```python
def _make_service(
    bot_friend_repo=None,
    bot_repository=None,
    process_service=None,
    bot_service=None,
    bcn_service=None,
    passport_plugin=None,
    auth_relationship_plugin=None,
    publish_approval_plugin=None,
    skill_set_service_factory=None,
    device_context_resolver=None,
    device_sync_dispatcher=None,
    bcsfuse_config=None,
    catalog_metadata_service=None,
    resign_principal=None,
    mint_principal=None,
):
    resolver = device_context_resolver or MagicMock()
    return BotPublicService(
        bot_friend_repo=bot_friend_repo or MagicMock(),
        ...
        catalog_metadata_service=catalog_metadata_service or MagicMock(),
        resign_principal=resign_principal or (lambda token: f"resigned:{token}"),
        mint_principal=mint_principal or (lambda owner_id: f"minted:{owner_id}"),
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public/test_bot_public_service.py -q -k "bcs_native or human_row or both_stores"`

Expected: FAIL — `BotPublicService.__init__() got an unexpected keyword argument 'resign_principal'`.

- [ ] **Step 3: Add the callables and the fallback branch**

In `src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py`:

(a) add the imports:

```python
from agentclaw.community.core.bot_management.services.bcn_service import (
    BcnBotNotFoundError,
)
from agentclaw.community.core.bot_public.publication_channel import (
    BLOCK_KEY_BY_SCOPE,
    BcsPublicationChannel,
    parse_pending_block,
    validate_decision,
    VISIBILITY_FIELD_BY_SCOPE,
    VIEW_SCOPE_DEPS_KEY_BY_SCOPE,
    DEFAULT_VISIBILITY,
)
```

(b) extend `__init__` with the two injected callables (add after `catalog_metadata_service`):

```python
        resign_principal: Callable[[str], str],
        mint_principal: Callable[[str], str],
```

and in the body:

```python
        self._resign_principal = resign_principal
        self._mint_principal = mint_principal
        self._publication_channel = BcsPublicationChannel(bcn_service)
```

(c) change `public_bcs_bot` to take the token and fall back to BCS:

```python
    def public_bcs_bot(
        self,
        bot_uid: str,
        owner_id: str,
        public_scope: str,
        operator: OperatorContext,
        principal_token: str,
        view_depts: Optional[List[Dict[str, str]]] = None,
        visibility: Optional[str] = None,
    ) -> Dict[str, Any]:
```

and replace the lookup block (currently `backend_bot_id = ...` / `bot = ...` / `if not bot: raise BotNotFoundError(...)`) with:

```python
        backend_bot_id = bot_uid.split(":")[0]
        bot = self._bot_repository.get_by_id_and_owner(backend_bot_id, owner_id)
        if not bot:
            bot = self._resolve_bcs_native_bot(
                bot_uid=bot_uid,
                owner_id=owner_id,
                principal_token=principal_token,
            )
```

(d) add the resolver next to `_build_public_approval_context`:

```python
    def _resolve_bcs_native_bot(
        self, *, bot_uid: str, owner_id: str, principal_token: str
    ) -> Dict[str, Any]:
        """Resolve a BCS-native bot (no ``ac_bots`` row) through BCS itself.

        The V1 read applies no ownership or visibility filter and may answer with
        a Human row, so this is a hydration plus the early ownership check -- BCS
        re-checks ``created_by`` on the PATCH that follows. A Human row, a
        missing creator, a mismatch and a missing bot are all answered as
        "not found", which is the same answer the ``ac_bots`` path gives for an
        owner mismatch and the platform's existence-non-disclosure rule.

        Raises:
            BotNotFoundError: for any of the four not-found outcomes.
            BcnServiceError: for transport or unexpected BCS failures.
        """
        try:
            remote = self._bcn_service.get_bot_as_user(
                bot_uuid=bot_uid,
                principal_token=self._resign_principal(principal_token),
            )
        except BcnBotNotFoundError as exc:
            raise BotNotFoundError(f"Bot not found: {bot_uid}") from exc

        if remote.get("kind") != "bot":
            raise BotNotFoundError(f"Bot not found: {bot_uid}")
        if (remote.get("created_by") or None) != (owner_id or None):
            raise BotNotFoundError(f"Bot not found: {bot_uid}")

        return {
            "bot_id": remote.get("bot_id") or bot_uid,
            "owner_id": owner_id,
            "bot_name": remote.get("name") or bot_uid,
            "entity_id": "",
            "ext": {},
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public/test_bot_public_service.py -q`

Expected: PASS. If existing tests fail with "missing principal_token", pass `principal_token="t"` at those call sites — the parameter is required because the synchronous entry always has one.

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py \
        src/backend/tests/community/core/bot_public/test_bot_public_service.py
git commit -m "feat(backend): resolve BCS-native bots for publication"
```

---

### Task 7: Migrate the synchronous writes to the V1 channel

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py`
- Modify: `src/backend/tests/community/core/bot_public/test_bot_public_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_bot_public_service.py` (replacing the two tests that assert `bcn.get_attributes` / `bcn.patch_attributes` if they exist — grep for `patch_attributes` first and update those expectations to the V1 methods):

```python
    def test_persist_writes_the_processing_block_over_the_v1_channel(self):
        bcn = MagicMock()
        bcn.get_bot_as_user.return_value = {"friend_ext": {"keep": "me"}}
        svc = _make_service(bcn_service=bcn, bot_repository=_repo_with_bot())

        svc._persist_public_approval(
            bot_uid="b1:entity1",
            public_scope="user",
            puid="p1",
            approval_url="https://approval/p1",
            view_depts=[{"deptNo": "D1", "deptName": "Tech"}],
            visibility="public",
            principal_token="tok",
        )

        body = bcn.patch_bot_as_user.call_args.kwargs["body"]
        assert body["friend_ext"]["keep"] == "me"
        block = body["friend_ext"]["public_user_approval"]
        assert block["puid"] == "p1"
        assert block["status"] == "PROCESSING"
        assert block["visibility"] == "public"
        assert bcn.patch_bot_as_user.call_args.kwargs["principal_token"] == "resigned:tok"

    def test_persist_failure_cancels_the_ticket_and_raises(self):
        bcn = MagicMock()
        bcn.get_bot_as_user.return_value = {"friend_ext": {}}
        bcn.patch_bot_as_user.side_effect = RuntimeError("bcs down")
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True, "puid": "p1", "state": "PROCESSING",
        }
        svc = _make_service(
            bcn_service=bcn, process_service=process, bot_repository=_repo_with_bot()
        )

        with pytest.raises(BotPublicServiceError, match="pending approval"):
            svc.public_bcs_bot(
                bot_uid="b1", owner_id="u1", public_scope="user",
                operator=_make_operator(), principal_token="tok",
            )

        process.cancel_approval.assert_called_once_with(puid="p1", operator="op_user")

    def test_private_direct_apply_uses_the_v1_channel(self):
        bcn = MagicMock()
        bcn.get_bot_as_user.return_value = {"friend_ext": {}}
        svc = _make_service(bcn_service=bcn, bot_repository=_repo_with_bot())

        result = svc.public_bcs_bot(
            bot_uid="b1", owner_id="u1", public_scope="user",
            operator=_make_operator(), principal_token="tok", visibility="private",
        )

        assert result["visibility"] == "private"
        body = bcn.patch_bot_as_user.call_args.kwargs["body"]
        assert body["user_visibility"] == "private"
        bcn.patch_attributes.assert_not_called()
```

with the helper at module scope:

```python
def _repo_with_bot(bot_id="b1", owner_id="u1"):
    repo = MagicMock()
    repo.get_by_id_and_owner.return_value = _make_bot(bot_id=bot_id, owner_id=owner_id)
    return repo
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public/test_bot_public_service.py -q -k "v1_channel or cancels_the_ticket"`

Expected: FAIL — `_persist_public_approval` still calls `get_attributes`/`patch_attributes`.

- [ ] **Step 3: Rewrite the two synchronous writers**

In `bot_public_service.py`:

(a) `_persist_public_approval` keeps its signature plus `principal_token: str`, and becomes:

```python
    def _persist_public_approval(
        self,
        *,
        bot_uid: str,
        public_scope: str,
        puid: Optional[str],
        approval_url: Optional[str],
        view_depts: Optional[List[Dict[str, str]]],
        principal_token: str,
        visibility: Optional[str] = None,
    ) -> None:
        """Record the ticket on the bot over the V1 channel (status PROCESSING).

        The write must land: a ticket without its block is a ticket whose every
        callback the state machine will refuse, so failure propagates to the
        caller (who cancels the ticket) rather than being logged and forgotten.
        """
        token = self._resign_principal(principal_token)
        view = self._publication_channel.load(bot_uid=bot_uid, principal_token=token)
        block: Dict[str, Any] = {
            "puid": puid,
            "approval_url": approval_url,
            "view_friend_deps": list(view_depts) if view_depts else [],
            "status": "PROCESSING",
        }
        if visibility:
            block["visibility"] = visibility
        friend_ext = dict(view.friend_ext)
        friend_ext[BLOCK_KEY_BY_SCOPE[public_scope]] = block
        self._publication_channel.write(
            bot_uid=bot_uid,
            principal_token=token,
            friend_ext=friend_ext,
            expect_block_key=BLOCK_KEY_BY_SCOPE[public_scope],
            expect_block_status="PROCESSING",
        )
```

(b) `_apply_visibility_direct` keeps its signature plus `principal_token: str`, and becomes:

```python
    def _apply_visibility_direct(
        self, *, bot_uid: str, public_scope: str, principal_token: str
    ) -> Dict[str, Any]:
        """Set the scope's visibility straight to ``private`` over V1.

        Reclaiming visibility needs no approval, so there is no block to write
        and no ticket to cancel; BCS enforces ``created_by == caller`` on the
        patch, which is the ownership check this path previously lacked.
        """
        field = VISIBILITY_FIELD_BY_SCOPE[public_scope]
        token = self._resign_principal(principal_token)
        view = self._publication_channel.load(bot_uid=bot_uid, principal_token=token)
        self._publication_channel.write(
            bot_uid=bot_uid,
            principal_token=token,
            friend_ext=view.friend_ext,
            visibility_field=field,
            visibility_value="private",
            expect_block_key=BLOCK_KEY_BY_SCOPE[public_scope],
            expect_block_status=None,
        )
        if public_scope == "user":
            self._sync_bcsfuse_worker_runtime_state(bot_uid, bot_uid, "0")
        return {
            "success": True,
            "state": "COMPLETED",
            "puid": None,
            "approval_url": None,
            "visibility": "private",
            "visibility_field": field,
        }
```

Note: Task 4's implementation must use `expect_block_status: Optional[str]`. For this private path, pass `expect_block_status=None`, so the writer verifies `visibility_field == visibility_value` rather than looking for a block. The same writer implementation and predicate are already specified in Task 4's code block; do not add a second implementation here.

(c) update the two call sites in `public_bcs_bot`:

```python
            return self._apply_visibility_direct(
                bot_uid=bot_uid, public_scope=public_scope,
                principal_token=principal_token,
            )
```

```python
        try:
            self._persist_public_approval(
                bot_uid=bot_uid,
                public_scope=public_scope,
                puid=approval_result.get("puid"),
                approval_url=approval_result.get("approval_url"),
                view_depts=view_depts,
                visibility=visibility,
                principal_token=principal_token,
            )
        except Exception as exc:  # noqa: BLE001 -- compensate, then fail the publish
            logger.warning(
                "[public_bcs_bot] BCS pending approval block write failed: %s", exc
            )
            self._cancel_started_ticket(
                puid=approval_result.get("puid"), operator_id=operator_id
            )
            raise BotPublicServiceError(
                "failed to record the pending approval block"
            ) from exc
```

(d) add the cancellation helper:

```python
    def _cancel_started_ticket(self, *, puid: Optional[str], operator_id: str) -> None:
        """Best-effort cancel of a ticket whose block could not be recorded.

        Compensation, not correctness: if the cancel also fails, the operator has
        an orphaned ticket and no block, which is exactly the state the log lines
        here name. It never masks the original write failure, which the caller
        propagates.
        """
        if not puid:
            return
        try:
            self._process_service.cancel_approval(puid=puid, operator=operator_id)
        except Exception as exc:  # noqa: BLE001 -- compensation must not mask
            logger.error(
                "[public_bcs_bot] cancelling ticket %s after a failed block write "
                "also failed: %s",
                puid, exc,
            )
```

(e) the inline-COMPLETED call after `_persist_public_approval` now passes the resigned token:

```python
        if str(approval_result.get("state", "")).upper() == "COMPLETED":
            try:
                self.handle_public_approval_callback(
                    bot_id=bot_uid,
                    owner_id=owner_id,
                    puid=approval_result.get("puid"),
                    last_operate=str(approval_result.get("lastOperate", "")).lower(),
                    public_scope=public_scope,
                    principal_token=principal_token,
                )
            except Exception as exc:  # noqa: BLE001 -- the block is written; surface it
                logger.warning(
                    "[public_bcs_bot] COMPLETED inline callback failed: %s", exc
                )
                raise BotPublicServiceError(
                    "failed to apply the completed approval"
                ) from exc
```

(`handle_public_approval_callback` gains `principal_token: Optional[str] = None`; Task 8 completes its body.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py \
        src/backend/src/agentclaw/community/core/bot_public/publication_channel.py \
        src/backend/tests/community/core/bot_public/test_bot_public_service.py
git commit -m "feat(backend): run synchronous publication writes over BCS V1"
```

---

### Task 8: Give `query_approval_status` a decision contract

**Files:**
- Modify: `src/backend/src/agentclaw/community/plugin_api/approval_workflow.py`
- Modify: `src/backend/src/agentclaw/community/plugins/community/approval_workflow.py`
- Modify: `src/backend/src/agentclaw/community/plugins/local/antprocess.py`
- Test: `src/backend/tests/community/contracts/test_approval_workflow.py`

- [ ] **Step 1: Write the failing conformance test**

Append to `src/backend/tests/community/contracts/test_approval_workflow.py`:

```python
def test_query_approval_status_returns_the_typed_decision_contract(world):
    """Every local impl must answer the shape the corroboration gate reads.

    The gate cannot rest on "some dict": it decides whether an externally
    delivered callback may change a bot's visibility. The permitted shape is
    ``success`` (bool), ``puid`` (str | None), ``state`` (str | None) and
    ``decision`` (one of AGREE/DISAGREE/CANCEL, or None).
    """
    plugin = world.get(ApprovalWorkflowPlugin)

    result = plugin.query_approval_status("some-puid")

    assert set(result) >= {"success", "puid", "state", "decision"}
    assert isinstance(result["success"], bool)
    assert result["puid"] in (None, "some-puid")
    assert result["state"] is None or isinstance(result["state"], str)
    assert result["decision"] in (None, "AGREE", "DISAGREE", "CANCEL")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/backend && uv run pytest tests/community/contracts/test_approval_workflow.py -q`

Expected: FAIL — `KeyError: 'decision'` (the impls return the legacy dict).

- [ ] **Step 3: State the contract and shape both impls**

In `plugin_api/approval_workflow.py`, replace the `query_approval_status` docstring body with the contract:

```python
    def query_approval_status(self, puid: str) -> dict[str, Any]:
        """Query one approval instance's current decision.

        The returned mapping is a contract, not a passthrough: the caller uses
        it to decide whether an externally delivered callback may change a bot's
        visibility, so the fields it reads are fixed.

        Returns:
            ``{"success": bool, "puid": str | None, "state": str | None,
            "decision": str | None}`` where

            - ``success`` — whether the query itself reached the platform;
            - ``puid`` — the instance the platform answered for, or ``None``;
            - ``state`` — the platform's own state string, echoed for logs;
            - ``decision`` — the authoritative decision: one of ``AGREE``,
              ``DISAGREE``, ``CANCEL``, or ``None`` when no decision has been
              recorded yet.

        An implementation with no workflow answers ``success=False`` and
        ``decision=None``; it must never synthesize a decision.
        """
```

In `plugins/community/approval_workflow.py`, replace the `query_approval_status` return value with:

```python
        return {
            "success": False,
            "puid": puid,
            "state": None,
            "decision": None,
            "title": None,
            "applicant": None,
            "process_id": None,
            "error_msg": _NO_WORKFLOW,
        }
```

In `plugins/local/antprocess.py`, replace the `query_approval_status` return value with:

```python
        return {
            "success": False,
            "puid": puid,
            "state": None,
            "decision": None,
            "title": None,
            "applicant": None,
            "process_id": None,
            "error_msg": "local mode — antprocess unavailable",
        }
```

- [ ] **Step 4: Run the conformance test to verify it passes**

Run: `cd src/backend && uv run pytest tests/community/contracts/test_approval_workflow.py tests/community/plugins -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backend/src/agentclaw/community/plugin_api/approval_workflow.py \
        src/backend/src/agentclaw/community/plugins/community/approval_workflow.py \
        src/backend/src/agentclaw/community/plugins/local/antprocess.py \
        src/backend/tests/community/contracts/test_approval_workflow.py
git commit -m "feat(backend): define the approval decision query contract"
```

Note for the rollout section of the PR: the corp/prod `ApprovalWorkflowPlugin` implementation lives outside this repository and must return the same shape before this flow is enabled there. Record that as a cross-repo dependency in the PR description.

---

### Task 9: Apply the callback decision through the minted principal

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py`
- Modify: `src/backend/src/agentclaw/community/di/modules/bot_public_module.py`
- Modify: `src/backend/tests/community/core/bot_public/test_bot_public_service.py`

- [ ] **Step 1: Write the failing tests**

Add a `class TestApplyCallbackDecision:` block to `test_bot_public_service.py`:

```python
class TestApplyCallbackDecision:
    def _service(self, friend_ext, decision="AGREE"):
        bcn = MagicMock()
        bcn.get_bot_as_user.return_value = {"friend_ext": friend_ext}
        process = MagicMock()
        process.query_approval_status.return_value = {
            "success": True, "puid": "p1", "state": "COMPLETED", "decision": decision,
        }
        return _make_service(bcn_service=bcn, process_service=process), bcn, process

    def test_agree_flips_the_scope_visibility_and_records_the_status(self):
        svc, bcn, _ = self._service(
            {"public_user_approval": {"puid": "p1", "status": "PROCESSING",
                                      "visibility": "public"}}
        )

        svc.handle_public_approval_callback(
            bot_id="bot_x:u1", owner_id="u1", puid="p1",
            last_operate="agree", public_scope="user",
        )

        body = bcn.patch_bot_as_user.call_args.kwargs["body"]
        assert body["user_visibility"] == "public"
        assert body["friend_ext"]["public_user_approval"]["status"] == "AGREE"
        assert bcn.patch_bot_as_user.call_args.kwargs["principal_token"] == "minted:u1"

    def test_a_callback_antprocess_cannot_corroborate_is_refused(self):
        bcn = MagicMock()
        process = MagicMock()
        process.query_approval_status.return_value = {
            "success": True, "puid": "p1", "state": "PROCESSING", "decision": None,
        }
        svc = _make_service(bcn_service=bcn, process_service=process)

        result = svc.handle_public_approval_callback(
            bot_id="bot_x:u1", owner_id="u1", puid="p1",
            last_operate="agree", public_scope="user",
        )

        assert result["success"] is False
        bcn.patch_bot_as_user.assert_not_called()

    def test_a_decision_disagreeing_with_the_claim_is_refused(self):
        svc, bcn, process = self._service(
            {"public_user_approval": {"puid": "p1", "status": "PROCESSING"}},
            decision="DISAGREE",
        )

        result = svc.handle_public_approval_callback(
            bot_id="bot_x:u1", owner_id="u1", puid="p1",
            last_operate="agree", public_scope="user",
        )

        assert result["success"] is False
        bcn.patch_bot_as_user.assert_not_called()

    def test_a_puid_mismatch_is_refused(self):
        svc, bcn, _ = self._service(
            {"public_user_approval": {"puid": "other", "status": "PROCESSING"}}
        )

        result = svc.handle_public_approval_callback(
            bot_id="bot_x:u1", owner_id="u1", puid="p1",
            last_operate="agree", public_scope="user",
        )

        assert result["success"] is False
        bcn.patch_bot_as_user.assert_not_called()

    def test_a_replayed_same_decision_is_idempotent(self):
        svc, bcn, _ = self._service(
            {"public_user_approval": {"puid": "p1", "status": "AGREE",
                                      "visibility": "public"}}
        )

        result = svc.handle_public_approval_callback(
            bot_id="bot_x:u1", owner_id="u1", puid="p1",
            last_operate="agree", public_scope="user",
        )

        assert result["success"] is True

    def test_a_query_failure_reports_failure_so_antprocess_retries(self):
        bcn = MagicMock()
        process = MagicMock()
        process.query_approval_status.side_effect = RuntimeError("timeout")
        svc = _make_service(bcn_service=bcn, process_service=process)

        result = svc.handle_public_approval_callback(
            bot_id="bot_x:u1", owner_id="u1", puid="p1",
            last_operate="agree", public_scope="user",
        )

        assert result["success"] is False
        bcn.patch_bot_as_user.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public/test_bot_public_service.py -q -k ApplyCallbackDecision`

Expected: FAIL — the current `_apply_callback_decision` uses provider-admin calls and no corroboration.

- [ ] **Step 3: Implement the corroborated, minted decision path**

In `bot_public_service.py`, extend `handle_public_approval_callback` with `principal_token: Optional[str] = None` and pass it down, then replace `_apply_callback_decision` with:

```python
    def _apply_callback_decision(
        self,
        *,
        bot_uid: str,
        public_scope: str,
        last_operate: str,
        puid: Optional[str],
        principal_token: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply one approval decision to BCS, as the bot's owner.

        Three gates stand between an externally delivered callback and a
        visibility flip:

        1. antprocess corroborates the decision -- the callback's ``lastOperate``
           is a claim, the platform's answer is the fact;
        2. the bot must already carry a ``PROCESSING`` block for this scope and
           puid, so a forged callback cannot invent a publication;
        3. BCS re-checks ``created_by`` against the identity used here, which is
           the request's own re-addressed principal on the synchronous path and a
           short-lived minted one on the callback path.

        Never raises for a refused decision: antprocess retries on failure, so a
        definitive refusal is answered with ``success=False`` and a log line, and
        only transport failures propagate.
        """
        decision = self._corroborate_decision(puid=puid, claimed=last_operate)
        if decision is None:
            return {
                "success": False,
                "public": None,
                "message": f"decision not corroborated for puid={puid}",
            }

        token = principal_token
        if token is None:
            if not owner_id:
                return {
                    "success": False,
                    "public": None,
                    "message": "callback carries neither a principal nor an owner id",
                }
            token = self._mint_principal(owner_id)
        else:
            token = self._resign_principal(token)

        view = self._publication_channel.load(bot_uid=bot_uid, principal_token=token)
        block = parse_pending_block(view.friend_ext, public_scope)
        try:
            validate_decision(block, decision, puid)
        except ConflictingDecision as exc:
            logger.error("[publication] conflicting decision refused: %s", exc)
            return {"success": False, "public": None, "message": str(exc)}
        except BotPublicServiceError as exc:
            logger.error("[publication] decision refused: %s", exc)
            return {"success": False, "public": None, "message": str(exc)}

        if block.status == decision:
            return {
                "success": True,
                "public": None,
                "message": f"decision {decision} already applied",
            }

        friend_ext = dict(view.friend_ext)
        merged = dict(friend_ext[BLOCK_KEY_BY_SCOPE[public_scope]])
        merged["status"] = decision
        friend_ext[BLOCK_KEY_BY_SCOPE[public_scope]] = merged

        visibility_field = VISIBILITY_FIELD_BY_SCOPE[public_scope]
        visibility_value = None
        if decision == "AGREE":
            visibility_value = merged.get("visibility") or DEFAULT_VISIBILITY
            friend_ext[VIEW_SCOPE_DEPS_KEY_BY_SCOPE[public_scope]] = (
                merged.get("view_friend_deps") or []
            )

        self._publication_channel.write(
            bot_uid=bot_uid,
            principal_token=token,
            friend_ext=friend_ext,
            visibility_field=visibility_field if decision == "AGREE" else None,
            visibility_value=visibility_value,
            expect_block_key=BLOCK_KEY_BY_SCOPE[public_scope],
            expect_block_status=decision,
        )

        if decision == "AGREE" and public_scope == "user":
            public = "0" if visibility_value == "private" else "1"
            self._sync_bcsfuse_worker_runtime_state(bot_uid, bot_uid, public)

        return {
            "success": True,
            "public": None,
            "message": f"public_scope={public_scope} callback status={decision}",
        }
```

and add the corroboration helper:

```python
    def _corroborate_decision(
        self, *, puid: Optional[str], claimed: str
    ) -> Optional[str]:
        """Return the platform's decision, or ``None`` when it cannot be used.

        The callback's ``lastOperate`` is never trusted on its own: this asks the
        workflow plugin and compares. A query that fails, reports no decision, or
        names a different decision than the callback claimed all yield ``None``,
        which the caller answers as a refusal. Transport failures propagate so
        antprocess retries.
        """
        if not puid:
            logger.error("[publication] callback carried no puid")
            return None
        try:
            status = self._process_service.query_approval_status(puid)
        except Exception:  # noqa: BLE001 -- propagated so antprocess retries
            logger.exception("[publication] approval status query failed puid=%s", puid)
            raise
        if not status.get("success"):
            logger.error(
                "[publication] approval status query unsuccessful puid=%s error=%s",
                puid, status.get("error_msg"),
            )
            return None
        decision = status.get("decision")
        if decision not in ALLOWED_DECISIONS:
            logger.error(
                "[publication] approval status carried no usable decision "
                "puid=%s decision=%r", puid, decision,
            )
            return None
        if decision != (claimed or "").upper():
            logger.error(
                "[publication] callback claim %r disagrees with the platform's %r "
                "puid=%s", claimed, decision, puid,
            )
            return None
        return decision
```

Add `ALLOWED_DECISIONS` and `ConflictingDecision` to the `publication_channel` import list in this module.

- [ ] **Step 4: Add the mint to the DI graph**

In `src/backend/src/agentclaw/community/di/modules/bot_public_module.py`, import the two config-bound callables:

```python
from agentclaw.community.utils.gateway_principal_config import (
    mint_owner_principal_for_bcs,
    resign_principal_for_bcn,
)
```

and pass them in the `bot_public_service` provider:

```python
        return BotPublicService(
            ...,
            catalog_metadata_service=catalog_metadata_service,
            resign_principal=resign_principal_for_bcn,
            mint_principal=mint_owner_principal_for_bcs,
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public tests/community/di -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py \
        src/backend/src/agentclaw/community/di/modules/bot_public_module.py \
        src/backend/tests/community/core/bot_public/test_bot_public_service.py
git commit -m "feat(backend): apply approval decisions as the bot's owner"
```

---

### Task 10: Refuse application-only callers at the Backend

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/admission.py`
- Test: `src/backend/tests/community/adapters/http/openapi_v1/test_app_only_refusals.py`

- [ ] **Step 1: Write the failing test**

Add to `src/backend/tests/community/adapters/http/openapi_v1/test_app_only_refusals.py` (follow that file's existing parameterization shape; add this case in the same style):

```python
def test_bcs_publish_refuses_an_application_only_caller(client, app_only_principal):
    """The publication write path needs a Human identity to re-address, so a
    caller that names only an application is refused here, before any ticket."""
    response = client.post(
        "/openapi/v1/collaboration/bots/bot_x/public?user_id=327325",
        json={"public_scope": "user"},
        headers={PRINCIPAL_HEADER: app_only_principal},
    )

    assert response.status_code == 401
```

If the file has no `client`/`app_only_principal` fixtures, use the module's existing construction of an app-only token and test client, mirroring its other cases verbatim.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/backend && uv run pytest tests/community/adapters/http/openapi_v1/test_app_only_refusals.py -q`

Expected: FAIL — the route is `OPEN`, so the app-only caller gets past the seam.

- [ ] **Step 3: Change the admission entry**

In `src/backend/src/agentclaw/community/adapters/http/openapi_v1/admission.py`, replace the entry and its comment:

```python
    # New-version bcs publish-to-users. Human-only: the flow re-addresses the
    # caller's principal to BCS, which requires a Human identity, and a caller
    # naming only an application cannot be given one. The edge already declares
    # `user: required` for this path -- REFUSED is the label the pair agrees on.
    ("POST", "/openapi/v1/collaboration/bots/{bot_uuid}/public"): AdmissionMode.REFUSED,
```

- [ ] **Step 4: Run the admission and endpoint tests**

Run: `cd src/backend && uv run pytest tests/community/adapters/http/openapi_v1 tests/community/endpoints/test_openapi_public_bcs.py tests/community/architecture -q`

Expected: PASS. The route inventory test is the pin that proves the surface and the table still agree.

- [ ] **Step 5: Verify the gateway counterpart still agrees**

Run: `cd src/gateway && rg -n "bots/\{bot_uuid\}/public" -A 1 configs/application.yaml`

Expected: the entry reads `user: required` (already true). If the gateway's route-security test suite is runnable locally, run it; otherwise state in the PR that only the Backend side moved and the edge already matched.

- [ ] **Step 6: Commit**

```bash
git add src/backend/src/agentclaw/community/adapters/http/openapi_v1/admission.py \
        src/backend/tests/community/adapters/http/openapi_v1/test_app_only_refusals.py
git commit -m "feat(backend): require a human caller for bot publication"
```

---

### Task 11: Unify the singlebox signing key and prove the round trip

**Files:**
- Modify: `scripts/ci/singlebox_coverage.sh`

- [ ] **Step 1: Confirm the mismatch**

Run:

```bash
rg -n "SINGLEBOX_GATEWAY_PRINCIPAL_SIGNING_KEY|AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE" \
   scripts/ci/singlebox_coverage.sh scripts/modules/gateway.sh scripts/modules/bcs.sh
```

Expected: `singlebox_coverage.sh` arms the Backend with `singlebox-gateway-principal-key-not-for-production`, while the other two default to `avernet-dev-signing-key-NOT-FOR-PROD`.

- [ ] **Step 2: Make one value reach all three consumers**

In `scripts/ci/singlebox_coverage.sh`, keep the single variable and inject it under both spellings when the stack starts:

```bash
  env SINGLEBOX_COVERAGE=1 SINGLEBOX_COVERAGE_DIR="$coverage_root/raw" OCB_SKIP_GIT_HOOKS=1 SINGLEBOX_MODEL_CONFIG_MODE="$model_config_mode" \
    AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE="$SINGLEBOX_GATEWAY_PRINCIPAL_SIGNING_KEY" \
    AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE="$SINGLEBOX_GATEWAY_PRINCIPAL_SIGNING_KEY" \
    BCS_DEBUG=true BCS_COVERAGE_FORCE_REBUILD="${BCS_COVERAGE_FORCE_REBUILD:-1}" \
```

- [ ] **Step 3: Prove the round trip in the acceptance suite**

Add a test to the Backend acceptance set (under `src/backend/tests/community/acceptance/`, alongside the existing BCS-publication acceptance module — grep for `bot_public` under that directory to find its home) that, against the running singlebox stack, mints a principal through `mint_owner_principal_for_bcs` and calls BCS's `GET /openapi/v1/collaboration/bots/{bot}` with it, asserting the call is **not** a 401:

```python
def test_backend_minted_principal_is_accepted_by_bcs():
    """One signing key across Backend, Gateway and BCS.

    A mocked BCS client proves nothing here: the failure mode is a signature
    mismatch between two processes, which only a real round trip can see.
    """
    token = mint_owner_principal_for_bcs("singlebox-user")

    response = _bcs_get("/openapi/v1/collaboration/bots/singlebox-user", token)

    assert response.status_code != 401
```

(Use the acceptance module's existing HTTP helper in place of `_bcs_get`; if none exists, use `httpx.get` against the stack's BCS base URL, which the acceptance harness already exposes.)

- [ ] **Step 4: Run the singlebox acceptance module**

Run: `cd src/backend && DEPLOY_PROFILE=test uv run pytest tests/community/acceptance -q -k publication`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/ci/singlebox_coverage.sh src/backend/tests/community/acceptance
git commit -m "fix(ci): one principal signing key across singlebox services"
```

---

### Task 12: Endpoint-level regression cases

**Files:**
- Modify: `src/backend/tests/community/endpoints/test_openapi_public_bcs.py`

- [ ] **Step 1: Add the not-found-shaped cases**

Append cases mirroring the file's existing `@endpoint_test` shape, binding the service method to raise the production error each branch owns:

```python
@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="bcs_native_bot_owned_by_another_user_surfaces_not_found",
    input=CaseInput(
        path_params={"bot_uuid": "bot_a9c86fe3"},
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={"user_id": _CALLER},
        json_body={"public_scope": "user"},
    ),
    seed=lambda world: (
        _boot_verifier(world),
        bind_failing_method(
            world,
            BotPublicServiceProtocol,
            "public_bcs_bot",
            BotNotFoundError("Bot not found: bot_a9c86fe3"),
        ),
    ),
    expect=ExpectError(
        status=404,
        json_contains={"code": 404000, "message": "Not found", "data": None},
    ),
)
def public_bcs_foreign_bot_is_not_found():
    """The existence-non-disclosure answer for a bot this caller does not own."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="missing_pending_block_write_surfaces_error",
    input=CaseInput(
        path_params={"bot_uuid": "bot_x"},
        headers={PRINCIPAL_HEADER: _principal()},
        query_params={"user_id": _CALLER},
        json_body={"public_scope": "user"},
    ),
    seed=lambda world: (
        _boot_verifier(world),
        bind_failing_method(
            world,
            BotPublicServiceProtocol,
            "public_bcs_bot",
            BotPublicServiceError("failed to record the pending approval block"),
        ),
    ),
    expect=ExpectError(
        status=500,
        json_contains={"code": 500000, "message": "Publish failed", "data": None},
    ),
)
def public_bcs_block_write_failure_is_500():
    """An unrecorded block fails the publish instead of silently reporting a
    ticket the state machine would later refuse."""
```

- [ ] **Step 2: Run the full endpoint suite**

Run: `cd src/backend && uv run pytest tests/community/endpoints -q`

Expected: PASS.

- [ ] **Step 3: Run the whole affected test set**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public tests/community/endpoints tests/community/adapters/http/openapi_v1 tests/community/contracts tests/community/architecture tests/community/di -q`

Expected: PASS, no regressions.

- [ ] **Step 4: Check the file-size gate**

Run: `wc -l src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py src/backend/src/agentclaw/community/core/bot_public/publication_channel.py src/backend/src/agentclaw/community/core/gateway_principal/minter.py src/backend/src/agentclaw/community/core/bot_management/services/bcn_service.py`

Expected: the two new files and `bcn_service.py` are under 1,000 lines. `bot_public_service.py` was already over the limit before this change; if CI's allowlist does not already carry it, add it there with a follow-up split noted in the PR -- do not split it in this change.

- [ ] **Step 5: Commit**

```bash
git add src/backend/tests/community/endpoints/test_openapi_public_bcs.py
git commit -m "test(backend): cover publication not-found and block-write failures"
```

---

## Manual verification (not CI)

After the automated suite is green, run one real round trip against a local
singlebox stack (the path that exercises the signing key, the mint, and BCS's
`created_by` check together):

1. `scripts/singlebox.sh --standalone start all` (or the coverage entrypoint).
2. Register a BCS-native bot in the local BCS and note its `bot_uuid`.
3. `POST /openapi/v1/collaboration/bots/<bot_uuid>/public?user_id=<owner>` with a
   real gateway principal header, through the gateway.
4. Expect `200` with `state` in `{"SKIPPED", "COMPLETED"}` (community has no
   workflow), and, reading the bot back over BCS V1, `public_user_approval.status
   == "AGREE"` plus `user_visibility` flipped to the requested value.
5. Confirm the same request against a bot whose `created_by` is another user
   answers `404000 "Not found"`.

Record the observed output in the PR's Validation section.

---

## Self-Review

**Spec coverage**

| Spec section | Task |
| --- | --- |
| Admission is Human-only | Task 10 |
| Principals (re-address / mint) | Tasks 1, 5, 9 |
| Minted JWT wire shape | Task 1 (claims, `kid`, `typ`, integer `iat`/`exp`, config reuse) |
| Read fallback + `kind`/`created_by` gates | Task 6 |
| Synchronous writes | Task 7 |
| Asynchronous callback guards + state machine | Tasks 3, 8, 9 |
| Corroboration contract | Task 8 |
| Orphan prevention | Task 7 (`_cancel_started_ticket`, propagated failures) |
| Concurrency (verify-and-rewrite) | Task 4 |
| Compatibility: singlebox key | Task 11 |
| Testing incl. the review's ten cases | Tasks 1-12 (app-only: 10; Human row: 6; ac_bots hit with BCS mismatch: 6; persist failure compensation: 7; two concurrent scopes: 4; opposing callbacks: 4 + 9; query/decision mismatch: 9; unknown `last_operate`: 3 + 9; minted token vs real BCS: 1 + 11; singlebox keys: 11) |

**Type consistency:** `public_bcs_bot(..., principal_token: str)` is used by the router (Task 5) and the service (Task 6); `handle_public_approval_callback(..., principal_token=None)` by the inline caller (Task 7) and the antprocess route (unchanged, passing none); `BcsPublicationChannel.write(..., expect_block_status: Optional[str])` by both writers (Tasks 4, 7, 9); `PendingBlock`, `BLOCK_KEY_BY_SCOPE`, `VISIBILITY_FIELD_BY_SCOPE`, `VIEW_SCOPE_DEPS_KEY_BY_SCOPE`, `ALLOWED_DECISIONS`, `DEFAULT_VISIBILITY`, `ConflictingDecision` are defined once in `publication_channel.py` and imported, never restated.

**Placeholders:** none. Every step carries the code or the exact command it needs.
