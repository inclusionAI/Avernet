"""Local-only dev-login page — arms the ``dev_cookie`` strategy's cookies.

NOT FOR PRODUCTION: this route mints a developer identity without any
credential check. It is the browser-facing half of the same local-only bargain
the ``dev_cookie`` strategy already makes (``SERVER_ENV`` local/dev/test +
``application-local.yaml``'s ``user: [dev_cookie, google]`` chain): opening the
page sets the ``staff_id``/``nick_name`` cookies that strategy resolves, so a
singlebox operator can log in by visiting a URL instead of learning to set
cookies with DevTools on whichever host they browse from.

Why the gateway hosts it: the gateway is the only hop the singlebox browser
actually reaches, and cookie jars are host-scoped but port-agnostic — a cookie
set on ``127.0.0.1:8889`` (this route) is sent to ``127.0.0.1:8000`` (the
frontend dev server) and to the forwarded ``/openapi/v1`` calls alike. The page
redirects to the frontend port passed as ``?next=`` so the operator lands back
in the UI with the identity in place.

The gate mirrors ``DevCookieUserStrategy.enabled_envs``: outside those
environments the route answers 404, indistinguishable from absent, so a
production deployment neiter advertises nor serves it.
"""

from __future__ import annotations

import os
import re
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)

# The enabled-envs twin of DevCookieUserStrategy. Read per request, not at
# import, so tests (and deployments) can set it after the app is built.
_ENABLED_ENVS = frozenset({"local", "dev", "test"})

# Staff ids are opaque tokens downstream (BCS treats a principal's user_id as
# a string), but the value lands in a Set-Cookie header, so the route admits
# only a cookie-safe charset and rejects anything that could smuggle
# attributes or a second header past the response.
_STAFF_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Nicknames are free text and ride in the cookie URL-quoted (the strategy
# ``unquote``s on read), so only control characters are structure-relevant:
# CRLF would split the header. Length-bound before quoting to keep the header
# line sane.
_NICK_MAX = 64

# ``?next=`` is a frontend port, never a URL: a full URL here would be an
# open redirect, and the page composes the redirect target itself.
_NEXT_PORT = re.compile(r"^[1-9][0-9]{0,4}$")

# Hostheaders we will echo back into the redirect/href. Host is attacker
# controlled in principle; confining it to a plain hostname keeps the
# refresh target a same-origin URL.
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")

_DEFAULT_STAFF_ID = "001"
_DEFAULT_NICK_NAME = "admin"
_DEFAULT_NEXT_PORT = "8000"


def _enabled() -> bool:
    return os.getenv("SERVER_ENV", "").strip().lower() in _ENABLED_ENVS


def _host_redirect_base(request: Request) -> str:
    # ``Host`` carries host[:port]; this route is reached on the gateway port,
    # and the continue link must swap in the frontend port instead.
    host = (request.headers.get("host") or "127.0.0.1").strip()
    candidate = host.rsplit(":", 1)
    if len(candidate) == 2 and candidate[1].isdigit():
        host = candidate[0]
    if not _HOST.match(host):
        host = "127.0.0.1"
    return f"http://{host}"


@router.get("/_dev/login")
async def dev_login(
    request: Request,
    staff_id: str = Query(default=_DEFAULT_STAFF_ID),
    nick_name: str = Query(default=_DEFAULT_NICK_NAME),
    next: str = Query(default=_DEFAULT_NEXT_PORT),
) -> HTMLResponse:
    if not _enabled():
        raise HTTPException(status_code=404)

    if not _STAFF_ID.match(staff_id):
        # Not a sanitizing rejection: a value that fails this check is trying
        # to carry cookie structure or length beyond what a staff id is.
        raise HTTPException(status_code=400, detail="staff_id must be [A-Za-z0-9_-]{1,64}")
    if not _NEXT_PORT.match(next):
        raise HTTPException(status_code=400, detail="next must be a port number")

    nick = nick_name.strip()[:_NICK_MAX]
    if any(ord(ch) < 0x20 or ch == "\x7f" for ch in nick):
        raise HTTPException(status_code=400, detail="nick_name must not contain control characters")

    continue_url = f"{_host_redirect_base(request)}:{next}/"

    response = HTMLResponse(
        content=_PAGE.format(
            staff=escape(staff_id),
            nick=escape(nick),
            url=escape(continue_url),
        )
    )
    # Lax, first-party, no Secure: this is a plain-http local dev identity.
    response.set_cookie("staff_id", staff_id, max_age=43200, samesite="lax")
    response.set_cookie("nick_name", quote(nick), max_age=43200, samesite="lax")
    return response


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0;url={url}">
<title>Avernet dev login</title>
</head>
<body style="font-family: system-ui, sans-serif; margin: 4rem auto; max-width: 32rem;">
<h1>Dev login armed</h1>
<p>Identity: <code>{staff}</code> ({nick}) — cookies set on this host for dev_cookie auth.</p>
<p><a href="{url}">Continue to the workbench</a> (redirecting...)</p>
<p style="color: #888; font-size: 0.85rem;">Local-only page; it 404s outside SERVER_ENV local/dev/test and grants no production identity.</p>
</body>
</html>
"""
