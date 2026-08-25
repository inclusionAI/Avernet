# ── Docker image build + prod-mode integration test ─────────────────────

docker-test-script := 'docker-test/test-proxy.sh'

test-docker *args='':
    @./{{docker-test-script}} {{args}}

test-docker-build:
    @./{{docker-test-script}} build

test-docker-up:
    @./{{docker-test-script}} up

test-docker-down:
    @./{{docker-test-script}} down

test-docker-status:
    @./{{docker-test-script}} status