"""Bench namespace is independent of the Bot execution identity.

Missing role-specific owners intentionally preserve legacy/internal/Singlebox inputs.
"""
import json


def bench_owner(args, role):
    return str(getattr(args, f"{role}_bench_owner_id", "") or getattr(args, "owner_id", "") or "")


def frozen_templates(pinned, owner, domain, *, scoped=False):
    key = json.dumps([owner, domain], ensure_ascii=False, separators=(",", ":")) if scoped else domain
    return pinned.get(key)
