"""Keep public BCS build environments on the repository's pinned compiler."""
from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class RustToolchainTest(unittest.TestCase):
    def setUp(self) -> None:
        self.toolchain = tomllib.loads((ROOT / 'rust-toolchain.toml').read_text())['toolchain']
        self.version = self.toolchain['channel']

    def test_repository_selects_the_pinned_toolchain(self) -> None:
        self.assertEqual(self.version, '1.94.0')
        self.assertIn('llvm-tools', self.toolchain['components'])

    def test_ci_installation_and_execution_use_the_same_version(self) -> None:
        for name in ['unit-tests', 'singlebox-coverage', 'architecture-checks',
                     'bailian-engine-smoke', 'publish-bcs-bridge']:
            with self.subTest(workflow=name):
                workflow = (ROOT / f'.github/workflows/{name}.yml').read_text()
                self.assertIn(f'RUSTUP_TOOLCHAIN: {self.version}', workflow)
                installed = re.findall(r'rustup toolchain install ([\w.]+)', workflow)
                installed += re.findall(r'dtolnay/rust-toolchain@([\w.]+)', workflow)
                self.assertTrue(installed, 'workflow must explicitly install Rust')
                self.assertEqual(set(installed), {self.version})

    def test_rust_builder_images_match_the_repository_toolchain(self) -> None:
        for path in ['docker/services/bcs.dockerfile', 'docker/agent/avernet.dockerfile']:
            with self.subTest(image=path):
                image = (ROOT / path).read_text()
                versions = re.findall(r'FROM rust:([\d.]+)-bookworm', image)
                self.assertEqual(versions, [self.version])
        for path in ['docker/minibcn/Dockerfile.ocb',
                     'apps/bcs/scripts/dockerfiles/arm64_build.Dockerfile']:
            with self.subTest(image=path):
                image = (ROOT / path).read_text()
                self.assertIn(f'ARG RUST_VERSION={self.version}', image)
                self.assertIn(f'test "${{RUST_VERSION}}" = "{self.version}"', image)


if __name__ == '__main__':
    unittest.main()
