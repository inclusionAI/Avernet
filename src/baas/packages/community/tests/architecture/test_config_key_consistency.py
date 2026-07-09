"""Architecture enforcement: config key consistency.

Verifies that ``configs/application.yaml`` and
``singlebox-configs/application.yaml`` expose the same set of
flattened dotted keys.  Any key present in one but not the
other will cause a test failure with a structured diff.

This prevents silent config drift when new keys are added to
only one of the two deploy-mode configs.
"""

from pathlib import Path

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_CONFIG_FILE = _PROJECT_ROOT / "configs" / "application.yaml"
_SINGLEBOX_CONFIG_FILE = _PROJECT_ROOT / "singlebox-configs" / "application.yaml"


def _flatten_keys(data: object, prefix: str = "") -> set[str]:
    """Recursively flatten a nested YAML dict into dotted-key strings.

    List items are skipped — we only care about config section structure, not
    individual list entries.
    """
    keys: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            dotted = f"{prefix}.{key}" if prefix else key
            keys.add(dotted)
            if isinstance(value, dict):
                keys |= _flatten_keys(value, dotted)
    return keys


def _format_diff(
    only_in_configs: set[str],
    only_in_singlebox: set[str],
) -> str:
    """Build a readable diff message."""
    parts: list[str] = []

    if only_in_configs:
        parts.append("Keys only in configs/application.yaml:")
        for key in sorted(only_in_configs):
            parts.append(f"  + {key}")

    if only_in_singlebox:
        parts.append("Keys only in singlebox-configs/application.yaml:")
        for key in sorted(only_in_singlebox):
            parts.append(f"  - {key}")

    parts.append(
        "\nTip: Add missing keys to singlebox-configs/application.yaml "
        "or remove stale keys from configs/application.yaml as appropriate."
    )
    return "\n".join(parts)


def test_config_keys_are_consistent() -> None:
    """Both config files must have the same set of flattened keys."""
    if not _CONFIG_FILE.exists():
        pytest.fail(f"Config file not found: {_CONFIG_FILE}")
    if not _SINGLEBOX_CONFIG_FILE.exists():
        pytest.fail(f"Singlebox config file not found: {_SINGLEBOX_CONFIG_FILE}")

    with _CONFIG_FILE.open() as fh:
        config_data = yaml.safe_load(fh)
    with _SINGLEBOX_CONFIG_FILE.open() as fh:
        singlebox_data = yaml.safe_load(fh)

    config_keys = _flatten_keys(config_data)
    singlebox_keys = _flatten_keys(singlebox_data)

    only_in_configs = config_keys - singlebox_keys
    only_in_singlebox = singlebox_keys - config_keys

    if only_in_configs or only_in_singlebox:
        diff = _format_diff(only_in_configs, only_in_singlebox)
        pytest.fail(
            f"Config key mismatch detected between:\n"
            f"  {_CONFIG_FILE}\n"
            f"  {_SINGLEBOX_CONFIG_FILE}\n\n"
            f"{diff}"
        )
