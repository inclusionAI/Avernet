#!/usr/bin/env bash
# Default: isolated SQLite/Qdrant acceptance, never the developer's running app.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
test_python="${PYTHON:-.venv/bin/python}"
export BCSFUSE_RUN_EXTERNAL_ACCEPTANCE=0
export BCSFUSE_RUN_MYSQL_INTEGRATION=0
case "${1:---isolated}" in
  --core|--core-mysql)
    if [[ "$1" == "--core-mysql" ]]; then
      export BCSFUSE_RUN_MYSQL_INTEGRATION=1
    fi
    # Current TC contracts exercise real local Card/Doc/index persistence and
    # controlled model providers. They do not certify a deployed service.
    exec "$test_python" -m pytest -q --tb=short \
      packages/search-core/tests \
      tests/contract/test_tc_catalog.py \
      tests/contract/test_tc_same_instance_migration.py \
      tests/contract/test_tc_legacy_transactions.py \
      tests/contract/test_tc_sync_contract.py \
      tests/contract/test_tc_http_compat.py \
      tests/contract/test_tc_fuse_modes.py \
      tests/contract/test_tc_review_regressions.py \
      tests/contract/test_tc_migration.py \
      tests/integration/test_isolated_runtime_acceptance.py \
      tests/integration/test_g9_core_acceptance.py \
      tests/integration/test_g2_conflict_alignment_flow.py \
      tests/integration/test_g5_expert_diagnosis_flow.py \
      tests/contract/test_composed_embedding_provider.py \
      tests/contract/test_composed_log_level.py \
      tests/contract/test_fusion_profile_store_wiring.py \
      tests/contract/test_fusion_response_projection.py \
      tests/contract/test_shared_search_application.py \
      tests/unit/bootstrap/test_app_factory.py \
      tests/unit/bootstrap/test_trust_gateway_auth.py \
      tests/unit/application/test_profile_merge_service.py \
      tests/unit/application/test_worker_candidate_recommendation_impl.py \
      tests/unit/application/test_group_fusion_service.py \
      tests/unit/utils/test_fuse_util.py \
      tests/contract/test_mysql_storage_clock_contract.py \
      tests/contract/test_runtime_acceptance_cleanup.py \
      tests/contract/test_acceptance_runner.py
    ;;
  --isolated|--mysql)
    if [[ "${1:-}" == "--mysql" ]]; then
      # Uses disposable UUID databases on the local test MySQL server.
      export BCSFUSE_RUN_MYSQL_INTEGRATION=1
    fi
    exec "$test_python" -m pytest -q --tb=short \
      tests/integration/test_isolated_runtime_acceptance.py \
      tests/contract/test_tc_catalog.py \
      tests/contract/test_tc_sync_contract.py \
      tests/integration/test_g9_core_acceptance.py \
      tests/contract/test_mysql_storage_clock_contract.py \
      tests/contract/test_runtime_acceptance_cleanup.py
    ;;
  --external)
    # This mode creates and removes only uniquely named acceptance workers.
    : "${BCSFUSE_ACCEPTANCE_URL:?Set BCSFUSE_ACCEPTANCE_URL to the approved test deployment}"
    : "${BCSFUSE_AUTH_TOKEN:?Set BCSFUSE_AUTH_TOKEN securely in the environment}"
    export BCSFUSE_RUN_EXTERNAL_ACCEPTANCE=1
    exec "$test_python" -m pytest -q --tb=short \
      tests/integration/test_opencore_runtime_real_services_e2e_core.py
    ;;
  *)
    echo "Usage: bash scripts/run_targeted_tests.sh [--core|--core-mysql|--isolated|--mysql|--external]" >&2
    exit 2
    ;;
esac
