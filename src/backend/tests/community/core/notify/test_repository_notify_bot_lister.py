"""Unit tests for ``RepositoryNotifyBotLister``.

Covers the owner-own active-binding path AND the collaborator fold-in:
collaborator bots must be enumerated via ``CollaboratorRepository.list_by_user``
with their sandbox resolved from the owner's active binding
(``get_active_by_bot_and_owner``), deduplicated against owner bots.
"""
from unittest.mock import MagicMock

from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRecord,
    CollaboratorRole,
)
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.notify.bot_lister import RepositoryNotifyBotLister
from agentclaw.community.core.notify.protocol import NotifyTarget


def _binding(
    *,
    device_id: str,
    sandbox_id: str | None = None,
    bolt_id: str,
    binding_id: int = 1,
) -> DeviceBindingRecord:
    """Build a minimal DeviceBindingRecord for tests."""
    props: dict = {}
    if sandbox_id is not None:
        props["sandbox_id"] = sandbox_id
    props["bolt_id"] = bolt_id
    return DeviceBindingRecord(
        id=binding_id,
        entity_id="u001",
        entity_type="staff",
        device_id=device_id,
        device_provider="arca",
        env="prod",
        device_props=props,
        status="ACTIVE",
        apply_reason=None,
        applied_by="u001",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=None,
        gmt_modified=None,
    )


def _collab(bot_id: str, owner_id: str, user_id: str = "u001") -> CollaboratorRecord:
    return CollaboratorRecord(
        bot_pk=0,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        user_name=None,
        role=CollaboratorRole.MEMBER,
        operator_id=owner_id,
    )


def _make_lister(
    *,
    bindings=None,
    bot_lookup=None,
    collaborator_records=None,
    binding_by_bot_owner=None,
):
    binding_repo = MagicMock()
    binding_repo.list_bindings.return_value = (
        len(bindings or []),
        list(bindings or []),
    )
    if binding_by_bot_owner is not None:
        binding_repo.get_active_by_bot_and_owner.side_effect = lambda **kw: (
            binding_by_bot_owner.get((kw["bot_id"], kw["owner_id"]))
        )
    else:
        binding_repo.get_active_by_bot_and_owner.return_value = None

    bot_repo = MagicMock()
    if bot_lookup is not None:
        bot_repo.get_by_id_and_owner.side_effect = lambda bot_id, owner_id: (
            bot_lookup.get((bot_id, owner_id))
        )
    else:
        bot_repo.get_by_id_and_owner.return_value = None

    collab_repo = None
    if collaborator_records is not None:
        collab_repo = MagicMock()
        collab_repo.list_by_user.return_value = list(collaborator_records)

    lister = RepositoryNotifyBotLister(
        binding_repo=binding_repo,
        bot_repo=bot_repo,
        collaborator_repo=collab_repo,
    )
    return lister, binding_repo, bot_repo, collab_repo


class TestRepositoryNotifyBotLister:
    # ------------------------------------------------------------------
    # Owner-only path (backward compatible: no collaborator repo wired)
    # ------------------------------------------------------------------
    def test_owner_mappings_only_when_collaborator_repo_missing(self):
        lister, _, _, collab_repo = _make_lister(
            bindings=[
                _binding(device_id="dev1", sandbox_id="sb1", bolt_id="bot1"),
                _binding(device_id="dev2", sandbox_id="sb2", bolt_id="bot2"),
            ],
            bot_lookup={
                ("bot1", "u001"): {"bot_name": "Alpha"},
                ("bot2", "u001"): {"bot_name": "Beta"},
            },
            collaborator_records=None,  # collaborator repo not provided
        )
        assert collab_repo is None

        result = lister.list_bot_mappings("u001")

        assert result == [
            NotifyTarget("bot1", "Alpha", "u001", "sb1"),
            NotifyTarget("bot2", "Beta", "u001", "sb2"),
        ]

    def test_owner_bindings_without_sandbox_are_skipped(self):
        lister, _, _, _ = _make_lister(
            bindings=[
                # device_id but no sandbox in props → sandbox falls back to device_id
                _binding(device_id="dev1", sandbox_id=None, bolt_id="bot1"),
                # no device_id and no sandbox → skipped entirely
                _binding(device_id="", sandbox_id=None, bolt_id="bot2"),
            ],
            bot_lookup={
                ("bot1", "u001"): {"bot_name": "Alpha"},
            },
        )
        result = lister.list_bot_mappings("u001")
        # bot_id comes from device_props.bolt_id; sandbox falls back to device_id
        assert result == [NotifyTarget("bot1", "Alpha", "u001", "dev1")]

    # ------------------------------------------------------------------
    # Collaborator fold-in
    # ------------------------------------------------------------------
    def test_collaborator_bots_are_included_with_owner_sandbox(self):
        lister, binding_repo, bot_repo, collab_repo = _make_lister(
            bindings=[
                _binding(device_id="dev1", sandbox_id="sb1", bolt_id="owned"),
                _binding(device_id="dev1", sandbox_id="sb1", bolt_id="owned"),
            ],
            bot_lookup={
                ("owned", "u001"): {"bot_name": "Mine"},
                ("cobra", "ownerA"): {"bot_name": "Collab"},
            },
            collaborator_records=[_collab("cobra", "ownerA")],
            binding_by_bot_owner={
                ("cobra", "ownerA"): _binding(
                    device_id="devX", sandbox_id="sbCobra", bolt_id="cobra"
                ),
            },
        )

        result = lister.list_bot_mappings("u001")

        assert NotifyTarget("owned", "Mine", "u001", "sb1") in result
        assert NotifyTarget("cobra", "Collab", "ownerA", "sbCobra") in result

        # collaborator binding resolved via owner's active binding (owner = ownerA)
        binding_repo.get_active_by_bot_and_owner.assert_any_call(
            bot_id="cobra", owner_id="ownerA"
        )
        # bot name for the collaborator bot looked up against the *owner*, not the
        # collaborator user
        bot_repo.get_by_id_and_owner.assert_any_call("cobra", "ownerA")
        collab_repo.list_by_user.assert_called_once()

    def test_collaborator_duplicate_with_owner_is_deduped(self):
        # bot "shared" is owned by u001 AND u001 is recorded as a collaborator →
        # must appear exactly once, coming from the owner path.
        lister, binding_repo, _, _ = _make_lister(
            bindings=[
                _binding(device_id="dev1", sandbox_id="sb1", bolt_id="shared"),
            ],
            bot_lookup={
                ("shared", "u001"): {"bot_name": "Shared"},
                ("shared", "ownerA"): {"bot_name": "SharedOwner"},
            },
            collaborator_records=[_collab("shared", "ownerA")],
            binding_by_bot_owner={
                ("shared", "ownerA"): _binding(
                    device_id="devX", sandbox_id="sbX", bolt_id="shared"
                ),
            },
        )

        result = lister.list_bot_mappings("u001")

        assert result == [NotifyTarget("shared", "Shared", "u001", "sb1")]
        # The collaborator branch should not ask for the owner binding because the
        # bot was already seen.
        binding_repo.get_active_by_bot_and_owner.assert_not_called()

    def test_collaborator_bot_without_active_binding_is_skipped(self):
        lister, _, _, collab_repo = _make_lister(
            bindings=[],
            collaborator_records=[
                _collab("cbot1", "ownerA"),
                _collab("cbot2", "ownerB"),
            ],
            binding_by_bot_owner={
                # cbot1 has an active binding, cbot2 does not
                ("cbot1", "ownerA"): _binding(
                    device_id="devA", sandbox_id="sbA", bolt_id="cbot1"
                ),
            },
        )

        result = lister.list_bot_mappings("u001")

        assert result == [NotifyTarget("cbot1", "cbot1", "ownerA", "sbA")]
        collab_repo.list_by_user.assert_called_once()

    def test_collaborator_record_with_empty_owner_id_is_skipped(self):
        # A collaborator record with an empty owner_id cannot resolve the
        # owner's binding (device bindings belong to the owner); it must be
        # short-circuited before any binding/name lookup is attempted.
        lister, binding_repo, bot_repo, _ = _make_lister(
            bindings=[],
            collaborator_records=[
                _collab("cbot1", "ownerA"),
                _collab("cbot_empty", ""),  # missing owner_id
            ],
            binding_by_bot_owner={
                ("cbot1", "ownerA"): _binding(
                    device_id="devA", sandbox_id="sbA", bolt_id="cbot1"
                ),
            },
        )

        result = lister.list_bot_mappings("u001")

        assert result == [NotifyTarget("cbot1", "cbot1", "ownerA", "sbA")]
        # only the valid record triggers a binding lookup; the empty-owner
        # record is short-circuited.
        binding_repo.get_active_by_bot_and_owner.assert_called_once_with(
            bot_id="cbot1", owner_id="ownerA"
        )
        bot_repo.get_by_id_and_owner.assert_called_once_with(
            "cbot1", "ownerA"
        )

    def test_collaborator_bot_name_falls_back_to_bot_id(self):
        # bot_repo raises / returns None → name falls back to bot_id
        lister, binding_repo, bot_repo, _ = _make_lister(
            bindings=[],
            collaborator_records=[_collab("cbot1", "ownerA")],
            binding_by_bot_owner={
                ("cbot1", "ownerA"): _binding(
                    device_id="devA", sandbox_id="sbA", bolt_id="cbot1"
                ),
            },
        )
        bot_repo.get_by_id_and_owner.return_value = None

        result = lister.list_bot_mappings("u001")

        assert result == [NotifyTarget("cbot1", "cbot1", "ownerA", "sbA")]

    def test_collaborator_repo_failure_does_not_break_owner_path(self):
        lister, _, _, collab_repo = _make_lister(
            bindings=[
                _binding(device_id="dev1", sandbox_id="sb1", bolt_id="owned"),
            ],
            bot_lookup={("owned", "u001"): {"bot_name": "Mine"}},
            collaborator_records=[],
        )
        collab_repo.list_by_user.side_effect = RuntimeError("db down")

        result = lister.list_bot_mappings("u001")

        # owner path intact; collaborator branch degraded to empty (logged)
        assert result == [NotifyTarget("owned", "Mine", "u001", "sb1")]

    def test_collaborator_binding_lookup_failure_skips_that_bot(self):
        lister, binding_repo, _, collab_repo = _make_lister(
            bindings=[],
            collaborator_records=[
                _collab("cbot1", "ownerA"),
                _collab("cbot2", "ownerB"),
            ],
            binding_by_bot_owner={
                ("cbot1", "ownerA"): _binding(
                    device_id="devA", sandbox_id="sbA", bolt_id="cbot1"
                ),
                ("cbot2", "ownerB"): None,
            },
        )
        # cbot2's binding lookup raises → skipped, cbot1 still returned
        def _side(**kw):
            if kw["bot_id"] == "cbot2":
                raise RuntimeError("lookup boom")
            return _binding(device_id="devA", sandbox_id="sbA", bolt_id="cbot1")

        binding_repo.get_active_by_bot_and_owner.side_effect = _side

        result = lister.list_bot_mappings("u001")

        assert result == [NotifyTarget("cbot1", "cbot1", "ownerA", "sbA")]
        collab_repo.list_by_user.assert_called_once()
