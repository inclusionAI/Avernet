"""Rule-25 conformance suite for the OssToNasRecordRepository contract.

The prod/local twins are now collapsed into one unified ORM body
(``plugins/oss_to_nas_record_repository.py``); this guards that
the single impl still exposes the full Protocol surface with compatible
signatures. Behavioural parity is covered by
``tests/plugins/test_oss_to_nas_unified.py``.
"""
import inspect

import pytest
from unittest.mock import MagicMock

from agentclaw.community.core.repository.protocols.devices import OssToNasRecordRepository
from agentclaw.community.core.repository.implementations.devices.oss_to_nas_record import OssToNasRecordRepository as UnifiedOssToNasRecordRepository

ALL_IMPLS = [UnifiedOssToNasRecordRepository]

# The contract methods (the Protocol's public, non-dunder surface).
PROTOCOL_METHODS = [
    name
    for name, _ in inspect.getmembers(
        OssToNasRecordRepository, predicate=inspect.isfunction
    )
    if not name.startswith("_")
]


@pytest.mark.parametrize("impl_cls", ALL_IMPLS)
def test_structurally_satisfies_protocol(impl_cls):
    """Every impl is a structural OssToNasRecordRepository."""
    assert isinstance(impl_cls(MagicMock()), OssToNasRecordRepository)


@pytest.mark.parametrize("impl_cls", ALL_IMPLS)
@pytest.mark.parametrize("method_name", PROTOCOL_METHODS)
def test_method_signature_matches_protocol(impl_cls, method_name):
    """Each impl method's parameter names match the Protocol's.

    Catches the Rule-25 failure mode: a method silently renamed /
    re-parameterised in one impl while the other (and callers) keep the
    old contract.
    """
    proto_sig = inspect.signature(getattr(OssToNasRecordRepository, method_name))
    impl_method = getattr(impl_cls, method_name, None)
    assert impl_method is not None, (
        f"{impl_cls.__name__} is missing contract method {method_name!r}"
    )
    impl_sig = inspect.signature(impl_method)
    assert list(impl_sig.parameters) == list(proto_sig.parameters), (
        f"{impl_cls.__name__}.{method_name} signature "
        f"{list(impl_sig.parameters)} != Protocol "
        f"{list(proto_sig.parameters)}"
    )


def test_protocol_surface_is_nonempty():
    """Sanity: the introspected contract surface isn't accidentally empty
    (which would make the parametrized parity test vacuous)."""
    assert set(PROTOCOL_METHODS) == {
        "get_record",
        "query_records_by_batch",
        "update_status",
        "insert_record",
        "update_record",
        "delete_record",
        "batch_update_status",
    }
