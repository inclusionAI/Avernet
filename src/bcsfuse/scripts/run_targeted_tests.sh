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
    # Search includes /recommend, used by catalog discovery. Fuse includes
    # its mode dispatch and shared-profile storage, not just route existence.
    # These tests use controlled model providers; they do not certify live LLMs.
    exec "$test_python" -m pytest -q --tb=short \
      tests/integration/test_isolated_runtime_acceptance.py \
      tests/integration/test_profile_activation_index_acceptance.py \
      tests/integration/test_worker_runtime_vector_sync.py \
      tests/contract/api/test_worker_api_contract.py \
      tests/contract/api/test_legacy_worker_api_contract.py \
      tests/contract/test_sync_identity_activation_contract.py \
      tests/contract/test_profile_identifier_content.py \
      tests/contract/test_composed_embedding_provider.py \
      tests/contract/test_composed_trace_contract.py \
      tests/contract/test_retrieval_logging.py \
      tests/contract/test_composed_log_level.py \
      tests/contract/test_auth_provider_contract.py \
      tests/contract/test_vector_metadata_filter_contract.py \
      tests/contract/test_legacy_fragment_identity.py \
      tests/contract/test_faiss_get_contract.py \
      tests/contract/test_mysql_storage_clock_contract.py \
      tests/integration/test_mysql_storage_timestamps.py \
      tests/integration/test_mysql_registry_delete.py \
      tests/unit/application/test_worker_vector_match_service.py \
      tests/unit/application/test_worker_candidate_recommendation_impl.py \
      tests/integration/test_registry_aware_filtering.py \
      tests/contract/api/test_fusion_api_contract.py \
      tests/contract/test_fusion_profile_store_wiring.py \
      tests/contract/test_fusion_response_projection.py \
      tests/unit/application/test_group_fusion_service.py \
      tests/unit/interfaces/test_run_fuse_threadpool.py \
      tests/integration/test_group_fusion_flow.py \
      tests/integration/test_g9_core_acceptance.py \
      tests/integration/test_g2_conflict_alignment_flow.py \
      tests/integration/test_g5_expert_diagnosis_flow.py \
      tests/integration/test_g5_vector_recommendation_flow.py \
      tests/unit/bootstrap/test_route_mount_contract.py \
      tests/unit/bootstrap/test_trust_gateway_auth.py \
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
      tests/integration/test_profile_activation_index_acceptance.py \
      tests/integration/test_worker_runtime_vector_sync.py \
      tests/contract/test_composed_embedding_provider.py \
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
