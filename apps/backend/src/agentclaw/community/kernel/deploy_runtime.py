"""``DeployRuntime`` — which container a deployment runs its bots in.

Lives in ``kernel`` because both sides of the deploy-composer seam need it and
neither can import the other: the composers under
``core/service_bot/services/deploy`` name themselves with it, and the DI layer
reads it out of config to pick one. ``kernel`` imports nothing, so it is the
one place both can reach.
"""
from __future__ import annotations

from enum import StrEnum


class DeployRuntime(StrEnum):
    """The bot container image + storage substrate a deployment runs.

    The value is what appears in config as ``baas.deploy_runtime`` and what
    ``DeployConfigComposer.name`` reports, so there is one spelling rather than
    a config string that has to be kept in sync with a class name.
    """

    #: The managed bot image: a four-step boot chain over ``/home/admin/bin``
    #: scripts, NAS mount points, and a NAS storage volume per bot. Every
    #: deployment that exists today runs this.
    MANAGED = "managed"

    #: The open-source engine image on managed Kubernetes / elastic container
    #: instances: the image carries its own entrypoint and the storage
    #: substrate is object storage. Not yet implemented — see
    #: ``AckDeployConfigComposer``.
    ACK = "ack"
