from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BCS = ROOT / 'apps/bcs'
CRATES = ['crates/bootstrap/bcs', 'crates/tools/bcs-cli']


def command(cwd: Path, *args: str) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@unittest.skipUnless(shutil.which('cargo') and shutil.which('rustc'), 'Rust is required')
class BuildMetadataTest(unittest.TestCase):
    def fixture(self, crate: str) -> tuple[Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / 'repository'
        root.mkdir()
        command(root, 'git', 'init', '--initial-branch=main')
        command(root, 'git', 'config', 'user.name', 'Build test')
        command(root, 'git', 'config', 'user.email', 'build-test@example.com')
        project = root / crate
        (project / 'src').mkdir(parents=True)
        shutil.copy(BCS / crate / 'build.rs', project / 'build.rs')
        support = BCS / 'build_support'
        if support.exists():
            shutil.copytree(support, root / 'build_support')
        (root / 'chrono/src').mkdir(parents=True)
        (root / 'chrono/Cargo.toml').write_text('[package]\nname="chrono"\nversion="0.0.0"\nedition="2021"\n')
        (root / 'chrono/src/lib.rs').write_text('pub struct Local; impl Local { pub fn now() -> Self { Self } pub fn format(&self, _: &str) -> &str { "2026-10-29" } }\n')
        (project / 'Cargo.toml').write_text('[package]\nname="metadata-fixture"\nversion="0.0.0"\nedition="2021"\n[build-dependencies]\nchrono={path="../../../chrono"}\n')
        (project / 'src/main.rs').write_text('fn main() { println!("{}", env!("GIT_COMMIT_HASH")); }\n')
        (root / '.gitignore').write_text('target/\n')
        command(root, 'git', 'add', '.')
        command(root, 'git', 'commit', '-m', 'fixture')
        return root, project

    def build(self, project: Path) -> str:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('CARGO_', 'RUST', 'GIT_'))}
        result = subprocess.run(['cargo', 'build', '--offline', '-vv'], cwd=project,
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def assert_commit(self, root: Path, project: Path) -> None:
        self.assertEqual(command(project, str(project / 'target/debug/metadata-fixture')),
                         command(root, 'git', 'rev-parse', '--short', 'HEAD'))

    def test_unchanged_real_build_scripts_do_not_recompile(self) -> None:
        for crate in CRATES:
            with self.subTest(crate=crate):
                root, project = self.fixture(crate)
                self.build(project)
                self.assert_commit(root, project)
                second = self.build(project)
                self.assertNotIn('Compiling metadata-fixture', second)
                self.assertIn('Fresh metadata-fixture', second)

    def test_detached_head_commits_refresh_without_permanent_dirtiness(self) -> None:
        for crate in CRATES:
            with self.subTest(crate=crate):
                root, project = self.fixture(crate)
                command(root, 'git', 'switch', '--detach')
                self.build(project)
                self.assertIn('Fresh metadata-fixture', self.build(project))
                command(root, 'git', 'commit', '--allow-empty', '-m', 'detached commit')
                self.build(project)
                self.assert_commit(root, project)
                self.assertIn('Fresh metadata-fixture', self.build(project))

    def test_new_commits_refresh_the_embedded_hash(self) -> None:
        for crate in CRATES:
            with self.subTest(crate=crate):
                root, project = self.fixture(crate)
                self.build(project)
                command(root, 'git', 'commit', '--allow-empty', '-m', 'next commit')
                self.assertIn('Compiling metadata-fixture', self.build(project))
                self.assert_commit(root, project)
                self.assertIn('Fresh metadata-fixture', self.build(project))

    def test_linked_worktrees_and_packed_refs_stay_fresh_then_track_commits(self) -> None:
        for crate in CRATES:
            with self.subTest(crate=crate):
                root, _ = self.fixture(crate)
                linked = root.parent / 'linked'
                command(root, 'git', 'worktree', 'add', '-b', 'linked', str(linked))
                command(root, 'git', 'pack-refs', '--all')
                project = linked / crate
                self.build(project)
                self.assert_commit(linked, project)
                self.assertIn('Fresh metadata-fixture', self.build(project))
                command(linked, 'git', 'commit', '--allow-empty', '-m', 'worktree commit')
                self.build(project)
                self.assert_commit(linked, project)
                self.assertIn('Fresh metadata-fixture', self.build(project))


if __name__ == '__main__':
    unittest.main()
