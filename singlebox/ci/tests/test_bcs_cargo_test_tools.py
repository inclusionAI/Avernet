from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INSTALLER = ROOT / 'apps/bcs/scripts/install_cargo_test_tools.sh'
CHECKSUMS = ROOT / 'apps/bcs/scripts/cargo_test_tools.sha256'


class CargoTestToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.releases = self.root / 'releases'
        self.releases.mkdir()
        shutil.copy(INSTALLER, self.root / INSTALLER.name)
        self.env = {**os.environ, 'CARGO_HOME': str(self.root / 'cargo'),
                    'PATH': f'{self.bin}:/usr/bin:/bin',
                    'TEST_ROOT': str(self.root), 'TEST_PLATFORM': 'Linux',
                    'TEST_ARCH': 'x86_64'}
        self.script('uname', 'if [ "$1" = -s ]; then echo "$TEST_PLATFORM"; else echo "$TEST_ARCH"; fi')
        self.script('curl', '''
output=""; url=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then output="$2"; shift 2; else url="$1"; shift; fi
done
printf '%s\\n' "$url" >> "$TEST_ROOT/downloads"
[ "${TEST_DOWNLOAD_FAIL:-0}" = 0 ] || exit 22
cp "$TEST_ROOT/releases/${url##*/}" "$output"
''')
        self.script('cargo', 'printf "%s\\n" "$*" >> "$TEST_ROOT/cargo-calls"')
        self.checksums: list[str] = []
        for target in ['x86_64-unknown-linux-musl', 'universal-apple-darwin']:
            self.archive('cargo-nextest', '0.9.150', target)
        self.archive('cargo-llvm-cov', '0.9.1', 'x86_64-unknown-linux-musl')
        (self.root / CHECKSUMS.name).write_text(''.join(self.checksums))

    def script(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text('#!/bin/bash\nset -eu\n' + body + '\n')
        path.chmod(0o755)

    def archive(self, name: str, version: str, target: str) -> None:
        asset = f'{name}-{version}-{target}.tar.gz' if name == 'cargo-nextest' else f'{name}-{target}.tar.gz'
        path = self.releases / asset
        content = f'#!/bin/sh\necho "{name} {version}"\n'.encode()
        with tarfile.open(path, 'w:gz') as tar:
            info = tarfile.TarInfo(f'package/{name}')
            info.mode = 0o755
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.checksums.append(f'{digest}  {version}/{asset}\n')

    def run_installer(self, *tools: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(['bash', str(self.root / INSTALLER.name), *tools],
                              env=self.env, text=True, capture_output=True)

    def test_workflow_downloads_prebuilt_tools_instead_of_compiling(self) -> None:
        job = (ROOT / '.github/workflows/unit-tests.yml').read_text().split('\n  backend:', 1)[0]
        self.assertIn('run: bash scripts/install_cargo_test_tools.sh', job)
        self.assertNotIn('cargo install cargo-nextest --locked', job)

    def test_verified_tools_are_installed_without_cargo_install(self) -> None:
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ['cargo-nextest', 'cargo-llvm-cov']:
            self.assertTrue((self.root / 'cargo/bin' / name).is_file(), result.stdout)
        self.assertFalse((self.root / 'cargo-calls').exists())

    def test_wrong_existing_version_is_replaced_but_matching_version_is_reused(self) -> None:
        self.script('cargo-nextest', 'echo "cargo-nextest 0.0.1"')
        result = self.run_installer('nextest')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('0.9.150', (self.root / 'cargo/bin/cargo-nextest').read_text())
        (self.root / 'downloads').unlink()
        result = self.run_installer('nextest')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'downloads').exists())

    def test_macos_nextest_uses_the_published_universal_asset(self) -> None:
        self.env.update(TEST_PLATFORM='Darwin', TEST_ARCH='arm64')
        result = self.run_installer('nextest')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('universal-apple-darwin.tar.gz', (self.root / 'downloads').read_text())
        self.assertFalse((self.root / 'cargo-calls').exists())

    def test_version_prefix_is_not_a_matching_version(self) -> None:
        self.script('cargo-nextest', 'echo "cargo-nextest 0.9.1500"')
        result = self.run_installer('nextest')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'cargo/bin/cargo-nextest').exists())

    def test_unknown_platform_uses_pinned_source_fallback(self) -> None:
        self.env['TEST_PLATFORM'] = 'Unsupported'
        result = self.run_installer('nextest')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('install cargo-nextest --locked --version 0.9.150',
                      (self.root / 'cargo-calls').read_text())

    def test_checksum_mismatch_fails_closed_before_installation(self) -> None:
        for archive in self.releases.iterdir():
            archive.write_bytes(archive.read_bytes() + b'tampered')
        result = self.run_installer('nextest')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('checksum', result.stderr.lower())
        self.assertFalse((self.root / 'cargo/bin/cargo-nextest').exists())
        self.assertFalse((self.root / 'cargo-calls').exists())

    def test_download_failure_falls_back_to_the_same_pinned_source_version(self) -> None:
        self.env['TEST_DOWNLOAD_FAIL'] = '1'
        result = self.run_installer('nextest')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('install cargo-nextest --locked --version 0.9.150',
                      (self.root / 'cargo-calls').read_text())

    def test_release_checksums_cover_every_default_supported_archive(self) -> None:
        self.assertTrue(CHECKSUMS.is_file(), 'Pinned release checksums are missing')
        entries = CHECKSUMS.read_text().splitlines()
        for version, prefix, targets in [
            ('0.9.150', 'cargo-nextest-0.9.150-', ['universal-apple-darwin']),
            ('0.9.1', 'cargo-llvm-cov-', ['aarch64-apple-darwin', 'x86_64-apple-darwin']),
        ]:
            targets += [f'{arch}-unknown-linux-{libc}' for arch in ['aarch64', 'x86_64'] for libc in ['musl', 'gnu']]
            for target in targets:
                self.assertTrue(any(line.endswith(f'  {version}/{prefix}{target}.tar.gz') for line in entries))


if __name__ == '__main__':
    unittest.main()
