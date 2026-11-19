"""Image-baked 01–06 acceptance strategy; no Backend DI or extra injected env.

Checks actual uid/rootfs/token mount before claiming a job. This is not a proof
of network enforcement, which must be verified independently in the cluster.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

from avernet_evolution.core.evolution_strategy.strategies.identity_hardening import (
    IdentityHardening,
)

from .entrypoint import main


def strategy():
    return IdentityHardening(max_verdict_polls=0)


def check_isolation():
    if os.getuid() == 0:
        raise RuntimeError("acceptance worker must not run as root")
    if Path("/var/run/secrets/kubernetes.io/serviceaccount/token").exists():
        raise RuntimeError("worker must not have a Kubernetes service token")
    try:
        with open("/rsi-root-write-probe", "x"):
            pass
    except OSError as exc:
        if exc.errno != errno.EROFS:
            raise RuntimeError("readonly rootfs was not demonstrated") from exc
    else:
        raise RuntimeError("worker rootfs is writable")
    print(
        json.dumps(
            {
                "event": "worker.isolation.checked",
                "uid": os.getuid(),
                "rootfs_readonly": True,
                "service_account_token": False,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    check_isolation()
    raise SystemExit(main({"acme/identity-hardening@1.0.0": strategy}))
