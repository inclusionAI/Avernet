"""Value types that make up the BaaS ``deploy_config`` payload.

They live here rather than in ``baas_service`` because both sides of the
deploy-composition seam need to construct them: ``BaasService`` assembles the
payload, and each :class:`~...deploy_config_composer.DeployConfigComposer`
produces the mounts and storage that go into it. Homing them in the service
would make every composer import the service that imports the composer.

``baas_service`` re-exports both dataclasses, so the call sites that import them
from there keep working.

The two concepts read similarly and are not the same thing:

* a :class:`MountPointEntry` **bind-mounts a directory that already exists** on
  the deployment's shared storage into the container;
* a :class:`Storage` asks BaaS to **provision and attach a volume of the bot's
  own**, which is where that bot's state lives across restarts.

A bot's payload normally carries several of the first and at most one of the
second.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Dict


class StorageType(StrEnum):
    """Backing store for a bot's own volume (:attr:`Storage.type`).

    One member today because NAS is the only store this platform provisions a
    bot volume on. It is an enum rather than a bare ``str`` so the answer to
    "what may I put here?" is in the type instead of in someone's memory; a
    deployment on a different substrate adds a member here.
    """

    #: Network-attached storage. BaaS resolves the share from
    #: :attr:`Storage.storage_id` and mounts it at :attr:`Storage.path`.
    NAS = "nas"


class MountPermission(StrEnum):
    """Access a mount point grants the container (:attr:`MountPointEntry.permission`).

    The values are the member *names*, not BaaS's own wire spelling (``ro`` /
    ``rw``). That is deliberate and load-bearing: BaaS's ``_convert_mount_points``
    special-cases exactly these two strings before falling back to
    ``MountPermission(value)``, and these are what this service has always sent.
    Switching the values to ``ro``/``rw`` would still be accepted by BaaS, but it
    would change the payload of every running bot for no gain.
    """

    #: The container may read the mount but not write to it.
    READ_ONLY = "READ_ONLY"

    #: The container may read and write. Writes land on the shared store and
    #: survive the container.
    READ_WRITE = "READ_WRITE"


@dataclass
class Storage:
    """The volume BaaS provisions and attaches for one bot.

    This is the bot's own state — its sessions or its home directory — as
    opposed to a :class:`MountPointEntry`, which mounts something that already
    exists. A composer returning ``None`` instead of one of these drops the
    ``storage`` key from the payload entirely, which is what a runtime that
    gives the container its own disk should send.

    A service bot on the managed image, running out of its sessions directory::

        Storage(
            type=StorageType.NAS,
            path="/home/admin/.openclaw/agents",
            storage_id="prod_staff_447172_openclaw_b1_{device_uuid}",
            quota="1Gi",
            permission="0777",
        )

    The same bot once the ``nas_mount`` whitelist moves it to its home
    directory — same volume family, different mount point inside the
    container::

        Storage(
            type=StorageType.NAS,
            path="/home/admin",
            storage_id="prod_staff_447172_openclaw_b1_{device_uuid}",
            quota="1Gi",
            permission="0777",
        )
    """

    type: StorageType
    """Which backing store to provision on — see :class:`StorageType`."""

    path: str
    """Absolute path the volume is mounted at **inside the container**.

    e.g. ``"/home/admin"`` for a home-directory bot, or
    ``"/home/admin/.openclaw/agents"`` for one running out of its engine's
    sessions directory.
    """

    storage_id: str
    """BaaS-side name of the volume — how BaaS finds the same one again on the
    next start, which is what makes a bot's state survive a restart.

    May contain the literal ``{device_uuid}`` placeholder, which BaaS
    substitutes per device: a multi-replica service bot needs one volume per
    replica rather than several containers writing the same files.
    e.g. ``"prod_staff_447172_openclaw_b1_{device_uuid}"``.
    """

    quota: str
    """Size, as a Kubernetes quantity string — e.g. ``"1Gi"``, ``"10Gi"``."""

    permission: str
    """POSIX mode the mounted directory is created with, as an octal string —
    e.g. ``"0777"``, ``"0755"``.

    Deliberately **not** an enum: this is a nine-bit mask (owner/group/other ×
    read/write/execute), so an enum would need 512 members to say what four
    characters already say precisely. The enum types above name a small, closed
    set of choices; this names a number.
    """

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式。

        ``type`` is coerced to a plain ``str``: the payload is serialized as
        JSON and a ``StrEnum`` would ride along as one only by virtue of
        subclassing ``str``. Being explicit keeps the wire contract independent
        of that detail.
        """
        return {
            "type": str(self.type),
            "path": self.path,
            "storage_id": self.storage_id,
            "quota": self.quota,
            "permission": self.permission,
        }


@dataclass
class MountPointEntry:
    """One directory bind-mounted into the bot's container.

    Platform-agnostic representation, converted to the runtime's own mount type
    at the BaaS boundary (K8s volumes on ACK, Arca ``MountPoint`` on the managed
    runtime). Keeps the domain model independent of any vendor SDK per D-01;
    field names match Arca's for clarity.

    The system directory, which every bot reads and none writes::

        MountPointEntry(
            remote_dir="/agentclaw-sys",
            local_dir="/mnt/sys",
            permission=MountPermission.READ_ONLY,
        )

    The bot's own data directory on shared storage::

        MountPointEntry(
            remote_dir="/bolt-data/staff/447172/b1",
            local_dir="/home/admin/nfs/bot-data",
            permission=MountPermission.READ_WRITE,
        )
    """

    remote_dir: str
    """Absolute path on the deployment's shared store — the source."""

    local_dir: str
    """Absolute path inside the container it appears at — the destination."""

    permission: MountPermission
    """Read-only or read-write — see :class:`MountPermission`."""

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式。

        ``permission`` is coerced to a plain ``str`` for the same reason
        :meth:`Storage.to_dict` coerces ``type``.
        """
        return {
            "remote_dir": self.remote_dir,
            "local_dir": self.local_dir,
            "permission": str(self.permission),
        }
