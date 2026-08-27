#!/usr/bin/env python3
"""
Generate bcsfuse deployment configs from .env.local or a supplied env file.

Usage:
  # Generate example configs with masked secrets (default, reads .env.local)
  python docker/generate_deploy_config.py /path/to/output/dir [image_tag]

  # Generate real deployment configs from a filled env file
  python docker/generate_deploy_config.py --env /path/to/bcsfuse.env --no-mask \
      /path/to/output/dir [image_tag]

It creates:
  - bcsfuse.env
  - bcsfuse-deployment.yaml
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env.local"
EXAMPLE_ENV_FILE = PROJECT_ROOT / "docker" / "bcsfuse.env.example"

# Variables we care about, grouped by sensitivity.
SENSITIVE_KEYS = {
    "MYSQL_PASSWORD",
    "EMBEDDING_AUTH_TOKEN",
    "LLM_AUTH_TOKEN",
}

REQUIRED_KEYS = {
    "MYSQL_HOST",
    "MYSQL_PORT",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "MYSQL_DATABASE",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_AUTH_TOKEN",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSION",
    "EMBEDDING_TIMEOUT_MS",
    "ENABLE_REAL_LLM",
    "LLM_BASE_URL",
    "LLM_AUTH_TOKEN",
    "LLM_API_TYPE",
    "LLM_FAST_MODEL",
    "LLM_BALANCED_MODEL",
    "LLM_REASONING_MODEL",
    "LLM_LONG_CONTEXT_MODEL",
    "LLM_EXTRACTION_MODEL",
    "LLM_DEFAULT_TIMEOUT_MS",
    "LLM_REASONING_TIMEOUT_MS",
}


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Handle both `export KEY=value` and `KEY=value`
        line = re.sub(r"^export\s+", "", line)
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip().strip('"').strip("'")
        values[key] = val
    return values


def mask_secrets(values: dict[str, str]) -> dict[str, str]:
    masked = dict(values)
    for key in SENSITIVE_KEYS:
        if key in masked and masked[key] and not masked[key].startswith("REPLACE_WITH"):
            masked[key] = f"REPLACE_WITH_{key}"
    return masked


def write_env_file(out_dir: pathlib.Path, values: dict[str, str]) -> pathlib.Path:
    path = out_dir / "bcsfuse.env"
    lines = [
        "# BCSFuse runtime environment variables",
        "# Fill in the REPLACE_WITH_* placeholders, then load via K8s Secret/ConfigMap.",
        "",
        "# ---- MySQL ----",
        f'export MYSQL_HOST="{values["MYSQL_HOST"]}"',
        f'export MYSQL_PORT="{values["MYSQL_PORT"]}"',
        f'export MYSQL_USER="{values["MYSQL_USER"]}"',
        f'export MYSQL_PASSWORD="{values["MYSQL_PASSWORD"]}"',
        f'export MYSQL_DATABASE="{values["MYSQL_DATABASE"]}"',
        "",
        "# ---- Embedding ----",
        f'export EMBEDDING_BASE_URL="{values["EMBEDDING_BASE_URL"]}"',
        f'export EMBEDDING_AUTH_TOKEN="{values["EMBEDDING_AUTH_TOKEN"]}"',
        f'export EMBEDDING_MODEL="{values["EMBEDDING_MODEL"]}"',
        f'export EMBEDDING_DIMENSION="{values["EMBEDDING_DIMENSION"]}"',
        f'export EMBEDDING_TIMEOUT_MS="{values["EMBEDDING_TIMEOUT_MS"]}"',
        "",
        "# ---- LLM ----",
        f'export ENABLE_REAL_LLM="{values["ENABLE_REAL_LLM"]}"',
        f'export LLM_BASE_URL="{values["LLM_BASE_URL"]}"',
        f'export LLM_AUTH_TOKEN="{values["LLM_AUTH_TOKEN"]}"',
        f'export LLM_API_TYPE="{values["LLM_API_TYPE"]}"',
        f'export LLM_FAST_MODEL="{values["LLM_FAST_MODEL"]}"',
        f'export LLM_BALANCED_MODEL="{values["LLM_BALANCED_MODEL"]}"',
        f'export LLM_REASONING_MODEL="{values["LLM_REASONING_MODEL"]}"',
        f'export LLM_LONG_CONTEXT_MODEL="{values["LLM_LONG_CONTEXT_MODEL"]}"',
        f'export LLM_EXTRACTION_MODEL="{values["LLM_EXTRACTION_MODEL"]}"',
        f'export LLM_DEFAULT_TIMEOUT_MS="{values["LLM_DEFAULT_TIMEOUT_MS"]}"',
        f'export LLM_REASONING_TIMEOUT_MS="{values["LLM_REASONING_TIMEOUT_MS"]}"',
        "",
    ]
    path.write_text("\n".join(lines))
    return path


def write_deployment_yaml(out_dir: pathlib.Path, values: dict[str, str], image_tag: str) -> pathlib.Path:
    path = out_dir / "bcsfuse-deployment.yaml"
    yaml = f"""---
apiVersion: v1
kind: ConfigMap
metadata:
  name: bcsfuse-config
  namespace: default
data:
  BCSFUSE_PROVIDER_MODE: "runtime"
  BCSFUSE_PORT: "8765"
  BCSFUSE_SERVER_HOST: "0.0.0.0"
  MYSQL_HOST: "{values['MYSQL_HOST']}"
  MYSQL_PORT: "{values['MYSQL_PORT']}"
  MYSQL_DATABASE: "{values['MYSQL_DATABASE']}"
  EMBEDDING_BASE_URL: "{values['EMBEDDING_BASE_URL']}"
  EMBEDDING_MODEL: "{values['EMBEDDING_MODEL']}"
  EMBEDDING_DIMENSION: "{values['EMBEDDING_DIMENSION']}"
  EMBEDDING_TIMEOUT_MS: "{values['EMBEDDING_TIMEOUT_MS']}"
  ENABLE_REAL_LLM: "{values['ENABLE_REAL_LLM']}"
  LLM_BASE_URL: "{values['LLM_BASE_URL']}"
  LLM_API_TYPE: "{values['LLM_API_TYPE']}"
  LLM_FAST_MODEL: "{values['LLM_FAST_MODEL']}"
  LLM_BALANCED_MODEL: "{values['LLM_BALANCED_MODEL']}"
  LLM_REASONING_MODEL: "{values['LLM_REASONING_MODEL']}"
  LLM_LONG_CONTEXT_MODEL: "{values['LLM_LONG_CONTEXT_MODEL']}"
  LLM_EXTRACTION_MODEL: "{values['LLM_EXTRACTION_MODEL']}"
  LLM_DEFAULT_TIMEOUT_MS: "{values['LLM_DEFAULT_TIMEOUT_MS']}"
  LLM_REASONING_TIMEOUT_MS: "{values['LLM_REASONING_TIMEOUT_MS']}"
---
apiVersion: v1
kind: Secret
metadata:
  name: bcsfuse-secrets
  namespace: default
type: Opaque
stringData:
  MYSQL_USER: "{values['MYSQL_USER']}"
  MYSQL_PASSWORD: "{values['MYSQL_PASSWORD']}"
  EMBEDDING_AUTH_TOKEN: "{values['EMBEDDING_AUTH_TOKEN']}"
  LLM_AUTH_TOKEN: "{values['LLM_AUTH_TOKEN']}"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bcsfuse
  namespace: default
  labels:
    app: bcsfuse
spec:
  replicas: 1
  selector:
    matchLabels:
      app: bcsfuse
  template:
    metadata:
      labels:
        app: bcsfuse
    spec:
      containers:
        - name: bcsfuse
          image: {image_tag}
          imagePullPolicy: Always
          ports:
            - containerPort: 8765
              name: http
          envFrom:
            - configMapRef:
                name: bcsfuse-config
            - secretRef:
                name: bcsfuse-secrets
          resources:
            requests:
              memory: "512Mi"
              cpu: "500m"
            limits:
              memory: "2Gi"
              cpu: "2000m"
          livenessProbe:
            httpGet:
              path: /health
              port: 8765
            initialDelaySeconds: 30
            periodSeconds: 30
            timeoutSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health
              port: 8765
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
---
apiVersion: v1
kind: Service
metadata:
  name: bcsfuse
  namespace: default
spec:
  type: ClusterIP
  selector:
    app: bcsfuse
  ports:
    - port: 80
      targetPort: 8765
      name: http
"""
    path.write_text(yaml)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate bcsfuse deployment configs.")
    parser.add_argument("output_dir", help="Directory to write bcsfuse.env and bcsfuse-deployment.yaml")
    parser.add_argument("image_tag", nargs="?", default="avernet-registry.cn-beijing.cr.aliyuncs.com/avernet/bcsfuse:CHANGE_ME", help="Container image reference")
    parser.add_argument("--env", dest="env_file", help="Env file to read (default: .env.local)")
    parser.add_argument("--no-mask", action="store_true", help="Do not mask secrets in generated files")
    args = parser.parse_args()

    out_dir = pathlib.Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    env_file = pathlib.Path(args.env_file).expanduser().resolve() if args.env_file else DEFAULT_ENV_FILE

    if not env_file.exists():
        print(f"ERROR: env file not found: {env_file}", file=sys.stderr)
        return 1

    values = parse_env_file(env_file)
    missing = REQUIRED_KEYS - values.keys()
    if missing:
        print(f"ERROR: missing keys in {env_file}: {sorted(missing)}", file=sys.stderr)
        return 1

    if not args.no_mask:
        values = mask_secrets(values)

    env_path = write_env_file(out_dir, values)
    yaml_path = write_deployment_yaml(out_dir, values, args.image_tag)

    print(f"Generated:\n  {env_path}\n  {yaml_path}")
    if args.no_mask:
        print(f"\nNext step:\n  kubectl apply -f {yaml_path.name}")
    else:
        print(f"\nNext steps:")
        print(f"  1. Edit {env_path} and fill in REPLACE_WITH_* values.")
        print(f"  2. Update image tag in {yaml_path} if needed.")
        print(f"  3. Copy these files to your deploy host and run: kubectl apply -f {yaml_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
