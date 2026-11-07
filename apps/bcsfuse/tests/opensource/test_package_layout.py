"""The public application starts from its src layout without an internal host."""

import os
from pathlib import Path
import subprocess
import sys


def test_public_packages_start_without_internal_sdks_and_keep_resource_paths():
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(root / "src"), str(root / "packages/search-core/src"))
    )
    script = """
import importlib.abc
from pathlib import Path
import sys
class PublicOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'bcsfuse_internal', 'zbcsfuse_internal', 'sofapy_base', 'mist_sdk'}:
            raise AssertionError('Internal dependency: ' + fullname)
sys.meta_path.insert(0, PublicOnly())
from search_core.api.models import SearchCommand
from app.bootstrap.opensource_app import create_opensource_app
from app.infra.config.data_paths import resolve_data_path
from app.domain.taxonomy.registry import DEFAULT_TAXONOMY_DIR
from fastapi.testclient import TestClient
root = Path(sys.argv[1])
assert Path(resolve_data_path('data/vector_store.db')) == root / 'data/vector_store.db'
assert DEFAULT_TAXONOMY_DIR.resolve() == root / 'configs/taxonomy'
with TestClient(create_opensource_app('test')) as client:
    assert client.get('/health').status_code == 200
assert 'src.bootstrap.app_factory' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_public_dockerfiles_reference_existing_build_inputs():
    import shlex

    repository = Path(__file__).resolve().parents[4]
    for relative in ("apps/bcsfuse/Dockerfile", "docker/services/bcsfuse.dockerfile"):
        instructions = (repository / relative).read_text().splitlines()
        for line in instructions:
            if not line.startswith("COPY "):
                continue
            inputs = [part for part in shlex.split(line)[1:-1] if not part.startswith("--")]
            for source in inputs:
                assert (repository / source).exists(), (relative, source)
        copy_source = next(i for i, line in enumerate(instructions) if line.startswith("COPY apps/bcsfuse/src "))
        install = next(i for i, line in enumerate(instructions) if '-e ".[dev]"' in line)
        assert copy_source < install, "src-layout package must exist before editable installation"


def test_logging_supports_the_shallow_public_container_layout():
    script = """
from pathlib import Path
from unittest.mock import patch
import logging
import app.infra.logging as package
import app.infra.logging.logging_config as config
source = Path(package.__file__).read_text()
namespace = dict(__name__='app.infra.logging', __package__='app.infra.logging',
                 __file__='/app/src/app/infra/logging/__init__.py')
with patch.object(Path, 'mkdir'):
    exec(compile(source, namespace['__file__'], 'exec'), namespace)
    assert namespace['DEFAULT_LOG_DIR'] == Path('/scripts/.dependencies/logs')
    config.__file__ = '/app/src/app/infra/logging/logging_config.py'
    with patch.object(logging.handlers, 'RotatingFileHandler', return_value=logging.NullHandler()) as handler:
        config.configure_logging(enable_file_handler=True)
        assert handler.call_args.args[0] == Path('/scripts/.dependencies/logs/bcsfuse.log')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
