
import json

import zipfile





import pytest





from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
)

from agentclaw.community.adapters.http.skill_center.skills import (
    _classify_upload_validation_error,
    _is_runtime_upload_error,
)

from agentclaw.community.core.skill_center.upload_error_codes import (
    SkillUploadErrorCode,
)

from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterHTTPStatusError,
)




from agentclaw.community.core.skill_center.errors import (
    LocalSkillInvalidPackageError,
    LocalSkillRuntimeUnavailableError,
    LocalSkillStorageError,
    LocalSkillTooLargeError,
)



from agentclaw.community.core.skill_center.skill_metadata import SkillManifestError


from tests.community.api.skill_center._skills_api_fixtures import mock_ctx as mock_ctx, _upload_skill_di_app
class TestUploadSkillValidation:
    def test_upload_rejects_non_active_bot(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="PENDING") as (
            client,
            mock_svc,
            _,
            _,
        ):
            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            assert response.status_code == 200
            body = response.json()
            assert body["success"] is False
            assert body["error_code"] == SkillUploadErrorCode.BOT_NOT_READY
            assert "expected ACTIVE" in body["message"]
            mock_svc.upload_skill.assert_not_called()

    def test_upload_rejects_missing_bot(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (
            client,
            mock_svc,
            mock_bot_repo,
            _,
        ):
            mock_bot_repo.get_by_id_and_owner.return_value = None

            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert body["success"] is False
            assert body["error_code"] == SkillUploadErrorCode.BOT_NOT_FOUND
            assert body["message"] == "Bot not found."
            mock_svc.upload_skill.assert_not_called()

    def test_upload_rejects_bot_without_owner_metadata(self, mock_ctx):
        """Local Skill 的归属无法确定时，不能将上传归到请求方。"""
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (
            client,
            mock_svc,
            mock_bot_repo,
            _,
        ):
            mock_bot_repo.get_by_id_and_owner.return_value["owner_id"] = ""

            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert body["success"] is False
            assert body["error_code"] == SkillUploadErrorCode.BOT_OWNER_MISSING
            assert body["message"] == "Bot ownership metadata is incomplete."
            mock_svc.upload_skill.assert_not_called()

    def test_upload_rejects_file_paths_length_mismatch(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (
            client,
            mock_svc,
            _,
            _,
        ):
            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    ),
                    ("files", ("a.txt", b"a", "text/plain")),
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert body["success"] is False
            assert (
                body["error_code"]
                == SkillUploadErrorCode.FILE_PATHS_COUNT_MISMATCH
            )
            assert body["message"] == "file_paths length must match files length."
            mock_svc.upload_skill.assert_not_called()

    def test_upload_rejects_invalid_file_paths_json(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (
            client,
            mock_svc,
            _,
            _,
        ):
            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": "not-json"},
            )

            body = response.json()
            assert body["success"] is False
            assert body["error_code"] == SkillUploadErrorCode.FILE_PATHS_INVALID
            assert body["message"] == "file_paths must be a JSON array."
            mock_svc.upload_skill.assert_not_called()

    def test_upload_active_bot_calls_service(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (
            client,
            mock_svc,
            _,
            _,
        ):
            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert body["success"] is True
            assert body["error_code"] is None
            assert mock_svc.upload_skill.await_count == 1

    def test_upload_persists_bot_owner_for_collaborator_upload(self):
        collaborator_ctx = RequestContext(
            user_id="collaborator-9",
            bot_id="service-bot-1",
        )
        with _upload_skill_di_app(
            collaborator_ctx,
            bot_status="ACTIVE",
            bot_type="service",
            bot_owner_id="bot-owner-1",
        ) as (client, mock_svc, _, _):
            response = client.post(
                ("/api/skills/upload?user_id=bot-owner-1&bot_id=service-bot-1"),
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            assert response.json()["success"] is True, response.json()
            mock_svc.upload_skill.assert_awaited_once()
            kwargs = mock_svc.upload_skill.await_args.kwargs
            assert kwargs["user_id"] == "bot-owner-1"
            assert "author_id" not in kwargs

    def test_upload_passes_bot_scope_to_shared_upload_service(self, mock_ctx):
        with _upload_skill_di_app(
            mock_ctx,
            bot_status="ACTIVE",
        ) as (client, mock_svc, _, mock_factory):
            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            assert response.json()["success"] is True
            call_kwargs = mock_svc.upload_local_skill_files.await_args.kwargs
            assert call_kwargs["bot_id"] == mock_ctx.bot_id
            assert call_kwargs["owner_id"] == mock_ctx.user_id
            assert call_kwargs["actor_id"] == mock_ctx.user_id
            mock_factory.create.assert_not_called()

    @pytest.mark.parametrize("engine_type", ["openclaw", "claude_code", "hermes"])
    def test_desktop_upload_preserves_engine_and_bot_scope(self, engine_type):
        desktop_ctx = RequestContext(
            user_id="desktop-owner",
            bot_id=f"desktop-{engine_type}",
        )
        with _upload_skill_di_app(
            desktop_ctx,
            bot_status="ACTIVE",
            bot_type="desktop",
            engine_type=engine_type,
        ) as (client, mock_svc, _, mock_factory):
            # Real persistence returns ``bolt_id`` for the owning Bot; it does
            # not populate the legacy response-only ``bot_id`` alias.
            mock_svc.upload_skill.return_value.pop("bot_id")

            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            assert response.status_code == 200, response.text
            assert response.json()["data"]["bot_id"] == desktop_ctx.bot_id
            call_kwargs = mock_svc.upload_local_skill_files.await_args.kwargs
            assert call_kwargs["bot_id"] == desktop_ctx.bot_id
            assert call_kwargs["owner_id"] == desktop_ctx.user_id
            mock_factory.create.assert_not_called()

    def test_upload_normalizes_runtime_unavailable_error_message(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (
            client,
            mock_svc,
            _,
            _,
        ):
            mock_svc.upload_skill.side_effect = ValueError(
                "Upload processing error: 502 Bad Gateway"
            )

            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert response.status_code == 200
            assert body["success"] is False
            assert body["error_code"] == SkillUploadErrorCode.RUNTIME_UNAVAILABLE
            assert (
                body["message"]
                == "当前 Bot 的运行环境暂不可用，请重新启动 Bot 后重试。"
            )

    def test_upload_normalizes_legacy_timeout_error_message(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (
            client,
            mock_svc,
            _,
            _,
        ):
            mock_svc.upload_skill.side_effect = ValueError(
                "Upload processing error: timeout"
            )

            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert response.status_code == 200
            assert body["success"] is False
            assert body["error_code"] == SkillUploadErrorCode.RUNTIME_UNAVAILABLE
            assert (
                body["message"]
                == "当前 Bot 的运行环境暂不可用，请重新启动 Bot 后重试。"
            )

    def test_upload_returns_manifest_error_code(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (
            client,
            mock_svc,
            _,
            _,
        ):
            mock_svc.upload_skill.side_effect = ValueError("SKILL.md is required.")

            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "README.md",
                            b"readme",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["README.md"])},
            )

            body = response.json()
            assert response.status_code == 200
            assert body["success"] is False
            assert body["error_code"] == SkillUploadErrorCode.MANIFEST_MISSING
            assert body["message"] == "SKILL.md is required."

    @pytest.mark.parametrize(
        ("error", "error_code", "message"),
        [
            (
                LocalSkillInvalidPackageError("missing_skill_file"),
                SkillUploadErrorCode.MANIFEST_MISSING,
                "SKILL.md is required.",
            ),
            (
                LocalSkillInvalidPackageError("invalid_zip"),
                SkillUploadErrorCode.ZIP_INVALID,
                "File is not a valid ZIP archive.",
            ),
            (
                LocalSkillTooLargeError(),
                SkillUploadErrorCode.PACKAGE_TOO_LARGE,
                "Skill package is too large.",
            ),
            (
                LocalSkillRuntimeUnavailableError(),
                SkillUploadErrorCode.RUNTIME_UNAVAILABLE,
                "当前 Bot 的运行环境暂不可用，请重新启动 Bot 后重试。",
            ),
        ],
    )
    def test_upload_maps_shared_service_domain_errors(
        self, mock_ctx, error, error_code, message
    ):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (
            client,
            mock_svc,
            _,
            _,
        ):
            mock_svc.upload_local_skill_files.side_effect = error

            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert body["success"] is False
            assert body["error_code"] == error_code
            assert body["message"] == message

    def test_upload_keeps_persistence_failure_distinct_from_runtime(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (
            client,
            mock_svc,
            _,
            _,
        ):
            error = LocalSkillStorageError()
            error.__cause__ = RuntimeError("database write failed")
            mock_svc.upload_local_skill_files.side_effect = error

            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert body["success"] is False
            assert body["error_code"] == SkillUploadErrorCode.UPLOAD_FAILED

    def test_upload_runtime_unavailable_logs_cause_not_unexpected(self, mock_ctx, caplog):
        # Regression for the opaque CDO "[skills.upload_skill] Unexpected
        # error:" (Dima 2026102900120185304): LocalSkillRuntimeUnavailableError
        # carries an empty str(e); the upstream device reason lives in
        # __cause__ and must surface in the log instead of the meaningless
        # empty envelope.
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (
            client,
            mock_svc,
            _,
            _,
        ):
            error = LocalSkillRuntimeUnavailableError()
            error.__cause__ = RuntimeError("teclaw apply failed")
            mock_svc.upload_local_skill_files.side_effect = error

            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            # Client-facing envelope is unchanged (degraded runtime message).
            assert body["success"] is False
            assert body["error_code"] == SkillUploadErrorCode.RUNTIME_UNAVAILABLE
            assert (
                body["message"]
                == "当前 Bot 的运行环境暂不可用，请重新启动 Bot 后重试。"
            )
            # The cause is surfaced, not swallowed into the empty envelope.
            assert "runtime/storage unavailable" in caplog.text
            assert "LocalSkillRuntimeUnavailableError()" in caplog.text
            assert "teclaw apply failed" in caplog.text
            assert "Unexpected error: " not in caplog.text

    def test_upload_bare_storage_failure_logs_cause_not_unexpected(
        self, mock_ctx, caplog
    ):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (
            client,
            mock_svc,
            _,
            _,
        ):
            error = LocalSkillStorageError()
            error.__cause__ = RuntimeError("database write failed")
            mock_svc.upload_local_skill_files.side_effect = error

            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            # Behavior preserved: bare storage failure stays UPLOAD_FAILED.
            assert body["success"] is False
            assert body["error_code"] == SkillUploadErrorCode.UPLOAD_FAILED
            assert "runtime/storage unavailable" in caplog.text
            assert "database write failed" in caplog.text
            assert "Unexpected error: " not in caplog.text

    def test_upload_generic_exception_logs_cause_in_unexpected_envelope(
        self, mock_ctx, caplog
    ):
        # Covers the generic catch-all path (skills_upload_routes.py
        # `except Exception`): a non-typed, non-runtime-pattern exception still
        # gets repr + __cause__ surfaced in the "Unexpected error:" log and an
        # UPLOAD_FAILED envelope, preserving prior client-facing behaviour.
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (
            client,
            mock_svc,
            _,
            _,
        ):
            error = RuntimeError("unhandled marker xyz")
            error.__cause__ = ValueError("ctx detail")
            mock_svc.upload_local_skill_files.side_effect = error

            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert body["success"] is False
            assert body["error_code"] == SkillUploadErrorCode.UPLOAD_FAILED
            # The generic envelope now logs repr + cause instead of {e} alone.
            assert "Unexpected error:" in caplog.text
            assert "RuntimeError('unhandled marker xyz')" in caplog.text
            assert "ctx detail" in caplog.text

    @pytest.mark.parametrize(
        ("message", "expected_code"),
        [
            ("No files uploaded", SkillUploadErrorCode.NO_FILES),
            ("SKILL.md is required.", SkillUploadErrorCode.MANIFEST_MISSING),
            (
                "Only one skill can be uploaded at a time. Found 2 SKILL.md files.",
                SkillUploadErrorCode.MANIFEST_MULTIPLE,
            ),
            (
                "Upload contains files outside the skill root directory.",
                SkillUploadErrorCode.FILE_OUTSIDE_ROOT,
            ),
            (
                "Invalid upload path: parent directory '..' is not allowed.",
                SkillUploadErrorCode.PATH_INVALID,
            ),
            (
                "SKILL.md must contain required field: name.",
                SkillUploadErrorCode.NAME_MISSING,
            ),
            (
                "SKILL.md field 'name' cannot be empty.",
                SkillUploadErrorCode.NAME_EMPTY,
            ),
            (
                "SKILL.md must contain required field: description.",
                SkillUploadErrorCode.DESCRIPTION_MISSING,
            ),
            (
                "Skill folder name must match SKILL.md field 'name'.",
                SkillUploadErrorCode.ROOT_NAME_MISMATCH,
            ),
            (
                "Skill name 'a_b' is invalid. Only English letters, numbers, and '-' are allowed",
                SkillUploadErrorCode.NAME_INVALID,
            ),
            (
                "Skill name cannot contain underscore '_'",
                SkillUploadErrorCode.NAME_INVALID,
            ),
            (
                "Skill name 'default' is reserved and cannot be used",
                SkillUploadErrorCode.NAME_RESERVED,
            ),
            ("File is not a zip file", SkillUploadErrorCode.ZIP_INVALID),
            ("Bad CRC-32 for file 'foo'", SkillUploadErrorCode.ZIP_INVALID),
            (
                "Bad offset for central directory",
                SkillUploadErrorCode.ZIP_INVALID,
            ),
            ("database write failed", SkillUploadErrorCode.UPLOAD_FAILED),
        ],
    )
    def test_upload_validation_messages_have_stable_codes(
        self, message, expected_code
    ):
        assert (
            _classify_upload_validation_error(message)
            or SkillUploadErrorCode.UPLOAD_FAILED
        ) == expected_code

    @pytest.mark.parametrize(
        ("manifest_code", "expected_code"),
        [
            ("INVALID_FRONTMATTER", SkillUploadErrorCode.MANIFEST_INVALID),
            (
                "INVALID_ENCODING",
                SkillUploadErrorCode.MANIFEST_ENCODING_INVALID,
            ),
            ("MISSING_NAME", SkillUploadErrorCode.NAME_MISSING),
            ("EMPTY_DESCRIPTION", SkillUploadErrorCode.DESCRIPTION_EMPTY),
            ("NAME_TOO_LONG", SkillUploadErrorCode.NAME_TOO_LONG),
            (
                "DESCRIPTION_TOO_LONG",
                SkillUploadErrorCode.DESCRIPTION_TOO_LONG,
            ),
        ],
    )
    def test_manifest_errors_use_their_stable_codes(
        self, manifest_code, expected_code
    ):
        error = SkillManifestError(manifest_code, "manifest error")
        assert _classify_upload_validation_error(error) == expected_code

    def test_runtime_classification_uses_exception_types_and_explicit_transport_text(self):
        assert _is_runtime_upload_error(TimeoutError("timed out"))
        assert _is_runtime_upload_error(
            ValueError("Upload processing error: 502 Bad Gateway")
        )
        assert _is_runtime_upload_error(DeviceAdapterHTTPStatusError(502, "bad gateway"))
        assert not _is_runtime_upload_error(
            DeviceAdapterHTTPStatusError(400, "bad request")
        )
        assert not _is_runtime_upload_error(
            "Skill folder name must match; Folder name: '503'"
        )
        assert not _is_runtime_upload_error(
            "SKILL.md frontmatter is invalid at line 502"
        )

    def test_wrapped_zip_exception_keeps_zip_error_code(self):
        cause = zipfile.BadZipFile("corrupt archive")
        error = ValueError("Upload processing error: malformed archive")
        error.__cause__ = cause
        assert (
            _classify_upload_validation_error(error)
            == SkillUploadErrorCode.ZIP_INVALID
        )

    def test_upload_preserves_manifest_error_before_timeout_classification(self, mock_ctx):
        with _upload_skill_di_app(mock_ctx, bot_status="ACTIVE") as (
            client,
            mock_svc,
            _,
            _,
        ):
            mock_svc.upload_skill.side_effect = SkillManifestError(
                "INVALID_FRONTMATTER",
                "SKILL.md frontmatter is invalid: timeout in quoted value",
            )

            response = client.post(
                "/api/skills/upload",
                files=[
                    (
                        "files",
                        (
                            "SKILL.md",
                            b"---\nname: a\ndescription: a\n---",
                            "text/markdown",
                        ),
                    )
                ],
                data={"file_paths": json.dumps(["SKILL.md"])},
            )

            body = response.json()
            assert response.status_code == 200
            assert body["success"] is False
            assert body["error_code"] == SkillUploadErrorCode.MANIFEST_INVALID
            assert body["message"] == (
                "SKILL.md frontmatter is invalid: timeout in quoted value"
            )

