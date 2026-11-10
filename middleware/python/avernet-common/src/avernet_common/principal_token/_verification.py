"""Verify a principal token's envelope and return its claims.

Checks everything the HS256 envelope can vouch for — signature against the
shared key, ``alg`` pinned, ``iss`` and ``aud`` value-checked, ``exp``/``iat``/
``iss`` present, ``exp`` not passed — and nothing about what the claims mean.

What comes back is **authenticated but not admitted**: a component holding the
shared key signed these claims, so they are not the caller's assertion, but
whether the ``principals`` set parses, whose tenant it names, and whether this
service serves that kind of caller are each service's own decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt

from avernet_common.principal_token._errors import PrincipalTokenError
from avernet_common.principal_token._keys import key_fingerprint

# Pinned, not read from the token. Honouring the token's own ``alg`` is the
# classic JWT downgrade: a forged header saying ``none`` (or naming an
# asymmetric algorithm whose "key" is our public material) would verify.
_ALGORITHMS = ("HS256",)

# Clock skew allowance. The gateway's default TTL is 60s, so two containers a few
# seconds apart would otherwise reject live tokens. Fixed, not configurable: a
# knob here is a knob that can be set to hours.
_LEEWAY_SECONDS = 5

# Claims that must be present. ``aud``/``iss`` are also value-checked; requiring
# them explicitly means a token that simply omits one is rejected rather than
# skipping the check.
_REQUIRED_CLAIMS = ("exp", "iat", "iss")

# How much of a JOSE header field reaches a log line. The header is unverified
# attacker-controlled input, so it is clipped before it is ever formatted.
_MAX_HEADER_FIELD = 32


@dataclass(frozen=True)
class PrincipalVerifierConfig:
    """What a received principal token must satisfy to be believed.

    ``audience`` names **this component** — a token addressed to another
    upstream is not replayable here. ``issuer`` names the component(s) trusted
    to sign for it: one name, or a tuple of them when more than one signer
    calls this component (the gateway and a peer service, say). ``audience``
    may also be a tuple, accepted when the token names any one of them.

    ``audience`` is checked only when ``verify_audience`` is set; ``issuer`` is
    always checked.

    ``signing_key`` is the shared HMAC secret. An empty key means the deployment
    has not been given one, and every verification then fails closed rather
    than accepting unsigned identity.
    """

    signing_key: str
    audience: str | tuple[str, ...]
    issuer: str | tuple[str, ...]
    verify_audience: bool = True

    @property
    def key_fingerprint(self) -> str:
        """This config's key as :func:`key_fingerprint` renders it for a log."""
        return key_fingerprint(self.signing_key)


def decode_principal_token(
    token: str, verifier: PrincipalVerifierConfig
) -> dict[str, Any]:
    """Return ``token``'s claims once its envelope checks out.

    Raises:
        PrincipalTokenError: on any failure — no key configured, empty token,
            bad signature, wrong ``alg``/``iss``/``aud``, a required claim
            missing, or expired. The message names the failure and this
            component's expectations, for the log.
    """
    if not verifier.signing_key:
        # No key means we cannot tell a genuine token from a forged one. The
        # only safe reading of "unconfigured" is "trust nothing".
        raise PrincipalTokenError("no principal signing key is configured")
    if not token:
        raise PrincipalTokenError("empty principal token")

    options: dict[str, Any] = {"require": list(_REQUIRED_CLAIMS)}
    decode_kwargs: dict[str, Any] = {
        "algorithms": list(_ALGORITHMS),
        "issuer": verifier.issuer,
        "leeway": _LEEWAY_SECONDS,
        "options": options,
    }
    if verifier.verify_audience:
        decode_kwargs["audience"] = verifier.audience
    else:
        options["verify_aud"] = False
    try:
        return jwt.decode(token, verifier.signing_key, **decode_kwargs)
    except jwt.PyJWTError as exc:
        # PyJWT's own message already separates the failure modes ("Signature
        # verification failed" vs "Signature has expired" vs "Invalid
        # audience"), so it is kept verbatim. What it cannot say is *which key
        # and which contract* we judged the token against — and that is what
        # turns an unattributable rejection into a diagnosis.
        #
        # The two halves of the suffix have very different standing, and the
        # wording keeps them apart. Our key fingerprint, audience and issuer
        # come from this process's own configuration: trustworthy, and the
        # thing to reason from. The JOSE header comes from the request and is
        # unauthenticated — a forger sets ``kid`` to whatever makes their token
        # look legitimate — so it is labelled as caller-supplied rather than
        # presented as provenance.
        raise PrincipalTokenError(
            f"principal token rejected: {exc} "
            f"[verifier key fp={verifier.key_fingerprint}, "
            f"expects aud={verifier.audience!r} iss={verifier.issuer!r} "
            f"verify_aud={verifier.verify_audience}; "
            f"{_unverified_token_header(token)}]"
        ) from exc


def _clip(value: object) -> str:
    """Render an unverified header field for a log line, bounded and escaped.

    ``repr`` rather than the bare string: it escapes newlines and control
    characters, so a crafted ``kid`` cannot forge extra log lines around itself.
    """
    text = str(value)
    if len(text) > _MAX_HEADER_FIELD:
        text = text[:_MAX_HEADER_FIELD] + "…"
    return repr(text)


def _unverified_token_header(token: str) -> str:
    """Render the token's JOSE header for a log line, marked as unverified.

    **This is a hint, never evidence of provenance, and the log line says so.**
    A failed signature means nothing about the token has been authenticated —
    including its header — so anyone able to reach this component can put
    ``alg: HS256, kid: bare`` on a token they minted themselves. Reading
    ``kid=bare`` as "our gateway with the wrong key" is exactly the inference a
    forger gets for free.

    What it is good for is the cheap negative: an unexpected ``alg``, an
    unfamiliar ``kid``, a header that will not parse at all. **The
    authoritative signal is the pair of boot fingerprints**, which come from
    each component's own configuration rather than from the request.

    Used for nothing but this string: no decision reads it, ``alg`` stays
    pinned to :data:`_ALGORITHMS`, and :func:`_clip` bounds and escapes it. Only
    the header is read — never the payload.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return "unparseable JOSE header"
    return (
        "unverified caller-supplied header "
        f"alg={_clip(header.get('alg'))} kid={_clip(header.get('kid'))}"
    )
