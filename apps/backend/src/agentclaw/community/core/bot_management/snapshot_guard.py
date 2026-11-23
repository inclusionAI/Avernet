import hashlib
import json
from decimal import Decimal, InvalidOperation


class ManagedSnapshotConflictError(RuntimeError):
    """The draft snapshot changed or the requested update would downgrade it."""


class ManagedBotInputError(ValueError):
    """A managed draft restart request is incompatible with its target."""


class ManagedSnapshotPersistenceError(RuntimeError):
    """A conditional snapshot write failed without exposing its sensitive input."""


def template_snapshot_hash(config):
    document = json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(document.encode('utf-8')).hexdigest()


def _version(config):
    value = config.get('template_version_id')
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = Decimal(str(value))
        return int(number) if number.is_finite() and number >= 0 and number == number.to_integral_value() else None
    except InvalidOperation:
        return None


def validate_snapshot_write(current, *, candidate, expected_hash):
    if template_snapshot_hash(current) != expected_hash or current.get('template_key') != candidate.get('template_key'):
        raise ManagedSnapshotConflictError('managed snapshot changed')
    old_version, new_version = _version(current), _version(candidate)
    if old_version is not None and (new_version is None or new_version < old_version):
        raise ManagedSnapshotConflictError('managed snapshot cannot be downgraded')
