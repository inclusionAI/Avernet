# ── Docker image build + prod-mode integration test ─────────────────────
#
# Builds the baas image (via docker/build-image.sh) and brings up a
# MariaDB + Redis + baas stack (docker-compose.baas-test.yml) to verify
# the image boots under SERVER_ENV=prod and answers /health. The compose file
# and its orchestration script live under src/baas/docker-test/.

docker-test-script := 'docker-test/test-baas.sh'

# Build the baas:local image, start MariaDB + Redis + baas, and wait
# for /health. Pass extra args through (e.g. just test-docker down).
test-docker *args='':
    @./{{docker-test-script}} {{args}}

# Build only the baas:local image.
test-docker-build:
    @./{{docker-test-script}} build

# Start MariaDB + Redis + baas (builds the image first) and wait for /health.
test-docker-up:
    @./{{docker-test-script}} up

# Tear down the test stack.
test-docker-down:
    @./{{docker-test-script}} down

# Show stack status and probe /health.
test-docker-status:
    @./{{docker-test-script}} status
