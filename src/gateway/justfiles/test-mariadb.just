# ── ASGI e2e against a local MariaDB (via the e2e-mariadb overlay) ─────────

# Defaults mirror configs/overlays/e2e-mariadb.yaml
mariadb-container := 'gateway-e2e-mariadb'
mariadb-image := 'mariadb:11'
mariadb-port := '33306'
mariadb-database := 'gateway_test'
mariadb-user := 'gateway'
mariadb-password := 'gatewaypass'

# Start the MariaDB container
start-mariadb:
    docker rm -f {{mariadb-container}} >/dev/null 2>&1 || true
    docker run -d --name {{mariadb-container}} \
        -p {{mariadb-port}}:3306 \
        -e MARIADB_ROOT_PASSWORD=rootpass \
        -e MARIADB_DATABASE={{mariadb-database}} \
        -e MARIADB_USER={{mariadb-user}} \
        -e MARIADB_PASSWORD={{mariadb-password}} \
        {{mariadb-image}} >/dev/null
    i=0; until \
        docker exec {{mariadb-container}} \
            mariadb -u{{mariadb-user}} -p{{mariadb-password}} \
            -e 'SELECT 1' {{mariadb-database}} >/dev/null 2>&1; do \
        i=$((i+1)); [ "$i" -ge 45 ] && { echo 'MariaDB did not become ready'; exit 1; }; \
        sleep 1; done
    echo "MariaDB ready ({{mariadb-container}} on :{{mariadb-port}})"

# Stop and remove the MariaDB container
stop-mariadb:
    docker rm -f {{mariadb-container}} >/dev/null 2>&1 || true

# Run ASGI e2e tests against a live MariaDB (starts and stops the container).
# When Docker/MariaDB is not available the tests are skipped, never failed.
test-e2e-mariadb:
    #!/bin/bash
    set -uo pipefail
    if ! command -v docker >/dev/null 2>&1; then
        echo "test-e2e-mariadb: docker not installed — skipping MariaDB e2e tests"
        exit 0
    fi
    if ! docker info >/dev/null 2>&1; then
        echo "test-e2e-mariadb: docker daemon not available — skipping MariaDB e2e tests"
        exit 0
    fi
    docker rm -f {{mariadb-container}} >/dev/null 2>&1 || true
    if ! docker run -d --name {{mariadb-container}} \
        -e MARIADB_ROOT_PASSWORD=rootpass \
        -e MARIADB_DATABASE={{mariadb-database}} \
        -e MARIADB_USER={{mariadb-user}} \
        -e MARIADB_PASSWORD={{mariadb-password}} \
        -p {{mariadb-port}}:3306 \
        {{mariadb-image}} >/dev/null 2> "${TMPDIR:-/tmp}/mariadb-start.err"; then
        echo "test-e2e-mariadb: cannot start MariaDB container — skipping MariaDB e2e tests"
        rm -f "${TMPDIR:-/tmp}/mariadb-start.err"
        exit 0
    fi
    trap 'docker rm -f {{mariadb-container}} >/dev/null 2>&1 || true' EXIT
    i=0; until \
        docker exec {{mariadb-container}} \
            mariadb -u{{mariadb-user}} -p{{mariadb-password}} \
            -e "SELECT 1" {{mariadb-database}} >/dev/null 2>&1; do \
        i=$((i+1)); [ "$i" -ge 45 ] && { echo "test-e2e-mariadb: MariaDB did not become ready — skipping"; exit 0; }; \
        sleep 1; done
    echo "MariaDB ready ({{mariadb-container}} :{{mariadb-port}})"
    SOFAPY_CONFIG_PATH="$(pwd)/configs" \
    SOFAPY_CONFIG_OVERLAY=e2e-mariadb \
    GATEWAY_RUN_MODE=bare \
        uv run pytest tests/e2e/asgi/ -v --color=yes