"""Deprecated acceptance worker; canonical implementation is in apps/evolution."""
from avernet_evolution.plugins.smoke_entrypoint import check_isolation, main, strategy

if __name__ == "__main__":
    check_isolation()
    raise SystemExit(main({"acme/identity-hardening@1.0.0": strategy}))
