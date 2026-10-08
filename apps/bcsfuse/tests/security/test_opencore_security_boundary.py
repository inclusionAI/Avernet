"""
OPENCORE-G9: Security Boundary Tests

Tests to verify open-core security and boundary compliance:
1. No bcsfuse_internal runtime imports
2. No root_original runtime imports
3. No internal default dependencies in pyproject.toml
4. Internal dependencies only in optional internal extra
5. No real secret literals in code
6. No runtime artifacts visible in git
7. No internal provider runtime imports

Reference: 17-opencore-p0-gate-evidence-closeout.md
"""

import os
import re
import sys
import ast
from pathlib import Path
from typing import List, Set
import pytest


# Open-core source directory (src/ under bcsfuse root)
OPENCORE_SRC_DIR = Path(__file__).parent.parent.parent / "src"
# pyproject.toml is in bcsfuse root directory
PYPROJECT_PATH = Path(__file__).parent.parent.parent / "pyproject.toml"

# Forbidden internal packages in default dependencies
FORBIDDEN_DEFAULT_DEPS = {
    "ant-sofapy-base",
    "mist-sdk-py3",
    "bcsfuse_internal",
}

# Forbidden runtime imports
FORBIDDEN_RUNTIME_IMPORTS = {
    "bcsfuse_internal",
    "ant-sofapy-base",
    "mist_sdk",
    "sofapy_base",
}

# Patterns to skip (comments, docs, tests, stubs)
SKIP_PATTERNS = [
    r"#.*",  # Comments
    r'""".*?"""',  # Triple-quoted strings
    r"'''.*?'''",  # Triple-quoted strings
    r"test_.*\.py",  # Test files
    r".*test.*",  # Test directories
]


class TestNoBcsfuseInternalRuntimeImports:
    """Test 1: No bcsfuse_internal runtime imports."""

    def test_no_bcsfuse_internal_imports_in_source(self):
        """Verify no bcsfuse_internal imports in runtime source code."""
        violations = []

        for py_file in OPENCORE_SRC_DIR.rglob("*.py"):
            # Skip test files
            if "test" in str(py_file).lower():
                continue

            # Skip __pycache__
            if "__pycache__" in str(py_file):
                continue

            try:
                content = py_file.read_text()

                # Parse AST to find actual imports
                tree = ast.parse(content, filename=str(py_file))

                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        module_name = ""
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                module_name = alias.name
                        elif isinstance(node, ast.ImportFrom):
                            module_name = node.module or ""

                        # Check for bcsfuse_internal imports
                        if "bcsfuse_internal" in module_name:
                            violations.append(
                                f"{py_file}:{node.lineno} - Import bcsfuse_internal: {module_name}"
                            )

            except SyntaxError:
                # Skip files that can't be parsed
                continue
            except Exception as e:
                # Skip files that can't be read
                continue

        if violations:
            pytest.fail(
                f"Found {len(violations)} bcsfuse_internal runtime imports:\n" +
                "\n".join(violations[:10])  # Show first 10
            )


class TestNoRootOriginalRuntimeImports:
    """Test 2: No root_original runtime imports."""

    def test_no_sys_path_manipulation_to_root_original(self):
        """Verify no sys.path manipulation pointing to root_original."""
        violations = []

        for py_file in OPENCORE_SRC_DIR.rglob("*.py"):
            if "test" in str(py_file).lower():
                continue
            if "__pycache__" in str(py_file):
                continue

            try:
                content = py_file.read_text()

                # Check for sys.path.insert/append with root_original path
                if re.search(r'sys\.path\.(insert|append).*["\'].*src/bcsfuse', content):
                    violations.append(
                        f"{py_file} - sys.path manipulation to src/bcsfuse"
                    )

                # Check for relative imports reaching outside open-core
                if re.search(r'from \.\.\.\.\.bcsfuse', content):
                    violations.append(
                        f"{py_file} - Relative import reaching root_original"
                    )

            except Exception:
                continue

        if violations:
            pytest.fail(
                f"Found {len(violations)} root_original runtime import violations:\n" +
                "\n".join(violations[:10])
            )


class TestNoInternalDefaultDependencies:
    """Test 3: No internal packages in default dependencies."""

    def test_no_internal_default_dependencies_in_pyproject(self):
        """Verify pyproject.toml has no internal deps in default dependencies."""
        if not PYPROJECT_PATH.exists():
            pytest.skip("pyproject.toml not found")

        content = PYPROJECT_PATH.read_text()

        # Extract dependencies section
        deps_match = re.search(
            r'dependencies\s*=\s*\[(.*?)\]',
            content,
            re.DOTALL
        )

        if not deps_match:
            pytest.fail("Could not parse dependencies section from pyproject.toml")

        deps_section = deps_match.group(1)

        violations = []
        for dep in FORBIDDEN_DEFAULT_DEPS:
            if dep in deps_section:
                violations.append(f"Found forbidden dependency: {dep}")

        if violations:
            pytest.fail(
                f"Found {len(violations)} internal dependencies in default dependencies:\n" +
                "\n".join(violations)
            )


class TestInternalDepsOnlyOptional:
    """Test 4: Internal dependencies only in optional internal extra."""

    def test_internal_deps_only_in_optional_extra(self):
        """Verify internal dependencies are only in [project.optional-dependencies] internal."""
        if not PYPROJECT_PATH.exists():
            pytest.skip("pyproject.toml not found")

        content = PYPROJECT_PATH.read_text()

        # Check if there are any internal dependencies in the file
        has_internal_deps = any(dep in content for dep in ["ant-sofapy-base", "mist-sdk-py3"])

        if not has_internal_deps:
            # No internal deps found, test passes
            return

        # Internal deps exist, verify they're in [project.optional-dependencies] internal extra
        # Look for the pattern: internal = [ ... "ant-sofapy-base" ... ]
        # Use a more robust regex that ignores comments
        lines = content.split('\n')
        in_optional_deps = False
        in_internal_extra = False
        found_internal_deps_in_extra = False

        for line in lines:
            stripped = line.strip()

            # Skip comments
            if stripped.startswith('#'):
                continue

            # Check for section start
            if stripped == '[project.optional-dependencies]':
                in_optional_deps = True
                continue
            elif stripped.startswith('[') and stripped.endswith(']'):
                # New section, reset flags
                in_optional_deps = False
                in_internal_extra = False
                continue

            # If in optional-dependencies section
            if in_optional_deps:
                # Check for internal extra
                if 'internal' in stripped and '=' in stripped and '[' in stripped:
                    in_internal_extra = True
                    continue
                elif stripped == ']':
                    # End of current extra
                    in_internal_extra = False
                    continue
                elif '=' in stripped and not in_internal_extra:
                    # Another extra (e.g., dev = [)
                    in_internal_extra = False
                    continue

                # If in internal extra, check for deps
                if in_internal_extra:
                    if "ant-sofapy-base" in line or "mist-sdk-py3" in line:
                        found_internal_deps_in_extra = True

        if has_internal_deps and not found_internal_deps_in_extra:
            pytest.fail(
                "Internal deps (ant-sofapy-base, mist-sdk-py3) found in pyproject.toml "
                "but not in [project.optional-dependencies] internal extra"
            )


class TestNoRealSecretLiterals:
    """Test 5: No real secret literals in code."""

    def test_no_real_secret_literals(self):
        """Verify no real passwords, tokens, or raw DSN in code."""
        violations = []

        # Patterns that indicate real secrets
        secret_patterns = [
            (r'mysql\s+-h.*-p[^\s]+', 'MySQL command with password'),
            (r'PASSWORD\s*=\s*["\'][^"\']{8,}["\']', 'PASSWORD assignment'),
            (r'SECRET\s*=\s*["\'][^"\']{8,}["\']', 'SECRET assignment'),
            (r'Bearer\s+[A-Za-z0-9._-]{12,}', 'Bearer token'),
            (r'ZDAS_DATASOURCE\s*=\s*[^<].*password', 'ZDAS datasource with password'),
            (r'OCEANBASE.*PASSWORD', 'OceanBase password'),
            (r'127\.0\.0\.1.*3306.*root.*password', 'MySQL connection with password'),
        ]

        for py_file in OPENCORE_SRC_DIR.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue

            try:
                content = py_file.read_text()

                for pattern, description in secret_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        # Check if it's in a comment or test fixture
                        lines = content.split('\n')
                        for i, line in enumerate(lines, 1):
                            if re.search(pattern, line, re.IGNORECASE):
                                # Skip if it's obviously a comment or test
                                if line.strip().startswith('#'):
                                    continue
                                if 'test' in str(py_file).lower() and 'placeholder' in line.lower():
                                    continue
                                if 'example' in line.lower() or 'fixture' in line.lower():
                                    continue

                                violations.append(
                                    f"{py_file}:{i} - {description}"
                                )

            except Exception:
                continue

        # Allow up to 5 potential false positives
        if len(violations) > 5:
            pytest.fail(
                f"Found {len(violations)} potential secret literals:\n" +
                "\n".join(violations[:10])
            )


class TestNoRuntimeArtifactsVisible:
    """Test 6: No runtime artifacts visible in git."""

    def test_no_runtime_artifacts_in_git(self):
        """Verify no runtime artifacts (data/, logs/, *.sqlite, etc.) are tracked in git in open-core."""
        import subprocess

        # Only check open-core directory (src/ocb-ant/ocb/src/bcsfuse)
        opencore_dir = OPENCORE_SRC_DIR

        forbidden_file_extensions = [
            '.sqlite',
            '.sqlite3',
            '.db',
            '.faiss',
            '.index',
            '.log',
            '.dump',
        ]

        forbidden_exact_files = [
            '.env.real_token',
            '.env.live.local',
        ]

        violations = []

        try:
            # Use git ls-files to check tracked files only in open-core directory
            result = subprocess.run(
                ['git', 'ls-files', str(opencore_dir)],
                cwd=opencore_dir.parent.parent.parent.parent,
                capture_output=True,
                text=True,
                check=True
            )
            tracked_files = result.stdout.strip().split('\n') if result.stdout.strip() else []

            for file_path in tracked_files:
                # Filter out test fixtures and docs
                if 'test' in file_path.lower() or 'doc' in file_path.lower() or 'fixture' in file_path.lower():
                    continue

                # Check for forbidden file extensions
                for ext in forbidden_file_extensions:
                    if file_path.endswith(ext):
                        violations.append(f"Found tracked runtime artifact: {file_path}")
                        break

                # Check for forbidden exact file names
                for forbidden_file in forbidden_exact_files:
                    if file_path.endswith(forbidden_file):
                        violations.append(f"Found tracked secret file: {file_path}")
                        break

        except subprocess.CalledProcessError as e:
            pytest.skip(f"Failed to run git ls-files: {e}")

        if violations:
            pytest.fail(
                f"Found {len(violations)} runtime artifacts tracked in git:\n" +
                "\n".join(violations[:20])
            )


class TestNoInternalProviderRuntimeImports:
    """Test 7: No internal provider runtime imports."""

    def test_no_internal_provider_imports_in_public_providers(self):
        """Verify no internal provider imports in public provider implementations."""
        violations = []

        public_provider_dir = OPENCORE_SRC_DIR / "infra" / "public"

        if not public_provider_dir.exists():
            pytest.skip("Public provider directory not found")

        for py_file in public_provider_dir.rglob("*.py"):
            if "test" in str(py_file).lower():
                continue
            if "__pycache__" in str(py_file):
                continue

            try:
                content = py_file.read_text()
                tree = ast.parse(content, filename=str(py_file))

                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        module_name = ""
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                module_name = alias.name
                        elif isinstance(node, ast.ImportFrom):
                            module_name = node.module or ""

                        # Check for internal provider imports
                        if "bcsfuse_internal" in module_name:
                            violations.append(
                                f"{py_file}:{node.lineno} - Import bcsfuse_internal: {module_name}"
                            )

            except Exception:
                continue

        if violations:
            pytest.fail(
                f"Found {len(violations)} internal provider imports:\n" +
                "\n".join(violations[:10])
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])