"""Read a versioned team bundle without starting processes or reading credentials."""
import hashlib
import json
import re
from pathlib import Path
from core.case_loader import load_case


def validate_profile(source):
    source = Path(source)
    if source.is_symlink():
        raise ValueError("Profile root must not be a symlink")
    for item in source.rglob("*"):
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise ValueError("Profile must contain ordinary files/directories only")
    manifest = json.loads((source / "bots.json").read_text())
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("Expected profile manifest version 1")
    if set(manifest) - {"version", "name", "port_start", "port_step", "scopes", "bots", "entry_member"}:
        raise ValueError("Unsupported Profile fields; explicit mapping is required")
    if not isinstance(manifest.get("scopes", "production"), str):
        raise ValueError("Profile scopes must be a comma-separated string")
    bots = manifest.get("bots")
    if not isinstance(bots, list) or len(bots) < 2:
        raise ValueError("A group Bench requires at least two members")
    aliases, names = set(), set()
    allowed = {"source", "profile", "name", "summary", "domains", "skills", "runtime"}
    for bot in bots:
        if not isinstance(bot, dict) or set(bot) - allowed:
            raise ValueError("Unsupported member fields; do not silently discard configuration")
        alias, folder, name = bot.get("profile"), bot.get("source"), bot.get("name")
        for value in (alias, folder):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                raise ValueError("Member profile/source must be safe identifiers")
        if alias in aliases or not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("Member aliases and names must be nonempty and unique")
        aliases.add(alias); names.add(name)
        if bot.get("runtime", {"type": "openclaw"}) != {"type": "openclaw"}:
            raise ValueError("Only the OpenClaw runtime is implemented")
        for field in ("summary", "domains", "skills"):
            if not isinstance(bot.get(field, ""), str):
                raise ValueError(f"Member {field} must be a string")
        for file in ("AGENTS.md", "IDENTITY.md", "KNOWLEDGE.md"):
            if not (source / folder / file).is_file():
                raise ValueError(f"Missing member file: {folder}/{file}")
    entry = manifest.get('entry_member')
    if not isinstance(entry, str) or entry not in aliases:
        raise ValueError('Profile entry_member must refer to a member profile')
    return manifest


def profile_digest(source):
    source = Path(source)
    digest = hashlib.sha256()
    for file in sorted(source.rglob("*")):
        if file.is_file():
            name, content = str(file.relative_to(source)).encode(), file.read_bytes()
            digest.update(len(name).to_bytes(8, "big")); digest.update(name)
            digest.update(len(content).to_bytes(8, "big")); digest.update(content)
    return digest.hexdigest()


def load_bench(directory):
    directory = Path(directory).expanduser().resolve()
    case = load_case(directory / "case.md")
    source = directory / "profiles"
    manifest = validate_profile(source)
    case['entry_member'] = manifest['entry_member']
    case["case_sha256"] = hashlib.sha256((directory / "case.md").read_bytes()).hexdigest()
    return case, source, manifest
