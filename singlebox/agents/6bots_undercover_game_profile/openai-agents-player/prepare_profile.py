"""Prepare two Singlebox profiles that keep the original Bot gateway ports."""

import argparse
import json
from pathlib import Path


def write_profile(output: Path, original: Path, manifest: dict) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for bot in manifest["bots"]:
        source = original / bot["source"]
        link = output / bot["source"]
        if link.is_symlink() and link.resolve() == source:
            continue
        link.symlink_to(source, target_is_directory=True)
    target = output / "bots.json"
    pending = output / "bots.json.tmp"
    pending.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(target)


def prepare(output: Path) -> None:
    original = Path(__file__).resolve().parent.parent
    manifest = json.loads((original / "bots.json").read_text(encoding="utf-8"))
    bots = manifest["bots"]
    matches = [index for index, bot in enumerate(bots) if bot["source"] == "player-xiaoman"]
    if len(matches) != 1:
        raise ValueError("Expected exactly one player-xiaoman in the source profile")
    slot = matches[0]
    # Singlebox calculates each port from its index. Preserve the gap at Xiaoman's port.
    write_profile(output / "before-xiaoman", original, {**manifest, "bots": bots[:slot]})
    after_port = manifest["port_start"] + (slot + 1) * manifest["port_step"]
    write_profile(output / "after-xiaoman", original, {
        **manifest, "port_start": after_port, "bots": bots[slot + 1 :],
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    prepare(parser.parse_args().output.resolve())
