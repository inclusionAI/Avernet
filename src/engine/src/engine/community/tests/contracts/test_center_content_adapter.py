import hashlib
import io
import time
import zipfile
from pathlib import Path

from engine.community.kernel.center_content import (
    CenterContentAdapter,
    CenterContentPendingPackage,
    CenterContentPreparationStatus,
    CenterContentReadyPackage,
)
from engine.community.plugins.skills_pool.center_content import (
    DownloadedCenterContentAdapter,
    MountedCenterContentAdapter,
)


def _zip() -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("SKILL.md", "# contract\n")
    return target.getvalue()


def test_mounted_adapter_prepares_an_exact_mounted_package(tmp_path: Path) -> None:
    exact = tmp_path / "skill-id" / "1"
    exact.mkdir(parents=True)
    (exact / "SKILL.md").write_text("# contract\n", encoding="utf-8")
    mounted = MountedCenterContentAdapter(is_mounted=lambda _path: True)

    prepared = mounted.prepare(
        center_root=tmp_path,
        package=CenterContentPendingPackage("skill-id", "1"),
    )

    assert isinstance(mounted, CenterContentAdapter)
    assert prepared.status is CenterContentPreparationStatus.READY


def test_downloaded_adapter_prepares_an_exact_verified_package(tmp_path: Path) -> None:
    archive = _zip()
    downloaded = DownloadedCenterContentAdapter(
        fetch=lambda *_args, **_kwargs: archive,
        allowed_hosts=("objects.example.test",),
        resolve_host=lambda _host: ("93.184.216.34",),
    )
    package = CenterContentReadyPackage(
        skill_uuid="skill-id",
        sc_version_number="1",
        package_sha256=hashlib.sha256(archive).hexdigest(),
        package_size=len(archive),
        signed_url="https://objects.example.test/exact.zip",
        expires_at="2026-09-09T02:02:03Z",
    )

    prepared = downloaded.prepare(center_root=tmp_path, package=package)
    assert prepared.status is CenterContentPreparationStatus.PENDING
    deadline = time.monotonic() + 2
    while prepared.status is CenterContentPreparationStatus.PENDING:
        if time.monotonic() >= deadline:
            raise AssertionError("downloaded adapter did not publish its cache")
        time.sleep(0.01)
        prepared = downloaded.prepare(center_root=tmp_path, package=package)

    assert isinstance(downloaded, CenterContentAdapter)
    assert prepared.status is CenterContentPreparationStatus.READY
    assert (tmp_path / "skill-id/1/SKILL.md").read_text(encoding="utf-8") == "# contract\n"
