#!/usr/bin/env python3
"""
Generate Kubernetes deployment configs for bcsfuse / evolvetrace.

Usage:
  # Generate bcsfuse configs from .env.local (default service)
  python docker/generate_deploy_config.py /path/to/output/dir [image_tag]

  # Generate evolvetrace configs from a filled env file
  python docker/generate_deploy_config.py --service evolvetrace \
      --env /path/to/evolvetrace.env --no-mask \
      /path/to/output/dir [image_tag]

It creates:
  - <service>.env
  - <service>-deployment.yaml
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Service-specific defaults for the env file source.
DEFAULT_ENV_FILES: dict[str, pathlib.Path | None] = {
    "bcsfuse": PROJECT_ROOT / ".env.local",
    "evolvetrace": None,  # must be supplied via --env
}

# Sensitive key detection.
SENSITIVE_KEY_RE = re.compile(
    r"(PASSWORD|AUTH_TOKEN|TOKEN|SECRET|KEY_B64|PRIVATE_KEY|API_KEY)$",
    re.IGNORECASE,
)


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^export\s+", "", line)
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip().strip('"').strip("'")
        values[key] = val
    return values


def load_merged_values(service: str, env_file: pathlib.Path | None) -> dict[str, str]:
    example_env = PROJECT_ROOT / "docker" / f"{service}.env.example"
    if not example_env.exists():
        raise FileNotFoundError(f"example env file not found: {example_env}")

    example_values = parse_env_file(example_env)

    default_env = DEFAULT_ENV_FILES.get(service)
    default_values = parse_env_file(default_env) if default_env and default_env.exists() else {}

    user_values: dict[str, str] = {}
    if env_file and env_file.exists():
        user_values = parse_env_file(env_file)

    # precedence: user env > default env (e.g. .env.local) > example env
    return {**example_values, **default_values, **user_values}


def mask_secrets(values: dict[str, str]) -> dict[str, str]:
    masked = dict(values)
    for key in masked:
        if SENSITIVE_KEY_RE.search(key) and masked[key] and not masked[key].startswith("REPLACE_WITH"):
            masked[key] = f"REPLACE_WITH_{key}"
    return masked


def write_env_file(out_dir: pathlib.Path, service: str, values: dict[str, str]) -> pathlib.Path:
    path = out_dir / f"{service}.env"
    lines = [
        f"# {service} runtime environment variables",
        f"# Generated from example + env file.",
        "",
    ]
    for key, val in values.items():
        lines.append(f'export {key}="{val}"')
    lines.append("")
    path.write_text("\n".join(lines))
    return path


def get_image_from_template(template: str) -> str:
    match = re.search(r'^\s*image:\s*(.+)$', template, re.MULTILINE)
    return match.group(1).strip() if match else ""


def apply_deployment_template(template_path: pathlib.Path, image_tag: str) -> str:
    content = template_path.read_text()

    existing_image = get_image_from_template(content)
    if existing_image and image_tag and image_tag != "CHANGE_ME":
        content = content.replace(existing_image, image_tag)

    return content


def write_deployment_yaml(
    out_dir: pathlib.Path,
    service: str,
    image_tag: str,
) -> pathlib.Path:
    path = out_dir / f"{service}-deployment.yaml"
    template_path = PROJECT_ROOT / "docker" / f"{service}-deployment.example.yaml"
    if not template_path.exists():
        raise FileNotFoundError(f"deployment template not found: {template_path}")

    yaml = apply_deployment_template(template_path, image_tag)
    path.write_text(yaml)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate service deployment configs.")
    parser.add_argument("--service", default="bcsfuse", help="Service name (bcsfuse or evolvetrace)")
    parser.add_argument("output_dir", help="Directory to write <service>.env and <service>-deployment.yaml")
    parser.add_argument(
        "image_tag",
        nargs="?",
        default="CHANGE_ME",
        help="Container image reference",
    )
    parser.add_argument("--env", dest="env_file", help="Env file to read (service-specific default if omitted)")
    parser.add_argument("--no-mask", action="store_true", help="Do not mask secrets in generated files")
    args = parser.parse_args()

    service = args.service
    if service not in DEFAULT_ENV_FILES:
        print(f"ERROR: unsupported service '{service}'", file=sys.stderr)
        return 1

    out_dir = pathlib.Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    env_file: pathlib.Path | None = None
    if args.env_file:
        env_file = pathlib.Path(args.env_file).expanduser().resolve()

    try:
        values = load_merged_values(service, env_file)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    example_env = PROJECT_ROOT / "docker" / f"{service}.env.example"
    required_keys = set(parse_env_file(example_env).keys())
    missing = required_keys - values.keys()
    if missing:
        print(f"ERROR: missing keys in env input: {sorted(missing)}", file=sys.stderr)
        return 1

    if not args.no_mask:
        values = mask_secrets(values)

    env_path = write_env_file(out_dir, service, values)
    yaml_path = write_deployment_yaml(out_dir, service, args.image_tag)

    print(f"Generated:\n  {env_path}\n  {yaml_path}")
    if args.no_mask:
        print(f"\nNext step:\n  kubectl apply -f {yaml_path.name}")
    else:
        print("\nNext steps:")
        print(f"  1. Edit {env_path} and fill in REPLACE_WITH_* values.")
        print(f"  2. Update image tag in {yaml_path} if needed.")
        print(f"  3. Copy these files to your deploy host and run: kubectl apply -f {yaml_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
