from pathlib import Path

import pytest

from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl


@pytest.mark.asyncio
async def test_sync_bindpaths_rejects_missing_source_before_mutating_targets(
    tmp_path: Path,
) -> None:
    plugin = OpenClawPluginImpl()
    valid_source = tmp_path / "skills-pool" / "skills-local" / "valid"
    valid_source.mkdir(parents=True)
    missing_source = tmp_path / "skills-pool" / "skills-local" / "missing"
    active_root = tmp_path / "skills"
    valid_target = active_root / "valid"
    missing_target = active_root / "missing"

    with pytest.raises(RuntimeError, match="bindpath source does not exist"):
        await plugin.sync_bindpaths(
            {
                "symlinks": [
                    {
                        "source": str(valid_source),
                        "target": str(valid_target),
                    },
                    {
                        "source": str(missing_source),
                        "target": str(missing_target),
                    },
                ]
            }
        )

    assert not valid_target.exists()
    assert not valid_target.is_symlink()
    assert not missing_target.exists()
    assert not missing_target.is_symlink()


@pytest.mark.asyncio
async def test_sync_bindpaths_creates_link_when_every_source_exists(
    tmp_path: Path,
) -> None:
    plugin = OpenClawPluginImpl()
    source = tmp_path / "skills-pool" / "skills-local" / "writing"
    source.mkdir(parents=True)
    target = tmp_path / "skills" / "writing"

    result = await plugin.sync_bindpaths(
        {
            "symlinks": [
                {
                    "source": str(source),
                    "target": str(target),
                }
            ]
        }
    )

    assert result["created"] == [str(target)]
    assert target.is_symlink()
    assert target.resolve() == source
