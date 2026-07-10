# Backend Profile / Env Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `DeployProfile` the only Backend implementation selector while limiting runtime/data Env to `dev` / `pre` / `prod`, with singlebox running as `DEPLOY_PROFILE=singlebox SERVER_ENV=dev`.

**Architecture:** The composition root selects the non-corp YAML overlay and DI module column from `DeployProfile`. Runtime Env continues to select fields and data partitions only. Singlebox-specific infrastructure is installed explicitly, while the existing `WORKSPACE_ENV_FOLDER=aidesktop_singlebox` field preserves Backend/BAAS physical path isolation without turning `singlebox` back into a data Env.

**Tech Stack:** Python 3.12, Injector, FastAPI, Pydantic, pytest, Bash, SQLite, uv

## Global Constraints

- `DeployProfile` is the only implementation/module selector.
- `get_current_env()` returns only `dev`, `pre`, or `prod`; `get_current_env_with_gray()` may additionally return `gray`.
- The singlebox Backend launch contract is exactly `DEPLOY_PROFILE=singlebox`, `SERVER_ENV=dev`, and `WORKSPACE_ENV_FOLDER=aidesktop_singlebox`.
- `application-singlebox.yaml` is selected by Profile, never by `SERVER_ENV=singlebox`.
- Legacy `SERVER_ENV=singlebox` must fail at every Backend startup entrypoint with an actionable migration message.
- Do not introduce `RuntimeEnvironmentModule`, `DataEnvironment`, or `DeviceRuntimeEnvironment`.
- Do not add singlebox branches to business or Core code.
- Do not redesign BAAS, Engine, or BCS environment models in this change; verify compatibility only.
- Do not alter corp `dev` / `pre` / `prod` behavior or persisted production data semantics.
- Implement from a clean worktree created from the documentation commit. Do not carry over the discarded, uncommitted runtime-environment prototype from the design worktree.

---

## File Map

| File | Responsibility after this change |
| --- | --- |
| `src/backend/src/agentclaw/community/core/config/yaml_provider.py` | Load a caller-selected YAML overlay; never inspect Profile or Env |
| `src/backend/src/agentclaw/community/di/config_bootstrap.py` | Map non-corp Profile to YAML overlay and register the provider |
| `src/backend/src/agentclaw/community/di/modules/singlebox_access_module.py` | Bind `PolicyServiceProtocol` to `LocalPolicyService` for singlebox only |
| `src/backend/src/agentclaw/community/di/modules/infrastructure/test/http_client.py` | Bind fixed no-network `LocalHttpClient` instances for test profiles only |
| `src/backend/src/agentclaw/community/di/profile_modules.py` | Install explicit test versus singlebox binding differences |
| `src/backend/src/agentclaw/community/di/profile.py` | Detect Profile and reject the retired Env value `singlebox` |
| `src/backend/src/agentclaw/community/utils/env_utils.py` | Normalize runtime/data Env without any singlebox case |
| `src/backend/src/agentclaw/community/core/devices/models.py` | Keep Device `Env` strict to `dev` / `pre` / `prod` |
| `src/backend/src/agentclaw/community/core/workspace/path_factory.py` | Use `WORKSPACE_ENV_FOLDER` for physical folder isolation, Env only as fallback |
| `scripts/modules/backend.sh` | Launch singlebox Backend with Profile and Env on separate axes |
| `src/backend/tests/community/architecture/test_no_singlebox_env_axis.py` | Prevent singlebox from re-entering Env comparisons or aliases |

## Execution Preflight

- [ ] **Step 1: Create a clean implementation worktree**

Use `superpowers:using-git-worktrees` from the repository containing commit `f59fdec` and the plan commit. Create a fresh branch such as `codex/profile-env-separation-impl`. Confirm that none of these discarded prototype paths are modified or untracked:

```text
src/backend/src/agentclaw/community/di/runtime_environment.py
src/backend/src/agentclaw/community/kernel/runtime_environment.py
src/backend/tests/community/di/test_runtime_environment.py
src/backend/tests/community/core/devices/services/test_device_data_environment.py
```

- [ ] **Step 2: Confirm the clean baseline**

Run:

```bash
git status --short
git log -2 --oneline
```

Expected: no source changes; the latest commits contain the approved Spec and this implementation plan.

---

### Task 1: Select non-corp YAML configuration by Profile

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/config/yaml_provider.py:27-120`
- Modify: `src/backend/src/agentclaw/community/di/config_bootstrap.py:1-36`
- Modify: `src/backend/tests/community/local/test_load_yaml_configs.py`
- Modify: `src/backend/tests/community/local/test_patch_sofapy.py:72-90`
- Modify: `src/backend/tests/community/di/test_config_bootstrap.py`

**Interfaces:**
- Consumes: `DeployProfile`
- Produces: `_yaml_overlay_for(profile: DeployProfile) -> str`
- Produces: `YamlConfigProvider(overlay_name: str = "application-dev.yaml")`
- Produces: `_load_yaml_configs(overlay_name: str = "application-dev.yaml") -> dict[str, Any]`

- [ ] **Step 1: Write failing Profile-to-overlay tests**

Replace the non-corp assertion in `test_config_bootstrap.py` with an explicit matrix:

```python
from agentclaw.community.core.config import provider as P
from agentclaw.community.core.config.yaml_provider import YamlConfigProvider
from agentclaw.community.di.config_bootstrap import (
    _yaml_overlay_for,
    register_config_provider,
)
from agentclaw.community.di.profile import DeployProfile


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (DeployProfile.COMMUNITY, "application-community.yaml"),
        (DeployProfile.TEST, "application-test.yaml"),
        (DeployProfile.CORP_TEST, "application-test.yaml"),
        (DeployProfile.SINGLEBOX, "application-singlebox.yaml"),
    ],
)
def test_non_corp_profile_registers_explicit_yaml_provider(profile, expected):
    assert _yaml_overlay_for(profile) == expected

    register_config_provider(profile)

    assert isinstance(P._provider, YamlConfigProvider)
    assert P._provider.overlay_name == expected
```

Update the singlebox loader tests to pass the overlay explicitly:

```python
def test_singlebox_loads_singlebox_yaml():
    cfg = _load_yaml_configs("application-singlebox.yaml")
    assert cfg["user_config"]["app"]["title"] == "AgentClaw Single Box"


def test_singlebox_has_no_external_baseurl():
    cfg = _load_yaml_configs("application-singlebox.yaml")
    user_config = cfg["user_config"]
    assert user_config["buservice"]["base_url"] == "http://127.0.0.1:9999"
    assert user_config["arca_sandbox"]["base_url"] == "http://127.0.0.1:9999"
    assert user_config["skill_center"]["base_url"] == "http://127.0.0.1:9999"
    assert user_config["baas"]["api_base_url"] == "http://localhost:8890"
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
cd src/backend
DEPLOY_PROFILE=test uv run pytest \
  tests/community/di/test_config_bootstrap.py \
  tests/community/local/test_load_yaml_configs.py -q
```

Expected: collection fails because `_yaml_overlay_for` does not exist, and `YamlConfigProvider` does not expose `overlay_name`.

- [ ] **Step 3: Make `YamlConfigProvider` consume an explicit overlay**

Remove `_select_overlay_name()` and change the loader/provider to:

```python
def _load_yaml_configs(
    overlay_name: str = "application-dev.yaml",
) -> dict[str, Any]:
    """Merge application.yaml with the caller-selected overlay."""
    config_dirs = [
        Path.cwd() / "configs",
        Path(__file__).resolve().parents[2] / "configs",
    ]
    base_config: dict[str, Any] = {}
    overlay_config: dict[str, Any] = {}

    for config_dir in config_dirs:
        base_path = config_dir / "application.yaml"
        overlay_path = config_dir / overlay_name
        if not base_path.exists():
            continue
        with open(base_path, "r", encoding="utf-8") as file:
            base_config = yaml.safe_load(file) or {}
        if overlay_path.exists():
            with open(overlay_path, "r", encoding="utf-8") as file:
                overlay_config = yaml.safe_load(file) or {}
            logger.info("YamlConfigProvider loaded overlay: %s", overlay_path)
        else:
            logger.warning(
                "YamlConfigProvider overlay %s not found, using base only",
                overlay_name,
            )
        break

    return _deep_merge(base_config, overlay_config)


class YamlConfigProvider:
    """Load AppConfig from the neutral base plus one explicit overlay."""

    def __init__(self, overlay_name: str = "application-dev.yaml") -> None:
        self.overlay_name = overlay_name

    def load(self) -> AppConfig:
        raw = _load_yaml_configs(self.overlay_name)
        return AppConfig(
            user_config=raw.get("user_config", {}),
            raw=raw,
            app_name=raw["app_name"],
            delegate=None,
        )
```

Remove the now-unused `os` import from `yaml_provider.py`.

- [ ] **Step 4: Register a Profile-configured provider at bootstrap**

Add this mapping and registration path to `config_bootstrap.py`:

```python
_YAML_OVERLAY_BY_PROFILE = {
    DeployProfile.COMMUNITY: "application-community.yaml",
    DeployProfile.TEST: "application-test.yaml",
    DeployProfile.CORP_TEST: "application-test.yaml",
    DeployProfile.SINGLEBOX: "application-singlebox.yaml",
}


def _yaml_overlay_for(profile: DeployProfile) -> str:
    try:
        return _YAML_OVERLAY_BY_PROFILE[profile]
    except KeyError as exc:
        raise ValueError(f"Profile {profile.value!r} does not use YAML config") from exc


def register_config_provider(profile: DeployProfile) -> None:
    if profile is DeployProfile.CORP:
        from importlib import import_module

        import_module(
            "agentclaw.corp.di.corp_bootstrap"
        ).install_corp_config_provider()
        return

    from agentclaw.community.core.config.provider import set_config_provider
    from agentclaw.community.core.config.yaml_provider import YamlConfigProvider

    set_config_provider(YamlConfigProvider(_yaml_overlay_for(profile)))
```

- [ ] **Step 5: Update direct loader tests to state their overlay**

In `test_load_yaml_configs.py`, replace environment manipulation for overlay selection with explicit calls to `_load_yaml_configs("application-*.yaml")`. Keep community leak checks unchanged except for passing `application-community.yaml`.

In `test_patch_sofapy.py`, use:

```python
config = _load_yaml_configs("application-test.yaml")
```

Delete all imports and assertions for `_select_overlay_name`.

- [ ] **Step 6: Run focused configuration tests**

Run:

```bash
cd src/backend
DEPLOY_PROFILE=test uv run pytest \
  tests/community/di/test_config_bootstrap.py \
  tests/community/local/test_load_yaml_configs.py \
  tests/community/local/test_patch_sofapy.py \
  tests/community/config/test_effective_config_snapshot.py -q
```

Expected: all tests pass; singlebox config is loaded while `SERVER_ENV` is not consulted.

- [ ] **Step 7: Commit the configuration slice**

```bash
git add \
  src/backend/src/agentclaw/community/core/config/yaml_provider.py \
  src/backend/src/agentclaw/community/di/config_bootstrap.py \
  src/backend/tests/community/local/test_load_yaml_configs.py \
  src/backend/tests/community/local/test_patch_sofapy.py \
  src/backend/tests/community/di/test_config_bootstrap.py
git commit -m "refactor(config): select yaml overlays by profile"
```

---

### Task 2: Split test and singlebox DI bindings

**Files:**
- Rename: `src/backend/src/agentclaw/community/di/modules/testing_access_module.py` -> `src/backend/src/agentclaw/community/di/modules/singlebox_access_module.py`
- Modify: `src/backend/src/agentclaw/community/di/modules/infrastructure/test/http_client.py`
- Modify: `src/backend/src/agentclaw/community/di/modules/http_client_module.py:1-12`
- Modify: `src/backend/src/agentclaw/community/di/container.py:80-125`
- Modify: `src/backend/src/agentclaw/community/di/profile_modules.py:19-190`
- Modify: `src/backend/src/agentclaw/community/di/profile.py:1-35`
- Rename: `src/backend/tests/community/di/modules/test_testing_access_module.py` -> `src/backend/tests/community/di/modules/test_singlebox_access_module.py`
- Rename: `src/backend/tests/community/di/modules/test_testing_infrastructure_module.py` -> `src/backend/tests/community/di/modules/test_test_http_client_module.py`
- Modify: `src/backend/tests/community/di/modules/test_infrastructure_module.py`
- Modify: `src/backend/tests/community/di/test_profile_and_modules_for.py:77-111`

**Interfaces:**
- Consumes: base `AccessModule` and base `HttpClientModule`
- Produces: `SingleboxAccessModule._policy_service_protocol() -> PolicyServiceProtocol`
- Produces: fixed no-network providers in `TestHttpClientModule`
- Produces: distinct `modules_for(TEST)` and `modules_for(SINGLEBOX)` sets

- [ ] **Step 1: Write the explicit Profile binding-matrix test**

Replace `test_modules_for_test_and_singlebox_match` with:

```python
def test_test_and_singlebox_have_explicit_access_and_http_bindings():
    test_names = _names(modules_for(DeployProfile.TEST))
    singlebox_names = _names(modules_for(DeployProfile.SINGLEBOX))

    assert "TestHttpClientModule" in test_names
    assert "SingleboxAccessModule" not in test_names
    assert "TestingAccessModule" not in test_names

    assert "SingleboxAccessModule" in singlebox_names
    assert "TestHttpClientModule" not in singlebox_names
    assert "TestingAccessModule" not in singlebox_names

    assert test_names - {"TestHttpClientModule"} == (
        singlebox_names - {"SingleboxAccessModule"}
    )
```

Add a fixed singlebox Access provider test:

```python
from agentclaw.community.di.modules.singlebox_access_module import (
    SingleboxAccessModule,
)
from agentclaw.community.plugins.local.policy_service import LocalPolicyService


@pytest.mark.parametrize("server_env", [None, "dev", "pre", "prod"])
def test_singlebox_access_always_returns_local_policy(monkeypatch, server_env):
    if server_env is None:
        monkeypatch.delenv("SERVER_ENV", raising=False)
    else:
        monkeypatch.setenv("SERVER_ENV", server_env)

    result = SingleboxAccessModule()._policy_service_protocol()

    assert isinstance(result, LocalPolicyService)
```

- [ ] **Step 2: Rewrite Test HTTP client tests as fixed test doubles**

The renamed `test_test_http_client_module.py` should assert these four calls without setting `SERVER_ENV`:

```python
from agentclaw.community.di.modules.infrastructure.test.http_client import (
    TestHttpClientModule,
)
from agentclaw.community.plugins.local.http_client import LocalHttpClient


def test_test_http_clients_are_local_and_scoped():
    module = TestHttpClientModule()

    baas = module.baas_http_client()
    bcn = module.bcn_http_client()
    general = module.general_http_client()
    masa = module.masa_agent_eval_http_client()

    assert isinstance(baas, LocalHttpClient)
    assert isinstance(bcn, LocalHttpClient)
    assert isinstance(general, LocalHttpClient)
    assert isinstance(masa, LocalHttpClient)
    assert baas._base_url == "http://localhost:8890"
    assert bcn._base_url == "http://localhost:8891"
    assert general._base_url == ""
    assert masa._base_url == "http://localhost:8080"
    assert len({id(baas), id(bcn), id(general), id(masa)}) == 4
```

- [ ] **Step 3: Run the DI tests and confirm failure**

Run:

```bash
cd src/backend
DEPLOY_PROFILE=test uv run pytest \
  tests/community/di/test_profile_and_modules_for.py \
  tests/community/di/modules/test_singlebox_access_module.py \
  tests/community/di/modules/test_test_http_client_module.py -q
```

Expected: collection fails because `SingleboxAccessModule` is not defined and the existing Profile sets still match.

- [ ] **Step 4: Replace `TestingAccessModule` with a fixed singlebox module**

Rename the file and replace its body with:

```python
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.plugins.local.policy_service import LocalPolicyService

logger = get_logger()


class SingleboxAccessModule(Module):
    """Singlebox-only all-open PolicyService binding."""

    @singleton
    @provider
    def _policy_service_protocol(self) -> PolicyServiceProtocol:
        logger.info(
            "[NEW-ARCH] PolicyServiceProtocol: LocalPolicyService (singlebox)"
        )
        return LocalPolicyService()
```

The module must not import `os`, `PolicyService`, or any Env utility.

- [ ] **Step 5: Make `TestHttpClientModule` unconditional**

Replace its four providers with:

```python
class TestHttpClientModule(Module):
    """No-network HTTP clients for test and corp_test profiles."""

    @singleton
    @provider
    def baas_http_client(self) -> Annotated[HttpClient, QUALIFIER_BAAS]:
        return LocalHttpClient(base_url="http://localhost:8890")

    @singleton
    @provider
    def bcn_http_client(self) -> Annotated[HttpClient, QUALIFIER_BCN]:
        return LocalHttpClient(base_url="http://localhost:8891")

    @singleton
    @provider
    def general_http_client(self) -> Annotated[HttpClient, QUALIFIER_GENERAL]:
        return LocalHttpClient(base_url="")

    @singleton
    @provider
    def masa_agent_eval_http_client(
        self,
    ) -> Annotated[HttpClient, QUALIFIER_MASA_AGENT_EVAL]:
        return LocalHttpClient(base_url="http://localhost:8080")
```

Import `LocalHttpClient` once at module scope. Remove `os`, config dataclasses, `inject`, `HttpxClient`, and `get_current_env` from this test-only module.

- [ ] **Step 6: Install Profile-specific overrides in `modules_for`**

Remove Access and HTTP imports/items from `_common_test_doubles()`. Build the shared local column first, then append exactly one Profile-specific module:

```python
column = _common_test_doubles() + [
    TestTokenVaultModule(),
    CommunityAICodingModule(),
    CommunityGovernanceModule(),
    CommunityOutboundRulesModule(),
    CommunityDeviceSyncModule(),
    TestAppServicesModule(),
    TestDevicesModule(),
]

if profile is DeployProfile.TEST:
    from agentclaw.community.di.modules.infrastructure.test.http_client import (
        TestHttpClientModule,
    )

    column.append(TestHttpClientModule())
else:
    from agentclaw.community.di.modules.singlebox_access_module import (
        SingleboxAccessModule,
    )

    column.append(SingleboxAccessModule())

return column
```

In the `CORP_TEST` branch, import and append `TestHttpClientModule`; do not append `SingleboxAccessModule`.

Update `profile.py` and module comments to say TEST uses mock HTTP plus real policy, while SINGLEBOX uses real HTTP plus local policy.

Update `http_client_module.py` and `container.py` comments to state that only test and corp_test override the base real HTTP clients; singlebox deliberately consumes the base `HttpClientModule`.

- [ ] **Step 7: Run focused DI and contract tests**

Run:

```bash
cd src/backend
DEPLOY_PROFILE=test uv run pytest \
  tests/community/di/test_profile_and_modules_for.py \
  tests/community/di/modules/test_singlebox_access_module.py \
  tests/community/di/modules/test_test_http_client_module.py \
  tests/community/di/modules/test_infrastructure_module.py \
  tests/community/contracts/test_http_client.py \
  tests/community/plugins/local/test_policy_service.py \
  tests/community/e2e/test_access_flows.py -q
```

Expected: all tests pass; test Profile gets real policy plus local HTTP, and singlebox gets local policy plus the base real HTTP module.

- [ ] **Step 8: Prove no old module name remains**

Run:

```bash
rg -n "TestingAccessModule|testing_access_module|SERVER_ENV.*singlebox" \
  src/backend/src/agentclaw/community/di \
  src/backend/tests/community/di
```

Expected: no matches.

- [ ] **Step 9: Commit the DI slice**

```bash
git add \
  src/backend/src/agentclaw/community/di/modules \
  src/backend/src/agentclaw/community/di/profile.py \
  src/backend/src/agentclaw/community/di/profile_modules.py \
  src/backend/tests/community/di
git commit -m "refactor(di): split test and singlebox bindings"
```

---

### Task 3: Remove singlebox from the Env axis without breaking workspace isolation

**Files:**
- Create: `src/backend/tests/community/architecture/test_no_singlebox_env_axis.py`
- Modify: `src/backend/src/agentclaw/community/di/profile.py`
- Modify: `src/backend/src/agentclaw/community/di/__init__.py`
- Modify: `src/backend/src/agentclaw/community/main.py:17-61`
- Modify: `src/backend/src/agentclaw/community/adapters/http/app.py:34-56`
- Modify: `src/backend/src/agentclaw/community/utils/env_utils.py`
- Modify: `src/backend/src/agentclaw/community/core/devices/models.py:32-65`
- Modify: `src/backend/src/agentclaw/community/core/workspace/path_factory.py:48-150`
- Modify: `scripts/modules/backend.sh:83-108`
- Modify: `scripts/test_singlebox_service_guards.sh`
- Modify: `src/backend/tests/community/di/test_profile_and_modules_for.py`
- Modify: `src/backend/tests/community/architecture/test_community_only_boot.py`
- Modify: `src/backend/tests/community/utils/test_env_utils.py`
- Modify: `src/backend/tests/community/core/devices/test_env_enum.py`
- Modify: `src/backend/tests/community/core/workspace/test_path_factory.py`
- Modify: `src/backend/tests/community/acceptance/bot_management/test_bot_live_lifecycle.py`
- Verify unchanged: `src/backend/src/agentclaw/community/adapters/http/devices/schemas.py`

**Interfaces:**
- Produces: `validate_deploy_environment(source: Mapping[str, str] | None = None) -> None`
- Produces: `_get_aidesktop_env_folder() -> str`
- Preserves: `get_current_env() -> Literal["dev", "pre", "prod"]` behavior by convention
- Preserves: `get_current_env_with_gray()` with the additional `gray` result

- [ ] **Step 1: Add the architecture guard before removing violations**

Create `test_no_singlebox_env_axis.py` with an AST scan that:

```python
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_THIS_FILE = Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]
_SOURCE_ROOT = _BACKEND_ROOT / "src" / "agentclaw" / "community"
_COMPARE_ALLOWLIST = {
    "di/profile.py": "Rejects the retired legacy SERVER_ENV value at startup.",
}


def _has_singlebox_literal(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Constant)
        and isinstance(child.value, str)
        and child.value.lower() == "singlebox"
        for child in ast.walk(node)
    )


def _call_name(node: ast.Call) -> str | None:
    return getattr(node.func, "id", None) or getattr(node.func, "attr", None)


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rel = path.relative_to(_SOURCE_ROOT).as_posix()
    failures: list[str] = []

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Compare)
            and _has_singlebox_literal(node)
            and rel not in _COMPARE_ALLOWLIST
        ):
            failures.append(f"{rel}:{node.lineno} singlebox comparison")
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name == "is_singlebox":
                failures.append(f"{rel}:{node.lineno} is_singlebox call")
            if name == "from_string" and node.args and _has_singlebox_literal(node.args[0]):
                failures.append(f"{rel}:{node.lineno} singlebox Env alias")
        elif isinstance(node, ast.FunctionDef) and node.name == "is_singlebox":
            failures.append(f"{rel}:{node.lineno} is_singlebox definition")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "is_singlebox":
                    failures.append(f"{rel}:{node.lineno} is_singlebox import")

    return failures


def test_singlebox_never_reenters_the_env_axis():
    failures: list[str] = []
    for path in _SOURCE_ROOT.rglob("*.py"):
        failures.extend(_violations(path))

    if failures:
        pytest.fail(
            "singlebox is a DeployProfile, not a runtime/data Env:\n  "
            + "\n  ".join(failures)
        )


def test_comparison_allowlist_paths_exist():
    missing = [
        rel for rel in _COMPARE_ALLOWLIST if not (_SOURCE_ROOT / rel).is_file()
    ]
    assert not missing, f"stale singlebox comparison allowlist: {missing}"
```

- [ ] **Step 2: Add failing Env, startup, and path contract tests**

In `test_env_enum.py`, require strict persisted values:

```python
def test_singlebox_is_not_a_data_environment():
    with pytest.raises(ValueError):
        Env.from_string("singlebox")
```

Delete all `is_singlebox()` tests from `test_env_utils.py`. Keep the existing dev/pre/prod/gray normalization matrix and add:

```python
def test_singlebox_profile_uses_dev_data_env(clean_env, monkeypatch):
    monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
    monkeypatch.setenv("SERVER_ENV", "dev")
    assert utils_env.is_local_mode() is True
    assert utils_env.is_dev() is True
    assert utils_env.get_current_env() == "dev"
    assert utils_env.get_current_env_with_gray() == "dev"
```

Add validation tests to `test_profile_and_modules_for.py`:

```python
@pytest.mark.parametrize(
    "key",
    ["SERVER_ENV", "REAL_SERVER_ENV", "ALIPAY_APP_ENV"],
)
def test_legacy_singlebox_env_is_rejected(key):
    with pytest.raises(RuntimeError, match="DEPLOY_PROFILE=singlebox SERVER_ENV=dev"):
        validate_deploy_environment({key: "singlebox"})


def test_dev_env_is_accepted():
    validate_deploy_environment({"SERVER_ENV": "dev"})
```

Add path tests:

```python
def test_singlebox_workspace_folder_is_profile_field(monkeypatch, tmp_path):
    monkeypatch.setenv("AIDESKTOP_ROOT", str(tmp_path))
    monkeypatch.setenv("SERVER_ENV", "dev")
    monkeypatch.setenv("WORKSPACE_ENV_FOLDER", "aidesktop_singlebox")

    assert get_bolt_base_dir() == tmp_path / "aidesktop_singlebox" / "bolt_data"


def test_workspace_folder_defaults_to_data_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AIDESKTOP_ROOT", str(tmp_path))
    monkeypatch.setenv("SERVER_ENV", "dev")
    monkeypatch.delenv("WORKSPACE_ENV_FOLDER", raising=False)

    assert get_bolt_base_dir() == tmp_path / "aidesktop_dev" / "bolt_data"
```

Add this shell contract to `test_singlebox_service_guards.sh` and invoke it at the bottom:

```bash
test_backend_separates_profile_env_and_workspace_folder() {
  local start_body
  start_body="$(sed -n '/^backend_start()/,/^backend_wait_until_ready()/p' "${ROOT}/scripts/modules/backend.sh")"

  grep -F 'SERVER_ENV=dev DEPLOY_PROFILE=singlebox' <<<"$start_body" >/dev/null || \
    fail "singlebox backend should launch with SERVER_ENV=dev and DEPLOY_PROFILE=singlebox"
  grep -F 'WORKSPACE_ENV_FOLDER=aidesktop_singlebox' <<<"$start_body" >/dev/null || \
    fail "singlebox backend should preserve the isolated aidesktop_singlebox folder"
  if grep -F 'SERVER_ENV=singlebox' <<<"$start_body" >/dev/null; then
    fail "backend startup must not use singlebox as a data Env"
  fi
}
```

In `test_community_only_boot.py`, change the subprocess environment default to:

```python
env.setdefault("SERVER_ENV", "dev")
```

- [ ] **Step 3: Run the focused tests and confirm the old model fails**

Run:

```bash
cd src/backend
DEPLOY_PROFILE=test uv run pytest \
  tests/community/architecture/test_no_singlebox_env_axis.py \
  tests/community/architecture/test_community_only_boot.py \
  tests/community/di/test_profile_and_modules_for.py \
  tests/community/utils/test_env_utils.py \
  tests/community/core/devices/test_env_enum.py \
  tests/community/core/workspace/test_path_factory.py -q
cd ../..
bash scripts/test_singlebox_service_guards.sh
```

Expected: failures identify the old Device alias, `is_singlebox` Env helper, missing validator, missing workspace-folder override, and `SERVER_ENV=singlebox` in `backend.sh`.

- [ ] **Step 4: Add the startup contract validator**

Add to `di/profile.py`:

```python
from collections.abc import Mapping

_SERVER_ENV_KEYS = ("SERVER_ENV", "REAL_SERVER_ENV", "ALIPAY_APP_ENV")


def validate_deploy_environment(
    source: Mapping[str, str] | None = None,
) -> None:
    """Reject the retired singlebox value on the runtime/data Env axis."""
    values = os.environ if source is None else source
    raw = next(
        (values[key] for key in _SERVER_ENV_KEYS if values.get(key)),
        "",
    )
    if raw.strip().lower() == "singlebox":
        raise RuntimeError(
            "SERVER_ENV='singlebox' is no longer supported; "
            "launch singlebox with DEPLOY_PROFILE=singlebox SERVER_ENV=dev"
        )
```

Re-export it from `di/__init__.py`. Call it immediately after `DeployProfile.detect()` in both `main.py` and `adapters/http/app.py`, before any config registration or DI construction:

```python
_deploy_profile = DeployProfile.detect()
validate_deploy_environment()
register_config_provider(_deploy_profile)
```

- [ ] **Step 5: Remove singlebox from Env helpers and Device Env**

Delete `is_singlebox()` from `env_utils.py`. Replace the normalized functions with:

```python
def is_dev() -> bool:
    env = _resolve_env()
    return not env or env in ["stable", "dev"]


def get_current_env() -> str:
    env = _resolve_env()
    if env in ["prod", "gray"]:
        return "prod"
    if env in ["pre", "prepub"]:
        return "pre"
    return "dev"


def get_current_env_with_gray() -> str:
    env = _resolve_env()
    if env == "prod":
        return "prod"
    if env == "gray":
        return "gray"
    if env in ["pre", "prepub"]:
        return "pre"
    return "dev"
```

Replace `Env.from_string()` in Device Core with:

```python
@classmethod
def from_string(cls, value: str) -> "Env":
    try:
        return cls(value.lower())
    except ValueError:
        raise ValueError(
            f"Invalid env value: {value!r}. Expected one of: dev, pre, prod"
        ) from None
```

Do not create a shared runtime-environment enum or inject Env into Device services/repositories.

- [ ] **Step 6: Preserve physical singlebox workspace isolation explicitly**

In `path_factory.py`, replace `_get_aidesktop_env()` with:

```python
def _get_aidesktop_env_folder() -> str:
    """Return the physical workspace folder selected by the active Profile."""
    explicit = os.getenv("WORKSPACE_ENV_FOLDER")
    if explicit:
        return explicit

    from agentclaw.community.utils.env_utils import get_current_env

    return f"aidesktop_{get_current_env()}"
```

Then make both path builders consume the folder directly:

```python
def get_bolt_base_dir() -> Path:
    return _get_aidesktop_root() / _get_aidesktop_env_folder() / "bolt_data"
```

In `get_bolt_shared_dir()`, make these two exact substitutions while retaining its existing `git_sync.bolt_shared_dir_name` lookup:

```python
aidesktop_env_folder = _get_aidesktop_env_folder()
```

```python
return aidesktop_root / aidesktop_env_folder / dir_name
```

- [ ] **Step 7: Change the Backend shell launch contract**

In the standalone branch of `backend_start()`, use:

```bash
SERVER_ENV=dev DEPLOY_PROFILE=singlebox \
    WORKSPACE_ENV_FOLDER=aidesktop_singlebox \
    DATABASE_URL="sqlite:///${RUNTIME_DATA_DIR}/backend.db" \
    ENABLE_OSS_SYNC=false \
    CHAT_ENGINE="${CHAT_ENGINE}" \
    AIDESKTOP_ROOT="${LOCAL_AIDESKTOP_DIR}" \
    LOCAL_AIDESKTOP_ROOT="${LOCAL_AIDESKTOP_DIR}" \
    PYTHONPATH="${community_src}:${BACKEND_DIR}:${PYTHONPATH:-}" \
    nohup "${backend_cmd[@]}" < /dev/null >> "${BACKEND_LOG}" 2>&1 &
```

In the retained non-standalone branch, replace `SERVER_ENV=singlebox` with `SERVER_ENV=dev`. Do not add `singlebox` to any Env variable.

- [ ] **Step 8: Update live acceptance seed data to `dev`**

In `test_bot_live_lifecycle.py`, change only SQL/data environment values. For the device-binding inserts, replace the embedded value with a bound parameter:

```sql
INSERT INTO ac_entity_device_binding (
  entity_id, entity_type, device_id, device_provider, env, device_props,
  status, apply_reason, applied_by, gmt_create, gmt_modified
) VALUES (
  :entity_id, 'staff', :device_id, :device_provider, :env, :device_props,
  'ACTIVE', 'singlebox provider branch seed', :owner_id,
  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
)
```

For bot inserts, use the same `:env` binding in the `env` column. Add this value to every affected params dictionary:

```python
"env": "dev",
```

Apply this to both device-binding inserts and bot inserts that currently persist `singlebox`. Do not change variables such as `SINGLEBOX_COVERAGE` or descriptive test names.

- [ ] **Step 9: Run all focused Profile/Env tests**

Run:

```bash
cd src/backend
DEPLOY_PROFILE=test uv run pytest \
  tests/community/architecture/test_no_singlebox_env_axis.py \
  tests/community/architecture/test_community_only_boot.py \
  tests/community/di/test_profile_and_modules_for.py \
  tests/community/utils/test_env_utils.py \
  tests/community/core/devices/test_env_enum.py \
  tests/community/core/workspace/test_path_factory.py \
  tests/community/e2e/test_devices_flows.py \
  tests/community/e2e/test_bot_management_flows.py -q
cd ../..
bash scripts/test_singlebox_service_guards.sh
```

Expected: all tests and the shell guard pass.

- [ ] **Step 10: Verify the Device API Schema was not given an alias**

Run:

```bash
rg -n "singlebox" \
  src/backend/src/agentclaw/community/adapters/http/devices/schemas.py \
  src/backend/src/agentclaw/community/core/devices/models.py
```

Expected: no matches. Both API and Core Device Env accept only `dev`, `pre`, and `prod`.

- [ ] **Step 11: Commit the Env-axis migration**

```bash
git add \
  scripts/modules/backend.sh \
  scripts/test_singlebox_service_guards.sh \
  src/backend/src/agentclaw/community/di \
  src/backend/src/agentclaw/community/main.py \
  src/backend/src/agentclaw/community/adapters/http/app.py \
  src/backend/src/agentclaw/community/utils/env_utils.py \
  src/backend/src/agentclaw/community/core/devices/models.py \
  src/backend/src/agentclaw/community/core/workspace/path_factory.py \
  src/backend/tests/community/architecture/test_no_singlebox_env_axis.py \
  src/backend/tests/community/architecture/test_community_only_boot.py \
  src/backend/tests/community/di/test_profile_and_modules_for.py \
  src/backend/tests/community/utils/test_env_utils.py \
  src/backend/tests/community/core/devices/test_env_enum.py \
  src/backend/tests/community/core/workspace/test_path_factory.py \
  src/backend/tests/community/acceptance/bot_management/test_bot_live_lifecycle.py
git commit -m "refactor(runtime): separate singlebox profile from data env"
```

---

### Task 4: Run the full Backend and real singlebox verification gate

**Files:**
- Verify only; do not create generated reports in tracked source paths

**Interfaces:**
- Consumes: all three implementation commits
- Produces: evidence that unit, E2E, live process, workspace, data Env, and cross-system contracts hold together

- [ ] **Step 1: Run the complete community Backend suite**

Run:

```bash
cd src/backend
DEPLOY_PROFILE=test uv run pytest tests/community -q
```

Expected: exit code 0 with no failures or collection errors. Existing intentionally skipped acceptance tests remain governed by their explicit `RUN_ACCEPTANCE` gate.

- [ ] **Step 2: Run repository shell guards**

Run from the repository root:

```bash
bash scripts/test_singlebox_default_mode.sh
bash scripts/test_singlebox_service_guards.sh
bash scripts/test_open_source_domain_guard.sh
bash scripts/test_singlebox_coverage_gate.sh
```

Expected: every script prints `PASS` and exits 0.

- [ ] **Step 3: Start a real isolated singlebox stack**

Run:

```bash
SINGLEBOX_MODEL_CONFIG_MODE=mock ./scripts/singlebox.sh stop all || true
SINGLEBOX_MODEL_CONFIG_MODE=mock ./scripts/singlebox.sh setup all
SINGLEBOX_MODEL_CONFIG_MODE=mock ./scripts/singlebox.sh start all
./scripts/singlebox.sh status all
```

Expected: Backend, BAAS, BCS, bots, demo bot, and Frontend remain running after the start command returns.

- [ ] **Step 4: Probe live services directly**

Run:

```bash
curl --noproxy '*' --fail --silent http://127.0.0.1:8888/api/health
curl --noproxy '*' --fail --silent http://127.0.0.1:8890/health
curl --noproxy '*' --fail --silent http://127.0.0.1:21000/health
lsof -nP -iTCP:8888 -sTCP:LISTEN
lsof -nP -iTCP:8890 -sTCP:LISTEN
lsof -nP -iTCP:21000 -sTCP:LISTEN
```

Expected: all curls exit 0 and each port has a live listener owned by this worktree.

- [ ] **Step 5: Run live Backend acceptance slices**

Run:

```bash
cd src/backend
RUN_ACCEPTANCE=1 \
SINGLEBOX_ACCEPTANCE_REUSE_LIVE=1 \
DEPLOY_PROFILE=singlebox \
SERVER_ENV=dev \
uv run pytest \
  tests/community/acceptance/access \
  tests/community/acceptance/devices \
  tests/community/acceptance/bot_management -q
```

Expected: the selected live tests pass without policy rejection and without Env conversion errors.

- [ ] **Step 6: Verify data Env and physical workspace independently**

Run:

```bash
sqlite3 scripts/.dependencies/data/backend.db \
  "SELECT DISTINCT env FROM ac_entity_device_binding WHERE env IS NOT NULL ORDER BY env;"
find scripts/.dependencies -type d -name aidesktop_singlebox -print
```

Expected: the SQL result is empty or contains only `dev`; at least one `aidesktop_singlebox` directory exists after the live lifecycle. No persisted row contains `singlebox`.

- [ ] **Step 7: Run the real singlebox coverage entrypoint**

Run from the repository root:

```bash
scripts/ci/singlebox_coverage.sh
```

Expected: the command starts the real stack, runs Backend/BAAS E2E entrypoints, generates its coverage artifacts, and exits 0.

- [ ] **Step 8: Verify cross-system compatibility without refactoring those systems**

Run:

```bash
./scripts/singlebox.sh status baas
./scripts/singlebox.sh status bcs
rg -n "WORKSPACE_ENV_FOLDER.*aidesktop_singlebox" \
  scripts/modules/backend.sh \
  src/baas/packages/community/scripts/app.sh
bash src/baas/scripts/ci_test.sh
bash src/bcs/scripts/ci_test.sh --fast-fail
bash src/engine/scripts/ci_test.sh
```

Expected: BAAS and BCS report running, Backend/BAAS agree on `aidesktop_singlebox`, and the BAAS, BCS, and Engine module CI scripts all exit 0.

After Avernet validation, run the corp Profile wiring test in the OCB integration checkout that consumes this Avernet commit:

```bash
cd src/backend
DEPLOY_PROFILE=corp_test uv run pytest \
  tests/corp/di/test_profile_and_modules_for.py \
  tests/corp/di/test_config_bootstrap.py -q
```

Expected: corp_test still gets test HTTP clients, never `SingleboxAccessModule`, and corp config registration remains unchanged.

- [ ] **Step 9: Stop the live stack and inspect the final diff**

Run:

```bash
./scripts/singlebox.sh stop all
git status --short
git diff --check
git log --oneline --decorate -5
```

Expected: no runtime artifacts are tracked, `git diff --check` is clean, and the implementation consists of the three scoped commits from Tasks 1-3.

---

## Review Checkpoints

1. After Task 1, verify singlebox loads `application-singlebox.yaml` while `SERVER_ENV=dev` remains possible.
2. After Task 2, inspect the Profile module-name matrix before proceeding; this is the implementation-selection boundary.
3. After Task 3, inspect the architecture guard output and the Backend/BAAS workspace-folder contract.
4. Do not update an outer OCB gitlink until Task 4 passes in Avernet and the OCB corp compatibility tests pass.

## Spec Traceability

| Spec requirement | Implementation task |
| --- | --- |
| Profile-selected configuration | Task 1 |
| Explicit Access and HTTP binding matrix | Task 2 |
| Env normalization and strict Device Env | Task 3 |
| Legacy startup rejection | Task 3 |
| Backend/BAAS physical workspace alignment | Task 3 |
| Static anti-regression guard | Task 3 |
| Backend, real singlebox, and cross-system verification | Task 4 |
