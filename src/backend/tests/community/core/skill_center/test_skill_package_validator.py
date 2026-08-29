"""Behavior contract for the shared Skill package validator."""

from __future__ import annotations

import io
import zipfile

import pytest

from agentclaw.community.core.skill_center import skill_package as package_module
from agentclaw.community.core.skill_center.skill_package import (
    SkillPackageInvalidError,
    SkillPackageTooLargeError,
    SkillPackageValidator,
    ValidatedSkillPackage,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser


def _skill_md(
    name: str = "weather",
    description: str = "Reports the weather.",
    *,
    config: str = "",
) -> bytes:
    config_line = f"config: {config}\n" if config else ""
    return (
        f"---\nname: {name}\ndescription: {description}\n{config_line}---\n# Weather\n"
    ).encode()


def _zip(
    entries: list[tuple[str, bytes]], *, attrs: dict[str, int] | None = None
) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for path, content in entries:
            info = zipfile.ZipInfo(path)
            if attrs and path in attrs:
                info.external_attr = attrs[path]
            archive.writestr(info, content)
    return stream.getvalue()


def _canonical_entries(package: ValidatedSkillPackage) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(package.canonical_zip)) as archive:
        return [(name, archive.read(name)) for name in archive.namelist()]


def test_directory_and_wrapped_zip_produce_the_same_canonical_package() -> None:
    validator = SkillPackageValidator(SkillParser())
    manifest = _skill_md(config="[{name: region, required: true}]")
    files = [
        ("weather/scripts/fetch.py", b"print('weather')"),
        ("weather/SKILL.md", manifest),
    ]

    from_directory = validator.validate_directory(files)
    from_zip = validator.validate_zip(
        _zip(
            [
                ("__MACOSX/._SKILL.md", b"metadata"),
                ("weather/SKILL.md", manifest),
                ("weather/.DS_Store", b"metadata"),
                ("weather/scripts/fetch.py", b"print('weather')"),
            ]
        )
    )

    assert from_directory == from_zip
    assert from_zip.name == "weather"
    assert from_zip.description == "Reports the weather."
    assert from_zip.files == (
        ("SKILL.md", manifest),
        ("scripts/fetch.py", b"print('weather')"),
    )
    assert _canonical_entries(from_zip) == list(from_zip.files)


def test_canonical_zip_is_stable_across_input_order_and_archive_metadata() -> None:
    validator = SkillPackageValidator(SkillParser())
    manifest = _skill_md()

    first = validator.validate_zip(
        _zip([("weather/z.txt", b"z"), ("weather/SKILL.md", manifest)])
    )
    second = validator.validate_zip(
        _zip([("weather/SKILL.md", manifest), ("weather/z.txt", b"z")])
    )

    assert first.canonical_zip == second.canonical_zip


@pytest.mark.parametrize(
    "config",
    ["{name: region}", "[region]", "not-a-list"],
)
def test_manifest_config_must_be_a_list_of_mappings(config: str) -> None:
    validator = SkillPackageValidator(SkillParser())

    with pytest.raises(SkillPackageInvalidError) as error:
        validator.validate_zip(_zip([("SKILL.md", _skill_md(config=config))]))

    assert error.value.reason == "invalid_metadata"


@pytest.mark.parametrize(
    "path",
    ["../SKILL.md", "/SKILL.md", "C:/SKILL.md", "dir\\SKILL.md"],
)
def test_zip_rejects_unsafe_relative_paths(path: str) -> None:
    validator = SkillPackageValidator(SkillParser())

    with pytest.raises(SkillPackageInvalidError) as error:
        validator.validate_zip(_zip([(path, _skill_md())]))

    assert error.value.reason == "unsafe_file_path"


@pytest.mark.parametrize(
    ("kind", "path"),
    [
        (0o120000, "SKILL.md"),
        (0o160000, "SKILL.md"),
        (0o060000, "SKILL.md"),
        (0o120000, "linked-directory/"),
    ],
)
def test_zip_rejects_links_and_special_files(kind: int, path: str) -> None:
    validator = SkillPackageValidator(SkillParser())

    with pytest.raises(SkillPackageInvalidError) as error:
        validator.validate_zip(
            _zip(
                [(path, _skill_md())],
                attrs={path: kind << 16},
            )
        )

    assert error.value.reason == "unsafe_file_path"


def test_root_and_matching_wrapper_are_normalized_to_the_same_file_tree() -> None:
    validator = SkillPackageValidator(SkillParser())
    manifest = _skill_md()

    root = validator.validate_zip(
        _zip([("SKILL.md", manifest), ("scripts/main.py", b"main")])
    )
    wrapped = validator.validate_zip(
        _zip(
            [
                ("weather/SKILL.md", manifest),
                ("weather/scripts/main.py", b"main"),
            ]
        )
    )

    assert root == wrapped


@pytest.mark.parametrize(
    ("metadata", "expected_description"),
    [
        (
            b"---\nname: weather\ndescription: |\n  first line\n  second line\n---\n",
            "first line\nsecond line",
        ),
        (
            b"---\nname: weather\ndescription: >\n  first line\n  second line\n---\n",
            "first line second line",
        ),
    ],
)
def test_multiline_description_preserves_yaml_semantics(
    metadata: bytes, expected_description: str
) -> None:
    package = SkillPackageValidator(SkillParser()).validate_zip(
        _zip([("SKILL.md", metadata)])
    )

    assert package.description == expected_description


@pytest.mark.parametrize(
    ("entries", "reason"),
    [
        ([], "missing_skill_file"),
        (
            [
                ("SKILL.md", b"name: one\ndescription: one\n"),
                ("a/SKILL.md", b"name: one\ndescription: two\n"),
            ],
            "multiple_skill_files",
        ),
        (
            [
                ("weather/SKILL.md", _skill_md()),
                ("outside.txt", b"outside"),
            ],
            "invalid_wrapper",
        ),
        (
            [
                ("SKILL.md", _skill_md()),
                ("a//b", b"x"),
                ("a/./b", b"y"),
            ],
            "duplicate_file_path",
        ),
    ],
)
def test_zip_requires_one_unambiguous_skill_tree(
    entries: list[tuple[str, bytes]], reason: str
) -> None:
    with pytest.raises(SkillPackageInvalidError) as error:
        SkillPackageValidator(SkillParser()).validate_zip(_zip(entries))

    assert error.value.reason == reason


def test_wrapper_must_match_the_manifest_name_and_contain_every_file() -> None:
    validator = SkillPackageValidator(SkillParser())

    with pytest.raises(SkillPackageInvalidError) as mismatch:
        validator.validate_zip(_zip([("wrong/SKILL.md", _skill_md(name="weather"))]))
    with pytest.raises(SkillPackageInvalidError) as conflict:
        validator.validate_zip(
            _zip(
                [
                    ("weather/SKILL.md", _skill_md()),
                    ("weather", b"not a directory"),
                ]
            )
        )

    assert mismatch.value.reason == "wrapper_name_mismatch"
    assert conflict.value.reason == "invalid_wrapper"


@pytest.mark.parametrize("name", ["skills-center", "skills-local", "skills-repo"])
def test_reserved_content_store_names_are_rejected(name: str) -> None:
    with pytest.raises(SkillPackageInvalidError) as error:
        SkillPackageValidator(SkillParser()).validate_zip(
            _zip([("SKILL.md", _skill_md(name=name))])
        )

    assert error.value.reason == "invalid_metadata"


def test_legacy_no_frontmatter_manifest_remains_accepted_for_local_compatibility() -> (
    None
):
    package = SkillPackageValidator(SkillParser()).validate_zip(
        _zip(
            [
                (
                    "SKILL.md",
                    b"name: weather\ndescription: Legacy upload compatibility\n",
                )
            ]
        )
    )

    assert package.name == "weather"
    assert package.description == "Legacy upload compatibility"


def test_unreadable_archive_entry_has_a_stable_reason(monkeypatch) -> None:
    class _UnreadableArchive:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def infolist(self):
            return [zipfile.ZipInfo("SKILL.md")]

        def read(self, _info):
            raise OSError("injected archive read failure")

    monkeypatch.setattr(
        package_module.zipfile,
        "ZipFile",
        lambda *_args, **_kwargs: _UnreadableArchive(),
    )

    with pytest.raises(SkillPackageInvalidError) as error:
        SkillPackageValidator(SkillParser()).validate_zip(b"archive")

    assert error.value.reason == "unreadable_archive"


def test_zip_enforces_file_count_path_and_size_limits(monkeypatch) -> None:
    validator = SkillPackageValidator(SkillParser())
    monkeypatch.setattr(package_module, "MAX_FILES", 1)
    with pytest.raises(SkillPackageTooLargeError):
        validator.validate_zip(_zip([("SKILL.md", _skill_md()), ("extra.txt", b"x")]))
    monkeypatch.setattr(package_module, "MAX_FILES", 500)
    monkeypatch.setattr(package_module, "MAX_PATH_LENGTH", 2)
    with pytest.raises(SkillPackageInvalidError):
        validator.validate_zip(_zip([("SKILL.md", _skill_md())]))
    monkeypatch.setattr(package_module, "MAX_PATH_LENGTH", 256)
    monkeypatch.setattr(package_module, "MAX_FILE_BYTES", 2)
    with pytest.raises(SkillPackageTooLargeError):
        validator.validate_zip(_zip([("SKILL.md", _skill_md())]))
    monkeypatch.setattr(package_module, "MAX_FILE_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(package_module, "MAX_EXPANDED_BYTES", 2)
    with pytest.raises(SkillPackageTooLargeError):
        validator.validate_zip(_zip([("SKILL.md", _skill_md())]))
    monkeypatch.setattr(package_module, "MAX_EXPANDED_BYTES", 50 * 1024 * 1024)
    monkeypatch.setattr(package_module, "MAX_COMPRESSED_BYTES", 1)
    with pytest.raises(SkillPackageTooLargeError):
        validator.validate_zip(_zip([("SKILL.md", _skill_md())]))


def test_directory_rejects_traversal_before_canonical_zip_is_created() -> None:
    with pytest.raises(SkillPackageInvalidError) as error:
        SkillPackageValidator(SkillParser()).validate_directory(
            [("weather/../outside/SKILL.md", _skill_md())]
        )

    assert error.value.reason == "unsafe_file_path"
