# ── Mesh (MOSN/Layotto) ──────────────────────────────────────────────────────

mesh-start:
    #!/bin/bash
    set -e
    if meshboot status -m binary -a agentclaw 2>&1 | grep -q "status is healthy"; then
        echo "✓ MOSN is already running and healthy"
    else
        echo "Starting MOSN..."
        meshboot start -m binary -a agentclaw > /dev/null 2>&1
        echo "✓ MOSN started successfully"
    fi

mesh-status:
    #!/bin/bash
    status=$(meshboot status -m binary -a agentclaw 2>&1)
    if echo "$status" | grep -q "status is healthy"; then
        echo "✓ MOSN is running and healthy"
    else
        echo "✗ MOSN is not running, run 'just mesh-start' to start"
    fi

mesh-stop:
    #!/bin/bash
    if meshboot status -m binary -a agentclaw 2>&1 | grep -q "status is healthy"; then
        echo "Stopping MOSN..."
        meshboot stop -m binary -a agentclaw > /dev/null 2>&1
        echo "✓ MOSN stopped successfully"
    else
        echo "✗ MOSN is not running"
    fi