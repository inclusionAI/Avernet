from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BCS = ROOT / 'apps/bcs'
RUNNER = BCS / 'scripts/ci_contract_test.py'


@unittest.skipUnless(shutil.which('cargo-nextest') and shutil.which('cargo-llvm-cov'),
                     'nextest, llvm-cov and llvm-tools are required')
class BcsBuildReuseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.project = cls.root / 'bcs'
        (cls.project / 'scripts').mkdir(parents=True)
        (cls.project / 'src').mkdir()
        (cls.project / '.config').mkdir()
        shutil.copy(BCS / 'scripts/ci_test.sh', cls.project / 'scripts/ci_test.sh')
        shutil.copy(BCS / '.config/nextest.toml', cls.project / '.config/nextest.toml')
        reusable = BCS / 'scripts/ci/reusable_coverage.sh'
        if reusable.exists():
            (cls.project / 'scripts/ci').mkdir()
            shutil.copy(reusable, cls.project / 'scripts/ci/reusable_coverage.sh')
        if RUNNER.exists():
            shutil.copy(RUNNER, cls.project / 'scripts/ci_contract_test.py')
        (cls.project / 'Cargo.toml').write_text('[package]\nname="contract-fixture"\nversion="0.0.0"\nedition="2021"\n')
        (cls.project / 'tests').mkdir()
        (cls.project / 'tests/integration_collab_reachability.rs').write_text('// Empty target for the real nextest config override.\n')
        (cls.project / 'src/lib.rs').write_text('''
#[test] fn unit_passes() { assert_eq!(1 + 1, 2); }
#[test] #[ignore] fn contract_passes() {
    assert_eq!(std::env::var("BCS_TEST_MYSQL_URL").unwrap(), "isolated-contract-db");
    println!("authority-mysql-gate=fixture scenarios=1");
}
#[test] #[ignore] fn contract_fails() { panic!("expected contract failure"); }
#[test] fn contract_not_ignored() {}
mod first { #[test] #[ignore] fn ambiguous() { panic!("must not execute"); } }
mod second { #[test] #[ignore] fn ambiguous() { panic!("must not execute"); } }
''')
        cls.source_text = (cls.project / 'src/lib.rs').read_text()
        cls.env = {key: value for key, value in os.environ.items()
                   if not key.startswith(('CARGO_', 'RUST', 'GIT_'))}
        result = subprocess.run(['cargo', 'generate-lockfile', '--offline'], cwd=cls.project,
                                env=cls.env, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.build = subprocess.run(['bash', 'scripts/ci_test.sh', '--coverage', '--reuse-build', '--fast-fail'],
                                   cwd=cls.project, env=cls.env, text=True, capture_output=True)

    def require_build(self) -> None:
        self.assertEqual(self.build.returncode, 0, self.build.stdout + self.build.stderr)

    def run_contract(self, test: str, package: str = 'contract-fixture',
                     binary: str = 'contract_fixture', database: str = 'isolated-contract-db') -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(self.project / 'scripts/ci_contract_test.py'),
                               '--package', package, '--binary', binary, '--test', test],
                              cwd=self.project, env={**self.env, 'BCS_TEST_MYSQL_URL': database},
                              text=True, capture_output=True)

    def test_ci_exports_the_build_without_changing_the_unit_inventory(self) -> None:
        self.require_build()
        self.assertTrue((self.project / 'testresult/cargo-metadata.json').is_file())
        binaries = json.loads((self.project / 'testresult/test-binaries.json').read_text())
        self.assertEqual(len(binaries['rust-binaries']), 2)
        self.assertEqual(Path(binaries['rust-build-meta']['target-directory']),
                         self.project.resolve() / 'target/llvm-cov-target')
        import xml.etree.ElementTree as ET
        junit = ET.parse(self.project / 'testresult/junit.xml').getroot()
        self.assertEqual(int(junit.get('tests')), 2)
        self.assertTrue((self.project / 'testresult/cobertura.xml').is_file())

    def test_contract_runs_without_recompiling_or_changing_unit_reports(self) -> None:
        self.require_build()
        reports = {path: path.read_bytes() for path in (self.project / 'testresult').glob('*.xml')}
        (self.project / 'src/lib.rs').write_text('compile_error!("a contract must reuse the existing binary");\n')
        result = self.run_contract('contract_passes')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('authority-mysql-gate=fixture scenarios=1', result.stdout)
        self.assertIn('test result: ok. 1 passed; 0 failed; 0 ignored', result.stdout)
        self.assertNotIn('Compiling ', result.stdout + result.stderr)
        for path, original in reports.items():
            self.assertEqual(path.read_bytes(), original)
        self.assertTrue(list((self.project / 'testresult/mysql-profraw').glob('*.profraw')))
        self.assertFalse(list(self.project.glob('*.profraw')))

    def test_default_coverage_mode_still_runs_the_same_unit_tests(self) -> None:
        self.require_build()
        source = self.project / 'src/lib.rs'
        previous = source.read_text()
        try:
            source.write_text(self.source_text)
            result = subprocess.run(['bash', 'scripts/ci_test.sh', '--coverage', '--fast-fail'],
                                    cwd=self.project, env=self.env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            import xml.etree.ElementTree as ET
            junit = ET.parse(self.project / 'testresult/junit.xml').getroot()
            self.assertEqual(int(junit.get('tests')), 2)
            self.assertEqual(int(junit.get('failures')), 0)
        finally:
            source.write_text(previous)

    def test_reuse_without_coverage_is_rejected(self) -> None:
        result = subprocess.run(['bash', 'scripts/ci_test.sh', '--reuse-build'],
                                cwd=self.project, env=self.env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--reuse-build requires --coverage', result.stderr)

    def test_contract_failures_are_not_hidden(self) -> None:
        self.require_build()
        result = self.run_contract('contract_fails')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('test result: ok.', result.stdout)

    def test_zero_matches_wrong_binary_and_nonignored_tests_fail(self) -> None:
        self.require_build()
        for test, package, binary in [('missing', 'contract-fixture', 'contract_fixture'),
                                      ('contract_passes', 'wrong-package', 'contract_fixture'),
                                      ('contract_passes', 'contract-fixture', 'wrong-binary'),
                                      ('contract_not_ignored', 'contract-fixture', 'contract_fixture')]:
            with self.subTest(test=test, package=package, binary=binary):
                result = self.run_contract(test, package, binary)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('test result: ok.', result.stdout)

    def test_ambiguous_contract_is_rejected_before_execution(self) -> None:
        self.require_build()
        result = self.run_contract('ambiguous')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('exactly one ignored test', result.stderr)
        self.assertNotIn('must not execute', result.stdout + result.stderr)

    def test_missing_database_fails_before_execution(self) -> None:
        self.require_build()
        result = self.run_contract('contract_passes', database='')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('BCS_TEST_MYSQL_URL', result.stderr)

    def test_workflow_contract_selectors_name_real_ignored_source_tests(self) -> None:
        import re
        import tomllib
        packages = {}
        for manifest in (BCS / 'crates').rglob('Cargo.toml'):
            if 'target' not in manifest.parts:
                packages[tomllib.loads(manifest.read_text())['package']['name']] = manifest.parent
        workflow = (ROOT / '.github/workflows/unit-tests.yml').read_text().split('\n  backend:', 1)[0]
        selections = re.findall(r'ci_contract_test.py --package (\S+) --binary (\S+) --test (\w+)', workflow)
        self.assertEqual(len(selections), 13)
        for package, binary, test in selections:
            with self.subTest(package=package, binary=binary, test=test):
                source_root = packages[package]
                if package != 'bcs-admin':
                    self.assertTrue((source_root / 'tests' / f'{binary}.rs').is_file())
                matches = []
                for source in source_root.rglob('*.rs'):
                    matches.extend(re.findall(r'#\[ignore[^\]]*\]\s*(?:async\s+)?fn\s+(' + re.escape(test) + r')\s*\(', source.read_text()))
                self.assertEqual(matches, [test])

    def test_workflow_runs_ci_tooling_regressions(self) -> None:
        workflow = (ROOT / '.github/workflows/unit-tests.yml').read_text()
        self.assertIn("python3 -m unittest discover -s singlebox/ci/tests -p 'test_bcs_*.py'", workflow)

    def test_workflow_preserves_contracts_and_coverage_thresholds(self) -> None:
        workflow = (ROOT / '.github/workflows/unit-tests.yml').read_text()
        bcs_job = workflow.split('\n  backend:', 1)[0]
        self.assertIn('bash scripts/ci_test.sh --coverage --reuse-build', bcs_job)
        self.assertEqual(bcs_job.count('python3 scripts/ci_contract_test.py'), 13)
        self.assertNotIn('run: cargo test ', bcs_job)
        for inventory in ['edge-authority scenarios=55', 'provider-lifecycle scenarios=30',
                          'group-authorization scenarios=9', 'session-authorization scenarios=6']:
            self.assertEqual(bcs_job.count(inventory), 1)
        for gate in ['--pass-rate-min 100', '--overall-line-min 84', '--changed-line-min 80']:
            self.assertIn(gate, bcs_job)



if __name__ == '__main__':
    unittest.main()
