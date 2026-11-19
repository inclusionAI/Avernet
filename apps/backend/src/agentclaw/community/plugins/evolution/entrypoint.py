"""Deprecated worker command; use avernet_evolution.plugins.entrypoint."""
from avernet_evolution.plugins.entrypoint import *  # noqa: F403

if __name__ == "__main__":
    raise SystemExit(main_from_env())
