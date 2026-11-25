from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[3]
MERGER = REPO_ROOT / "singlebox/ci/merge_junit_reports.py"


def write_report(path: Path, *, name: str, time: str, failure: bool = False) -> None:
    child = f'<failure message="bad">boom</failure>' if failure else ''
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites name="pytest">'
        f'<testsuite name="pytest" tests="1" errors="0" failures="{int(failure)}" skipped="0" time="{time}">'
        f'<testcase classname="suite" name="{name}" time="{time}">{child}</testcase>'
        '</testsuite></testsuites>',
        encoding="utf-8",
    )


def test_merges_cases_and_aggregates_counts(tmp_path: Path):
    first = tmp_path / "first.xml"
    second = tmp_path / "second.xml"
    output = tmp_path / "merged.xml"
    write_report(first, name="one", time="1.25")
    write_report(second, name="two", time="2.75", failure=True)

    result = subprocess.run(
        [sys.executable, str(MERGER), str(output), str(first), str(second)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    root = ET.parse(output).getroot()
    suite = root.find("testsuite")
    assert suite is not None
    assert suite.attrib["tests"] == "2"
    assert suite.attrib["failures"] == "1"
    assert suite.attrib["time"] == "4.0"
    assert {case.attrib["name"] for case in suite.findall("testcase")} == {"one", "two"}
    assert suite.find("testcase/failure") is not None


def test_rejects_missing_or_malformed_input(tmp_path: Path):
    output = tmp_path / "merged.xml"
    missing = subprocess.run(
        [sys.executable, str(MERGER), str(output), str(tmp_path / "missing.xml")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert missing.returncode != 0

    malformed = tmp_path / "malformed.xml"
    malformed.write_text("not xml", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(MERGER), str(output), str(malformed)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
