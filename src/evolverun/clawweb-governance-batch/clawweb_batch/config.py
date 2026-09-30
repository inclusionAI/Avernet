"""Validated configuration and composition-boundary secret loading."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    project: str
    clawweb_url: str
    output_dir: Path
    state_dir: Path
    nas_roots: tuple[Path, ...]
    llm_config_file: Path
    window_days: int = 7
    lookback_days: int = 14
    max_data_age_days: int = 3
    top_per_lane: int = 5
    sessions_per_bot: int = 24
    verification_limit: int = 10
    verification_sessions: int = 100
    timeout_seconds: int = 180
    analysis_max_tokens: int = 12000
    max_message_bytes: int = 2_000_000
    allow_writes: bool = False
    rejection_cooldown_days: int = 15


def service_url(value: str, *, origin: bool = False) -> str:
    u = urlsplit(value)
    if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password:
        raise ValueError("invalid service URL")
    if u.query or u.fragment or (origin and u.path not in {"", "/"}):
        raise ValueError("service URL must not contain query/fragment or origin path")
    return value.rstrip("/")


def load_config(path: Path) -> Config:
    raw = json.loads(path.read_text())
    allowed = set(Config.__dataclass_fields__)
    required = {"project", "clawweb_url", "output_dir", "state_dir", "nas_roots", "llm_config_file"}
    if not isinstance(raw, dict) or set(raw) - allowed or required - set(raw):
        raise ValueError("unknown or missing batch config fields")
    import re
    if not isinstance(raw["project"], str) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", raw["project"]):
        raise ValueError("invalid ODPS project")
    raw["clawweb_url"] = service_url(raw["clawweb_url"], origin=True)
    for name in ("output_dir", "state_dir", "llm_config_file"):
        p = Path(raw[name]).expanduser()
        raw[name] = (p if p.is_absolute() else path.parent / p).resolve()
    if not isinstance(raw["nas_roots"], list) or not raw["nas_roots"]:
        raise ValueError("nas_roots must contain configured absolute directories")
    raw["nas_roots"] = tuple(Path(p) for p in raw["nas_roots"])
    if any(not p.is_absolute() or ".." in p.parts for p in raw["nas_roots"]):
        raise ValueError("nas_roots must be absolute without parent traversal")
    cfg = Config(**raw)
    for name in allowed - required - {"allow_writes"}:
        v = getattr(cfg, name)
        if type(v) is not int or v < 1:
            raise ValueError(f"{name} must be a positive integer")
    if type(cfg.allow_writes) is not bool:
        raise ValueError("allow_writes must be boolean")
    if cfg.window_days > cfg.lookback_days or cfg.lookback_days > 31:
        raise ValueError("window_days <= lookback_days <= 31 required")
    if cfg.top_per_lane > 50 or cfg.verification_limit > 100 or cfg.verification_sessions > 500:
        raise ValueError("batch workload exceeds supported bounds")
    if not 5 <= cfg.sessions_per_bot <= 100:
        raise ValueError("sessions_per_bot must be between 5 and 100")
    if cfg.rejection_cooldown_days > 90:
        raise ValueError("rejection_cooldown_days must be <= 90")
    return cfg


def load_deployment(path: Path) -> dict:
    # Existing AIStudio deployment YAML is read in place; secrets never copied to artifacts.
    if path.suffix == ".json":
        value = json.loads(path.read_text())
    else:
        import yaml  # Provided by the existing AIStudio runner, not required for local tests.
        value = yaml.safe_load(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("deployment configuration must be an object")
    return value


def load_llm(path: Path) -> dict:
    llm = load_deployment(path).get("llm", {})
    for key in ("base_url", "model", "api_key"):
        if not isinstance(llm.get(key), str) or not llm[key].strip():
            raise ValueError(f"missing deployment llm.{key}")
    return {"base_url": service_url(llm["base_url"]), "model": llm["model"], "api_key": llm["api_key"]}
