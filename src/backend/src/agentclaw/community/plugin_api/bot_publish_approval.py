"""BotPublishApprovalPlugin -- strategy for the owner-permission bot
publish path.

Encapsulates the two modes of publishing a bot when the owner's
explicit permission is required:

- **No-workflow mode**: no external approval service; publish the bot
  immediately, same as if the caller had been the owner.
- **Workflow mode**: start an external approval workflow; the bot stays
  in PENDING until the approval callback fires.

The branch on "which mode am I in?" lives in the DI binding, not in
``BotPublicService``. The service hands the plugin a bag of callbacks
(``BotPublishCallbacks``) covering the service-level operations the
plugin may need (persist bot, archive approval, fall back to direct
publish, etc.), and the plugin runs the appropriate flow.

TODO(architecture): The callback bag below is a code smell. The plugin
shouldn't need to reach back into service internals (bot persistence,
approval archival, callback handlers) to do its job. Long-term options
to remove the coupling, in increasing order of refactor cost:

  1. **Result-object** style: the plugin returns a typed outcome
     (``DirectPublish`` / ``ApprovalPending`` / ``ApprovalAlreadyCompleted``)
     and the service executes it. Plugin stays mode-aware but stateless;
     service becomes a tiny dispatcher. Re-introduces a small typed
     branch in the service (acceptable — it's a data branch, not a
     mode branch).

  2. **Inject the repositories**: pass ``BotRepository`` and any other
     persistence Protocols into the plugin directly via DI rather than
     callbacks. Plugin becomes more autonomous but couples to the data
     layer.

  3. **FSM**: model the publish flow as a finite-state machine the
     plugin only updates; the service drives transitions and runs side
     effects. Most invasive; pays off if more states get added.

Punted for now — keeping the callback bag minimises blast radius for
the Rule 14 migration. Revisit when the next bot-publish state is
added (an obvious trigger).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol
from agentclaw.community.plugin_api.base import Plugin

# ``OperatorContext`` lives in ``core/`` and the plugins/ layer cannot
# import core/ (enforced by tests/test_architecture_compliance.py).
# Aliased to ``Any`` so the contract stays framework-agnostic — impls
# treat it as opaque and just pass it through to callbacks.
# TODO(architecture): if richer typing matters here, replace with a
# structural Protocol defined in plugins/ exposing the fields callbacks
# actually need (staff_id / nick_name / operator_name). Today no plugin
# impl reads the operator, so Any is honest.
OperatorContext = Any


@dataclass
class BotPublishCallbacks:
    """Service-level operations a publish-approval strategy may need.

    Each impl uses what it needs; unused fields are tolerated. See the
    TODO at the module top for the long-term plan to remove this bag.
    """

    # Publish the bot immediately (used by local strategy + by the prod
    # strategy's "permission_owner == caller" fallback path if needed).
    # Returns the updated bot dict.
    publish_directly: Callable[..., Dict[str, Any]]

    # Move any existing ``ext["public_approval"]`` into
    # ``public_approval_history`` before storing a new one.
    archive_approval: Callable[[Dict[str, Any], str], None]

    # Persist ``update_data`` onto the bot, optionally firing the
    # device-config-sync notification. Returns the updated bot dict
    # or None on failure.
    update_with_notification: Callable[..., Optional[Dict[str, Any]]]

    # Run the public-approval callback (used by prod strategy's
    # COMPLETED short-circuit). Returns the callback result dict.
    handle_approval_callback: Callable[[str, str, str, str], Dict[str, Any]]

    # Re-fetch a bot after a downstream mutation (e.g. after the
    # COMPLETED short-circuit). Returns the bot dict or None.
    refetch_bot: Callable[[str, str], Optional[Dict[str, Any]]]

    # Build the approval-context dict passed to the external workflow service
    # (publishHint / botSkills / botMcps). Prod-only.
    build_approval_context: Callable[
        [Dict[str, Any], Optional["OperatorContext"]], Dict[str, str]
    ]


class BotPublishApprovalPlugin(Plugin, Protocol):
    """Strategy for the owner-permission bot publish path."""

    def publish(
        self,
        *,
        bot: Dict[str, Any],
        ext: Dict[str, Any],
        bot_id: str,
        owner_id: str,
        operator_id: str,
        operator: Optional["OperatorContext"],
        public: str,
        permission_owner: str,
        friend_approval: str,
        access_mode: str,
        callbacks: BotPublishCallbacks,
    ) -> Dict[str, Any]:
        """Publish ``bot`` under the owner-permission flow.

        Returns the updated bot dict (post-publish or
        post-approval-registered).

        Raises:
            BotPublicServiceError / BotNotFoundError: per the existing
                service contract.
        """
        ...
