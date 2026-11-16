#!/usr/bin/env bash
# Exercise the BCS preset HTTP routes against disposable MySQL in local Kubernetes.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/k8s-test-lib.sh"
kube=(kubectl --context "${BCS_PRESET_TEST_KUBE_CONTEXT:-colima}")
namespace="bcs-preset-e2e-$(uuidgen | tr '[:upper:]' '[:lower:]' | cut -c1-12)"
password="$(uuidgen)"
root_password="$(uuidgen)"
forward_log="$(mktemp)"
forward_pid=""
namespace_created=false

cleanup() {
  if [[ -n "$forward_pid" ]]; then
    kill "$forward_pid" 2>/dev/null || true
    wait "$forward_pid" 2>/dev/null || true
  fi
  rm -f "$forward_log"
  if [[ "$namespace_created" == true ]]; then
    "${kube[@]}" delete namespace "$namespace" --wait=false
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"${kube[@]}" create namespace "$namespace"
namespace_created=true
"${kube[@]}" -n "$namespace" apply -f - <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: mysql
spec:
  containers:
    - name: mysql
      image: mysql:8.4
      env:
        - name: MYSQL_ROOT_PASSWORD
          value: "$root_password"
        - name: MYSQL_DATABASE
          value: bcs_preset_e2e
        - name: MYSQL_USER
          value: bcs
        - name: MYSQL_PASSWORD
          value: "$password"
      readinessProbe:
        exec:
          command:
            - sh
            - -ec
            - 'MYSQL_PWD="\$MYSQL_PASSWORD" mysql -h 127.0.0.1 -u bcs bcs_preset_e2e -e "SELECT 1"'
        periodSeconds: 2
YAML

if ! "${kube[@]}" -n "$namespace" wait --for=condition=Ready pod/mysql --timeout=180s; then
  "${kube[@]}" -n "$namespace" get events --sort-by=.lastTimestamp
  exit 1
fi

"${kube[@]}" -n "$namespace" port-forward --address 127.0.0.1 pod/mysql :3306 >"$forward_log" 2>&1 &
forward_pid=$!
port=""
for _ in {1..50}; do
  port="$(sed -n 's/^Forwarding from 127\.0\.0\.1:\([0-9]*\) -> 3306$/\1/p' "$forward_log")"
  [[ -n "$port" ]] && break
  kill -0 "$forward_pid" 2>/dev/null || break
  sleep 0.2
done
if [[ -z "$port" ]]; then
  cat "$forward_log" >&2
  exit 1
fi

cd "$K8S_TEST_REPO_ROOT/apps/bcs"
BCS_PRESET_TEST_MYSQL_PORT="$port" BCS_PRESET_TEST_MYSQL_PASSWORD="$password" \
  cargo test -p bcs --test integration_group_context -- \
  --ignored --exact mysql_human_preset_management_routes
