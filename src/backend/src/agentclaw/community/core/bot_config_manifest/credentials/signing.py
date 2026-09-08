"""Request signing for object-store credentials — the ``oss_aksk`` mechanism.

**Why this is a header injector and not a second fetch road.** The guarded
fetcher already owns everything a fetch needs to be safe: the size and time
ceilings, the redirect policy, the address checks, and per-hop re-authorization
against the credential's prefixes. The only thing an AK/SK fetch does
differently is *which headers it presents*, and those depend on the URL — which
is exactly the shape of the injector seam the fetcher already calls
(``headers_for(url)``). Building a parallel signed transport would mean a second
copy of every ceiling to keep in step with the first, for a difference of four
headers.

**The signature is botocore's, not ours.** ``SigV4Auth`` is the reference
implementation of the scheme every S3-compatible store speaks, it is already an
installed dependency, and hand-rolled request signing is a category of code that
fails silently and asymmetrically — a subtly wrong canonical request is a 403
against one store and an accepted request against another. What this module
contributes is the *binding*: which credential, which region, which request,
and the guarantee that nothing here can leak the secret key.

**A corp variant plugs in here.** :func:`sign_headers` is the seam; a store
speaking its own scheme (rather than SigV4) needs a second function beside this
one and a branch on the credential, not changes anywhere else.
"""
from __future__ import annotations

from typing import Mapping

#: What SigV4 binds a signature to when the credential names no region. Every
#: S3-compatible store accepts *a* region string and MinIO ignores which; AWS
#: itself requires the bucket's own, which is why the credential can name one.
DEFAULT_REGION = "us-east-1"

#: The service name in the credential scope. ``s3`` for every S3-compatible
#: store, which is the whole of what this build signs for.
_SERVICE = "s3"


def sign_headers(
    *,
    url: str,
    access_key_id: str,
    secret_access_key: str,
    region: str | None = None,
    method: str = "GET",
) -> Mapping[str, str]:
    """The headers that authenticate one object fetch, computed per request.

    Per request, never cached: a SigV4 signature is bound to a timestamp and to
    the exact URL, so a reused header set is either expired or authenticating a
    different request. That also makes rotation land on the very next fetch,
    which is the same observable contract the header mechanism has.

    Args:
        url: The absolute URL the platform is about to request. Already
            authorized against the credential's prefixes by the caller — this
            function signs, it does not decide.
        access_key_id: The key id. Travels in clear inside the signature.
        secret_access_key: The secret half. Used to derive the signing key and
            never placed in the returned mapping, in any form.
        region: The signing region, or ``None`` for :data:`DEFAULT_REGION`.
        method: The HTTP method being signed. The fetcher issues ``GET``.

    Returns:
        Every header the signature covers — ``Authorization``, ``X-Amz-Date``
        and ``X-Amz-Content-SHA256`` — to be presented together.
    """
    # Imported at call time: botocore is a heavy import, and a module-scope
    # import would pay it on every process that touches the credentials
    # package — including the ``PUT`` validator, which never signs anything.
    #
    # ``S3SigV4Auth``, not the generic ``SigV4Auth``: S3 requires
    # ``x-amz-content-sha256`` on every SigV4 request, and only the S3 variant
    # emits it. The generic one produces a signature AWS S3 rejects.
    from botocore.auth import S3SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    request = AWSRequest(method=method, url=url, headers={})
    S3SigV4Auth(
        Credentials(access_key_id, secret_access_key),
        _SERVICE,
        region or DEFAULT_REGION,
    ).add_auth(request)
    # Everything the signer added, and no allowlist. A signature covers a
    # specific set of headers; presenting a subset of them is not a weaker
    # request, it is an invalid one — and it would fail as an opaque 403 with
    # nothing pointing at the filter that caused it. The signer is the
    # authority on what its own signature needs.
    return dict(request.headers)


__all__ = ["DEFAULT_REGION", "sign_headers"]
