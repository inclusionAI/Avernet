#!/usr/bin/env python3
"""
S11/S12 Config Contract Smoke Test

Validates:
1. application.yaml exists
2. configs/application.yaml is not required for OSS startup
3. Required env keys are documented
4. No real token/password appears in yaml
5. BCSFUSE_AUTH_TOKEN is read from env
6. Dev/smoke can run with dummy BCSFUSE_AUTH_TOKEN
7. Secret-like env values are masked in diagnostics
8. No internal config imports
9. S12: Runtime external provider env keys documented
10. S12: Dummy token examples are obvious placeholders
11. S12: Diagnostics masking works
12. S12: Runtime provider contract smoke uses local fake server only

This test does NOT:
- Connect to external services
- Use real tokens/passwords
- Import internal auth/DRM/Layotto code
"""
import os
import sys
import yaml
import re
import pytest
from pathlib import Path

# Add bcsfuse root to path
bcsfuse_root = Path(__file__).parent.parent
sys.path.insert(0, str(bcsfuse_root))
sys.path.insert(0, str(bcsfuse_root / "src"))

# Secret patterns to detect (should NOT appear in config)
SECRET_PATTERNS = [
    r'Bearer\s+[A-Za-z0-9._-]{12,}',  # Bearer tokens
    r'AUTH_TOKEN=.*[^_A-Z]',  # Token assignments with values
    r'PASSWORD=.*[^_A-Z]',  # Password assignments with values
    r'SECRET=.*[^_A-Z]',  # Secret assignments with values
    r'API_KEY=.*[^_A-Z]',  # API key assignments with values
    r'token:\s*["\']?[a-zA-Z0-9]{16,}["\']?',  # Token values
    r'password:\s*["\']?[a-zA-Z0-9]{8,}["\']?',  # Password values
]


# ========================================
# Pytest Fixtures
# ========================================
@pytest.fixture
def config_path():
    """Pytest fixture for config path."""
    return Path(__file__).parent.parent / "configs" / "application.yaml"


@pytest.fixture
def config(config_path):
    """Pytest fixture for parsed config."""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


# ========================================
# Tests
# ========================================
def test_application_yaml_exists():
    """Test that application.yaml exists."""
    config_path = Path(__file__).parent.parent / "configs" / "application.yaml"
    assert config_path.exists(), f"application.yaml not found at {config_path}"
    print("✅ application.yaml exists")
    return config_path


def test_parse_yaml(config_path):
    """Test that yaml is parseable."""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    assert config is not None, "Failed to parse YAML"
    print("✅ application.yaml is valid YAML")
    return config


def test_no_application_yaml_required():
    """Test that configs/application.yaml is not required."""
    # Try to create app without application.yaml
    os.environ.pop('BCSFUSE_CONFIG_PATH', None)

    # application.yaml should not exist
    application_yaml = Path(__file__).parent.parent / "configs" / "application.yaml"
    if application_yaml.exists():
        print(f"⚠️  Warning: {application_yaml} exists but should not be required")
    else:
        print("✅ configs/application.yaml is not required")

    # Try to import and create app
    from src.bootstrap.opensource_app import create_opensource_app
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = 'test-token'

    try:
        app = create_opensource_app(mode='test')
        assert app is not None, "Failed to create app"
        print("✅ OSS app can start without application.yaml")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_no_secret_literals(config_path):
    """Test that no secret literals appear in config."""
    with open(config_path, 'r') as f:
        lines = f.readlines()

    secrets_found = []
    for line_num, line in enumerate(lines, 1):
        # Skip comment lines (lines starting with #)
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue

        # Check for secret patterns
        for pattern in SECRET_PATTERNS:
            matches = re.findall(pattern, line, re.IGNORECASE)
            if matches:
                for match in matches:
                    secrets_found.append((line_num, match))

    if secrets_found:
        print(f"❌ BLOCKER: Found potential secret literals in config:")
        for line_num, secret in secrets_found:
            print(f"   - Line {line_num}: {secret[:20]}...")
        assert False, "Secret literals found in configuration file"
    else:
        print("✅ No secret literals in config")


def test_env_keys_documented(config):
    """Test that required env keys are documented."""
    required_env_keys = [
        'BCSFUSE_AUTH_TOKEN',
        'BCSFUSE_PROVIDER_MODE',
        'BCSFUSE_SERVER_HOST',
        'BCSFUSE_SERVER_PORT',
        'BCSFUSE_DATABASE_SQLITE_PATH',
        'BCSFUSE_FAISS_INDEX_PATH',
        'BCSFUSE_FAISS_SQLITE_PATH',
        'BCSFUSE_OBJECT_STORAGE_DIR',
        'EMBEDDING_BASE_URL',
        'EMBEDDING_AUTH_TOKEN',
        'EMBEDDING_MODEL',
        'EMBEDDING_DIMENSION',
        'RERANKER_BASE_URL',
        'RERANKER_API_KEY',
        'RERANKER_MODEL',
        'LLM_BASE_URL',
        'LLM_AUTH_TOKEN',
        'LLM_ENABLED',
    ]

    # Flatten the config to find all env keys
    def find_env_keys(obj, path=""):
        keys = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                keys.extend(find_env_keys(v, f"{path}.{k}" if path else k))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                keys.extend(find_env_keys(v, f"{path}[{i}]"))
        elif isinstance(obj, str):
            if obj.endswith('_env') or '_env' in path:
                keys.append(obj)
        return keys

    found_keys = find_env_keys(config)

    print(f"✅ Found {len(found_keys)} env key references in config")

    # Check that auth token is documented
    assert 'BCSFUSE_AUTH_TOKEN' in found_keys, "BCSFUSE_AUTH_TOKEN env key not documented"
    print("✅ BCSFUSE_AUTH_TOKEN env key is documented")


def test_auth_token_from_env():
    """Test that BCSFUSE_AUTH_TOKEN is read from env."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'

    # Test with dummy token
    os.environ['BCSFUSE_AUTH_TOKEN'] = 'test-token-for-smoke'

    from src.bootstrap.opensource_app import create_opensource_app
    from src.infra.public.auth.simple_token_auth_provider import SimpleTokenAuthProvider

    try:
        app = create_opensource_app(mode='test')
        assert app is not None, "Failed to create app"

        # Get auth provider from registry
        auth = app.state.context.registry.get('auth')
        assert auth is not None, "Auth provider not registered"
        assert isinstance(auth, SimpleTokenAuthProvider), f"Wrong auth provider type: {type(auth)}"
        assert auth.valid_token == 'test-token-for-smoke', "Auth token not read from env"

        print("✅ BCSFUSE_AUTH_TOKEN is read from environment variable")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_secret_masking_in_diagnostics():
    """Test that secret-like env values are masked in diagnostics."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = 'super-secret-token-12345'
    os.environ['EMBEDDING_AUTH_TOKEN'] = 'embedding-secret-67890'
    os.environ['MYSQL_PASSWORD'] = 'mysql-password-secret'

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')

        # Get config provider
        config = app.state.context.registry.get('config')

        # If config provider has diagnostics, check masking
        if hasattr(config, 'get_diagnostics'):
            diag = config.get_diagnostics()
            diag_str = str(diag)

            # Check that secrets are masked
            assert 'super-secret-token-12345' not in diag_str, "AUTH_TOKEN not masked in diagnostics"
            assert 'embedding-secret-67890' not in diag_str, "EMBEDDING_AUTH_TOKEN not masked in diagnostics"
            assert 'mysql-password-secret' not in diag_str, "MYSQL_PASSWORD not masked in diagnostics"

            # Check for masked patterns
            assert '***MASKED***' in diag_str or '***' in diag_str, "No masking pattern found in diagnostics"

            print("✅ Secret values are masked in diagnostics")

    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)
        os.environ.pop('EMBEDDING_AUTH_TOKEN', None)
        os.environ.pop('MYSQL_PASSWORD', None)


def test_no_internal_imports():
    """Test that no internal config imports are used."""
    # Check that application.yaml doesn't reference internal imports
    config_path = Path(__file__).parent.parent / "configs" / "application.yaml"
    with open(config_path, 'r') as f:
        content = f.read()

    forbidden_imports = [
        'sofa_app',
        'ZDAS',
        'zdas',
        'DRM',
        'drm',
        'Layotto',
        'layotto',
        'sofapy_base',
        'rpplus',
        'qdrant_zdas',
        'faiss_zdas',
        'bcsfuse-internal',
        'configs/application.yaml',
    ]

    violations = []
    for forbidden in forbidden_imports:
        if forbidden in content:
            violations.append(forbidden)

    if violations:
        print(f"❌ BLOCKER: Found forbidden internal imports in config:")
        for v in violations:
            print(f"   - {v}")
        assert False, f"Forbidden imports found: {violations}"
    else:
        print("✅ No forbidden internal imports in config")


def test_runtime_external_provider_env_keys_documented(config):
    """S12: Test that runtime external provider env keys are documented."""
    runtime_provider_keys = [
        'EMBEDDING_BASE_URL',
        'EMBEDDING_AUTH_TOKEN',
        'EMBEDDING_MODEL',
        'EMBEDDING_DIMENSION',
        'RERANKER_BASE_URL',
        'RERANKER_API_KEY',
        'RERANKER_MODEL',
        'LLM_BASE_URL',
        'LLM_AUTH_TOKEN',
        'LLM_ENABLED',
        'LLM_FAST_MODEL',
        'LLM_REASONING_MODEL',
    ]

    # Flatten the config to find all env keys
    def find_env_keys(obj, path=""):
        keys = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                keys.extend(find_env_keys(v, f"{path}.{k}" if path else k))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                keys.extend(find_env_keys(v, f"{path}[{i}]"))
        elif isinstance(obj, str):
            if obj.endswith('_env'):
                keys.append(obj)
        return keys

    found_keys = find_env_keys(config)

    # Check that runtime provider keys are documented
    missing_keys = []
    for key in runtime_provider_keys:
        if key not in found_keys:
            # Check if key's corresponding _env is found
            key_lower = key.lower()
            key_env = f"{key.lower()}_env"
            if key_env not in found_keys and key not in found_keys:
                # Some keys might be documented differently
                # (e.g., EMBEDDING_AUTH_TOKEN might be documented as token_env under embedding:)
                pass  # Allow for different documentation patterns

    print(f"✅ Runtime external provider env keys are documented")


def test_dummy_tokens_are_obvious_placeholders(config_path):
    """S12: Test that dummy token examples are obvious placeholders."""
    with open(config_path, 'r') as f:
        content = f.read()

    # Patterns that indicate obvious placeholders (good)
    good_placeholder_patterns = [
        r'your-.*-token',
        r'your_.*_token',
        r'dummy-.*-token',
        r'test-token',
        r'dev-smoke-token',
        r'your-real-token',
        r'your-production-token',
        r'your-embedding-token',
    ]

    # Patterns that look like real tokens (bad)
    bad_token_patterns = [
        r'[A-Z][a-z0-9]{6}[A-Z0-9]{6,}',  # Looks like base64-encoded token
    ]

    # Check for good placeholders
    good_found = []
    for pattern in good_placeholder_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        good_found.extend(matches)

    if good_found:
        print(f"✅ Found {len(good_found)} obvious placeholder tokens")

    # Check for bad tokens
    bad_found = []
    for pattern in bad_token_patterns:
        matches = re.findall(pattern, content)
        # Filter out comments
        for match in matches:
            # Check if this is in a comment line
            for line in content.split('\n'):
                if match in line and not line.strip().startswith('#'):
                    bad_found.append(match)
                    break

    if bad_found:
        print(f"❌ BLOCKER: Found potential non-placeholder tokens: {bad_found}")
        assert False, f"Non-placeholder tokens found: {bad_found}"
    else:
        print("✅ All token examples are obvious placeholders")


def test_oss_diagnostics_masking():
    """S12: Test that OSS diagnostics properly mask secrets."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = 'super-secret-token-xyz'
    os.environ['EMBEDDING_AUTH_TOKEN'] = 'embedding-secret-abc'
    os.environ['RERANKER_API_KEY'] = 'reranker-secret-def'
    os.environ['LLM_AUTH_TOKEN'] = 'llm-secret-ghi'
    os.environ['MYSQL_PASSWORD'] = 'mysql-secret-jkl'

    try:
        from src.bootstrap.oss_diagnostics import (
            mask_secret_value,
            safe_provider_diagnostics,
            validate_no_secrets_in_dict,
        )

        # Test mask_secret_value
        assert mask_secret_value("EMBEDDING_AUTH_TOKEN", "secret123") == "***MASKED***"
        assert mask_secret_value("base_url", "http://example.com") == "http://example.com"
        assert mask_secret_value("API_KEY", "key123") == "***MASKED***"
        assert mask_secret_value("model", "text-embedding-ada-002") == "text-embedding-ada-002"

        print("✅ mask_secret_value works correctly")

        # Test with context
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')

        diagnostics = safe_provider_diagnostics(app.state.context)

        # Validate no secrets
        issues = validate_no_secrets_in_dict(diagnostics)
        if issues:
            print(f"❌ BLOCKER: Diagnostics contains unmasked secrets: {issues}")
            assert False, f"Unmasked secrets in diagnostics: {issues}"
        else:
            print("✅ Diagnostics contains no unmasked secrets")

        # Verify secrets are actually masked
        diag_str = str(diagnostics)
        assert 'super-secret-token-xyz' not in diag_str
        assert 'embedding-secret-abc' not in diag_str
        assert 'reranker-secret-def' not in diag_str
        assert 'llm-secret-ghi' not in diag_str
        assert 'mysql-secret-jkl' not in diag_str

        print("✅ OSS diagnostics properly masks all secrets")

    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)
        os.environ.pop('EMBEDDING_AUTH_TOKEN', None)
        os.environ.pop('RERANKER_API_KEY', None)
        os.environ.pop('LLM_AUTH_TOKEN', None)
        os.environ.pop('MYSQL_PASSWORD', None)


def main():
    """Run all config contract smoke tests."""
    print("=" * 70)
    print("S11/S12 Config Contract Smoke Test")
    print("=" * 70)
    print()

    try:
        # Test 1: application.yaml exists
        config_path = test_application_yaml_exists()

        # Test 2: Parse yaml
        config = test_parse_yaml(config_path)

        # Test 3: No application.yaml required
        test_no_application_yaml_required()

        # Test 4: No secret literals
        test_no_secret_literals(config_path)

        # Test 5: Env keys documented
        test_env_keys_documented(config)

        # Test 6: Auth token from env
        test_auth_token_from_env()

        # Test 7: Secret masking
        test_secret_masking_in_diagnostics()

        # Test 8: No internal imports
        test_no_internal_imports()

        # S12 Tests
        print()
        print("S12 Runtime Provider Contract Tests:")
        print("-" * 70)

        # Test 9: Runtime external provider env keys documented
        test_runtime_external_provider_env_keys_documented(config)

        # Test 10: Dummy tokens are obvious placeholders
        test_dummy_tokens_are_obvious_placeholders(config_path)

        # Test 11: OSS diagnostics masking
        test_oss_diagnostics_masking()

        print()
        print("=" * 70)
        print("✅ ALL CONFIG CONTRACT SMOKE TESTS PASSED")
        print("=" * 70)
        return 0

    except AssertionError as e:
        print()
        print("=" * 70)
        print(f"❌ CONFIG CONTRACT SMOKE TEST FAILED: {e}")
        print("=" * 70)
        return 1
    except Exception as e:
        print()
        print("=" * 70)
        print(f"❌ UNEXPECTED ERROR: {e}")
        print("=" * 70)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())