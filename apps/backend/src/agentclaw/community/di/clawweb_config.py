"""Deployment config for the clawweb host — the ``task_nav`` clone target.

Holds the ``ClawwebConfig`` dataclass OUT of ``di/config.py`` (which sits at
zero headroom under the 1000-line architecture cap) so this addition does not
push that shared config monolith over the threshold. The matching DI provider
lives in ``di/modules/clawweb_config_module.py`` (registered in
``di/container.py`` alongside ``ConfigModule``); re-exported from
``di/config.py`` so callers resolve it as ``cfg.ClawwebConfig``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClawwebConfig:
    """Clawweb host config — the ``task_nav`` bot_config kind's clone target.

    ``base_url`` is the clawweb origin this deployment reaches (the
    ``bot-workflow-permissions/clone`` endpoint), one canonical value supplied
    by the deployment overlay SOFAPy merges — prod / pre / the simulator's
    in-cluster clawweb service. Read by the ``clawweb_http_client`` HttpClient
    binding, never by name in code, so a deploy swaps hosts by editing one yaml
    line (the same pattern ``bcn`` / ``masa_agent_eval`` keep).

    Neutral empty default — the community build embeds no clawweb host; each
    env overlay sets ``clawweb.base_url``. Empty ⇒ the clone Port's POST has no
    host, and the ``task_nav`` handler's fire-and-log ``except`` logs it (apply
    is not aborted) — the degraded shape a bare/CI build sees, never a silent
    success.
    """

    base_url: str = ""
