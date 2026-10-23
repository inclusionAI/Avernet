"""Simulator AgentPass consent surfaces — the tcauthmng stand-in's pages (D8).

openspec ``add-bot-create-human-confirm`` D8: when the community composition
runs the ``ManualConsentPassportPlugin`` (``user_config.community_identity.
passport_mode: manual_consent``), bot creation parks at a human gate and the
build's own backend serves the consent experience the prod identity provider
would: a clickable page an iframe can open from any workbench, a listing for
approval scripts, and one decision endpoint that is the **only** way anything
headless-free can flip a pending application. The swarm never approves — these
surfaces stand in for the human's click, decided by DI.

Delivery only: every answer comes from the DI-resolved
``PassportManualConsentControl``. Wherever manual consent is off the process
holds the null control, and every route here answers 404 — "the app does not
branch on mode; DI decides". Routes are mode-inert, so the default composition
gains nothing but a router whose handlers refuse.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import PassportManualConsentControl


logger = get_logger()

router = APIRouter(prefix="/simulator/agentpass", tags=["simulator-agentpass"])

_MODE_OFF_DETAIL = (
    "manual-consent passport surfaces are off "
    "(user_config.community_identity.passport_mode is not manual_consent)"
)


class DecisionBody(BaseModel):
    """The human's verdict: required approve flag, optional reason."""

    approve: bool
    reason: str | None = None


def _control(request: Request) -> PassportManualConsentControl:
    """Resolve the live decision control, or refuse with the mode-off 404.

    The dependency fails soft on a composition that did not install the
    community identity module's control binding at all (a corp prod column,
    say) rather than 500ing: on such a deployment the manual-consent
    simulator genuinely does not exist, and 404 is also what the null
    control produces, so the wire contract is one answer everywhere.
    """
    control: PassportManualConsentControl | None = None
    injector = getattr(request.app.state, "injector", None)
    if injector is not None:
        try:
            control = injector.get(PassportManualConsentControl)
        except Exception:  # noqa: BLE001 — a missing binding is mode-off, not a fault
            logger.error(
                "[simulator-agentpass] PassportManualConsentControl unbound in "
                "this composition; manual consent surfaces stay off"
            )
            control = None
    if control is None or not getattr(control, "available", True):
        raise HTTPException(status_code=404, detail=_MODE_OFF_DETAIL)
    return control


@router.get("/consent", response_class=HTMLResponse, operation_id="simulator_agentpass_consent_page")
async def consent_page(
    app: str,
    control: PassportManualConsentControl = Depends(_control),
) -> HTMLResponse:
    """The human's page: application facts plus approve/reject buttons.

    This is the mock of the official consent page, so an ``iframe_url`` opened
    from a workbench behaves the way prod's does — click, decide, and the
    page tells its parent window via the ``AGENT_LICENSE_CONFIRM`` /
    ``AGENT_LICENSE_REJECT`` ``postMessage`` the existing authorization panels
    already listen for.
    """
    facts = control.get_application(app)
    if facts is None:
        raise HTTPException(status_code=404, detail="unknown passport application")
    return HTMLResponse(_render_consent_page(facts))


@router.get(
    "/applications",
    operation_id="simulator_agentpass_list_applications",
)
async def applications(
    bot_id: str | None = None,
    control: PassportManualConsentControl = Depends(_control),
) -> JSONResponse:
    """Every pending or decided application in this process, newest first.

    ``bot_id`` filters by bot, which is how an approval script can watch one
    specific creation without a server-side cursor: the listing is small (a
    simulator process's own registry only) and the JSON shape is the script
    contract pinned by the simulator bootstrap tooling.
    """
    return JSONResponse({"items": control.list_applications(bot_id=bot_id)})


@router.get(
    "/applications/{application_id}",
    operation_id="simulator_agentpass_get_application",
)
async def application_detail(
    application_id: str,
    control: PassportManualConsentControl = Depends(_control),
) -> JSONResponse:
    """One application's facts — the page-render shape, as JSON.

    Served so the ``redirect_url`` the plugin mints resolves on this same
    backend rather than dangling at an address no route answers.
    """
    facts = control.get_application(application_id)
    if facts is None:
        raise HTTPException(status_code=404, detail="unknown passport application")
    return JSONResponse(facts)


@router.post(
    "/applications/{application_id}/decision",
    operation_id="simulator_agentpass_decide",
)
async def decide(
    application_id: str,
    body: DecisionBody,
    control: PassportManualConsentControl = Depends(_control),
) -> JSONResponse:
    """Record the human's approve/reject verdict on a pending application.

    ``404`` when the application is unknown or the surfaces are off;
    ``409`` when it was already decided — the decision is a transition, not
    a value someone gets to rewrite.
    """
    from agentclaw.community.plugins.community.passport_consent import (
        PassportConsentAlreadyDecidedError,
    )

    try:
        status = control.decide(
            application_id, approve=body.approve, reason=body.reason
        )
    except PassportConsentAlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if status is None:
        raise HTTPException(status_code=404, detail="unknown passport application")
    return JSONResponse({"application_id": application_id, "status": status})


def _esc(value: Any) -> str:
    """HTML-escape a fact for the inline page."""
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _render_consent_page(facts: dict[str, Any]) -> str:
    """The whole consent page: facts, two buttons, and a result line.

    Inline styles and one inline script only — no external assets, so the page
    renders from a single response and works wherever the iframe opens.
    The script posts the decision endpoint and, once the server has recorded
    it, tells the embedding window what happened using the postMessage
    protocol the existing authorization panels already expect.
    """
    application_id = _esc(facts.get("application_id"))
    status = str(facts.get("status") or "")
    rows = "".join(
        f"<tr><th>{_esc(key)}</th><td id=\"fact-{_esc(key)}\">{_esc(value)}</td></tr>"
        for key, value in (
            ("bot_name", facts.get("bot_name")),
            ("bot_desc", facts.get("bot_desc")),
            ("engine_type", facts.get("engine_type")),
            ("bot_id", facts.get("bot_id")),
            ("application_id", facts.get("application_id")),
            ("owner_workno", facts.get("owner_workno")),
            ("created_at", facts.get("created_at")),
        )
    )
    decided = status != "PENDING"
    actions_block = (
        '<p class="note">This application was already decided.</p>'
        if decided
        else (
            "<button id=\"approve\" type=\"button\">Approve</button> "
            "<button id=\"reject\" type=\"button\">Reject</button> "
            "<label><input id=\"reason\" placeholder=\"optional reason\" /></label>"
        )
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Simulator AgentPass — manual consent</title>
<style>
 body {{ font-family: sans-serif; margin: 2rem auto; max-width: 41rem; color: #111;
        background: #f6f7f9; }}
 .card {{ background: #fff; border: 1px solid #e3e5e8; border-radius: 10px;
         padding: 1.4rem 1.6rem; box-shadow: 0 1px 2px rgba(0,0,0,.05); }}
 h1 {{ font-size: 1.15rem; }}
 table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
 th {{ text-align: left; width: 38%; vertical-align: top; white-space: nowrap; }}
 th, td {{ padding: .3rem .45rem; border-bottom: 1px solid #f0f1f3;
           font-size: .85rem; font-weight: 400; }}
 button {{ padding: .5rem 1.4rem; margin: .4rem .4rem .4rem 0; border-radius: 7px;
           border: 1px solid #c9cdd3; background: #fff; cursor: pointer;
           font-size: .95rem; }}
 #approve {{ background: #2563eb; border-color: #2563eb; color: #fff; }}
 #approve[disabled] {{ background: #cfd8e8; border-color: #cfd8e8; color: #fff; cursor: default; }}
 input {{ padding: .45rem .5rem; border-radius: 7px; border: 1px solid #c9cdd3;
          font-size: .85rem; width: 15rem; }}
 #result {{ margin-top: .8rem; font-size: .9rem; min-height: 1.2rem; }}
 .note {{ color: #62676d; }}
</style>
</head>
<body>
<div class="card">
 <h1>Simulator AgentPass — authorize this agent?</h1>
 <p class="note">You are standing in for the human approver. This page is the
 community build's mock of the official AgentPass consent page.</p>
 <table>{rows}</table>
 {actions_block}
 <p id="result" aria-live="polite"></p>
</div>
<script>
(function () {{
  var applicationId = {json.dumps(application_id)};
  var status = {json.dumps(status)};
  function settle(postType) {{
    var result = document.getElementById("result");
    result.textContent = "Waiting for the server…";
    var payload = {{ approve: postType === "AGENT_LICENSE_CONFIRM" }};
    var reasonEl = document.getElementById("reason");
    if (reasonEl && reasonEl.value) {{ payload.reason = reasonEl.value; }}
    fetch("/simulator/agentpass/applications/" + applicationId + "/decision",
          {{ method: "POST", headers: {{ "Content-Type": "application/json" }},
             body: JSON.stringify(payload) }})
      .then(function (resp) {{
        return resp.json().then(function (body) {{ return {{ ok: resp.ok, body: body }}; }});
      }})
      .then(function (outcome) {{
        if (!outcome.ok) {{
          result.textContent = "The server refused the decision: " +
            JSON.stringify(outcome.body);
          return;
        }}
        result.textContent = "Decision recorded — " + outcome.body.status + " (" +
          outcome.body.application_id + ")";
        var data = {{ success: outcome.body.status === "ISSUED",
                      status: outcome.body.status,
                      applicationId: outcome.body.application_id }};
        if (window.parent !== window) {{
          window.parent.postMessage({{ type: postType, data: data }}, "*");
        }}
        var buttons = document.querySelectorAll("button");
        for (var i = 0; i < buttons.length; i++) {{
          buttons[i].disabled = true;
        }}
      }})
      .catch(function (err) {{
        result.textContent = "The request failed: " + err;
      }});
  }}
  var approve = document.getElementById("approve");
  if (approve) {{
    approve.addEventListener("click", function () {{
      settle("AGENT_LICENSE_CONFIRM");
    }});
    document.getElementById("reject").addEventListener("click", function () {{
      settle("AGENT_LICENSE_REJECT");
    }});
  }}
}})();
</script>
</body>
</html>"""


__all__ = ["router"]