from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

from .. import pack_skill_core
from ..constants import (
    OSS_ACCESS_KEY_ID,
    OSS_ACCESS_KEY_SECRET,
    OSS_BUCKET_NAME,
    OSS_ENDPOINT,
    OSS_EVOLVE_RESULTS_PREFIX,
    OSS_PACK_ZIP_NAME,
    PACK_IMAGE_VERSION,
)


def publish_final_artifacts(
    *,
    plan: dict[str, Any],
    task_id: str,
    evolve_results_dir: Path,
) -> dict[str, Any]:
    """Pack current bot image and upload final self-evolve artifacts to OSS.

    OSS layout is defined by clawevolve_plan.constants and mirrors the
    repository-level upload_OSS.py behavior:
      oss://<OSS_BUCKET_NAME>/<OSS_EVOLVE_RESULTS_PREFIX>/<bot_id>/<task_id>/...
        uploads each file under the evolve results directory as individual OSS objects.
      oss://<OSS_BUCKET_NAME>/<OSS_EVOLVE_RESULTS_PREFIX>/<bot_id>/<task_id>_package/<OSS_PACK_ZIP_NAME>
        uploads the generated agent image zip as one OSS object.

    This function intentionally has no user-facing knobs: clawevolve-plan already owns
    bot_id/task_id/evolve_results_dir, and this is the mandatory finalization step.
    """
    safe_task_id = _validate_path_component(task_id, "task_id")
    bot_id_raw = str(plan.get("bot_id") or "").strip()
    if not bot_id_raw:
        raise ValueError("Planning Context missing bot_id; cannot build OSS artifact path")
    bot_id = _sanitize_bot_id(bot_id_raw)
    if not bot_id:
        raise ValueError(f"Planning Context bot_id is invalid for OSS path: {bot_id_raw!r}")

    src_dir = Path(evolve_results_dir).resolve()
    if not src_dir.is_dir():
        raise ValueError(f"evolve results dir not found: {src_dir}")

    result: dict[str, Any] = {
        "status": "running",
        "bot_id": bot_id,
        "raw_bot_id": bot_id_raw,
        "task_id": safe_task_id,
        "evolve_results_dir": str(src_dir),
        "bucket": OSS_BUCKET_NAME,
        "pack": {},
        "evolve_results": {},
    }

    with tempfile.TemporaryDirectory(prefix=f"clawevolve-plan-pack-{safe_task_id}-") as tmp:
        tmp_dir = Path(tmp)
        try:
            pack_zip = _run_pack_skill(
                evolve_results_dir=src_dir,
                bot_id=bot_id,
                out_dir=tmp_dir,
            )
            pack_key = f"{OSS_EVOLVE_RESULTS_PREFIX}/{bot_id}/{safe_task_id}_package/{OSS_PACK_ZIP_NAME}"
            result["pack"] = {
                "status": "packed",
                "local_zip": str(pack_zip),
                "source_zip_name": pack_zip.name,
                "oss_key": pack_key,
                "oss_path": _oss_uri(pack_key),
                "uploaded_name": OSS_PACK_ZIP_NAME,
            }
            _upload_zip(pack_zip, pack_key)
            result["pack"]["status"] = "ok"

            evolve_prefix = f"{OSS_EVOLVE_RESULTS_PREFIX}/{bot_id}/{safe_task_id}"
            result["evolve_results"] = {
                "status": "pending",
                "source_dir": str(src_dir),
                "oss_prefix": evolve_prefix.rstrip("/") + "/",
                "oss_path": _oss_uri(evolve_prefix).rstrip("/") + "/",
                "upload_mode": "directory_files",
            }
            _upload_dir(src_dir, evolve_prefix)
            result["evolve_results"]["status"] = "ok"
            result["status"] = "ok"
            return result
        except Exception as exc:
            result["status"] = "failed"
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["traceback"] = traceback.format_exc(limit=8)
            return result


def _run_pack_skill(*, evolve_results_dir: Path, bot_id: str, out_dir: Path) -> Path:
    pack_mod = _load_pack_module()
    out_dir.mkdir(parents=True, exist_ok=True)
    before = {p.resolve() for p in out_dir.glob("*.zip")}
    argv = [
        "--version",
        PACK_IMAGE_VERSION,
        "--evolve-results-dir",
        str(evolve_results_dir),
        "--bot-id",
        bot_id,
        "--out-dir",
        str(out_dir),
    ]

    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = pack_mod.main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        detail = str(exc) or stderr.getvalue() or stdout.getvalue()
        raise RuntimeError(_tail(f"pack-skill exited via SystemExit({exc.code}):\n{detail}\n{stderr.getvalue()}\n{stdout.getvalue()}"))
    except Exception as exc:  # noqa: BLE001 - boundary wrapper reports pack details.
        raise RuntimeError(_tail(f"pack-skill exception: {type(exc).__name__}: {exc}\n{stderr.getvalue()}\n{stdout.getvalue()}")) from exc

    if code != 0:
        raise RuntimeError(_tail(f"pack-skill failed with exit code {code}:\n{stderr.getvalue()}\n{stdout.getvalue()}"))

    created = [p for p in out_dir.glob("*.zip") if p.resolve() not in before]
    if len(created) != 1:
        all_zips = sorted(str(p) for p in out_dir.glob("*.zip"))
        raise RuntimeError(f"pack-skill expected exactly one new zip in {out_dir}, got {len(created)}; all_zips={all_zips}")
    return created[0].resolve()


def _upload_zip(zip_path: Path, oss_key: str) -> None:
    upload_mod = _load_upload_module()
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            upload_mod.upload_zip(str(zip_path), oss_key)
    except SystemExit as exc:
        raise RuntimeError(
            _tail(
                f"upload zip to OSS failed with SystemExit({exc.code}) for {_oss_uri(oss_key)}:\n"
                f"stdout={stdout.getvalue()}\nstderr={stderr.getvalue()}"
            )
        ) from exc
    except Exception as exc:  # noqa: BLE001 - report upload boundary details.
        raise RuntimeError(
            _tail(
                f"upload zip to OSS failed for {_oss_uri(oss_key)}: {type(exc).__name__}: {exc}\n"
                f"stdout={stdout.getvalue()}\nstderr={stderr.getvalue()}"
            )
        ) from exc


def _upload_dir(dir_path: Path, oss_key: str) -> None:
    upload_mod = _load_upload_module()
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            upload_mod.upload_dir(str(dir_path), oss_key)
    except SystemExit as exc:
        raise RuntimeError(
            _tail(
                f"upload dir to OSS failed with SystemExit({exc.code}) for {_oss_uri(oss_key)}:\n"
                f"stdout={stdout.getvalue()}\nstderr={stderr.getvalue()}"
            )
        ) from exc
    except Exception as exc:  # noqa: BLE001 - report upload boundary details.
        raise RuntimeError(
            _tail(
                f"upload dir to OSS failed for {_oss_uri(oss_key)}: {type(exc).__name__}: {exc}\n"
                f"stdout={stdout.getvalue()}\nstderr={stderr.getvalue()}"
            )
        ) from exc


def _load_pack_module():
    # Vendored from pack-skill/scripts/pack.py into clawevolve_plan.pack_skill_core,
    # so clawevolve-plan can invoke the pack core as a Python submodule instead
    # of shelling out to pack-skill/scripts/pack.sh.
    return pack_skill_core


def _load_upload_module():
    path = _repo_root() / "upload_OSS.py"
    if not path.is_file():
        raise FileNotFoundError(f"upload_OSS.py not found: {path}")
    import importlib.util

    name = "clawevolve_plan_upload_oss"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    old = sys.modules.get(name)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        if old is not None:
            sys.modules[name] = old
        else:
            sys.modules.pop(name, None)
        raise
    _configure_upload_module(mod)
    return mod


def _configure_upload_module(mod) -> None:
    # Reuse upload_OSS.py's implementation, but keep all runtime configuration
    # centralized in clawevolve_plan.constants.
    mod.AK = OSS_ACCESS_KEY_ID
    mod.SK = OSS_ACCESS_KEY_SECRET
    mod.ENDPOINT = OSS_ENDPOINT
    mod.BUCKET_NAME = OSS_BUCKET_NAME


def _repo_root() -> Path:
    # artifact_publish.py -> clawevolve_plan/ -> clawevolve-plan/ -> skills/
    return Path(__file__).resolve().parents[2]


def _sanitize_bot_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")[:120]


def _validate_path_component(value: str, name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"missing {name}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", raw) or ".." in raw:
        raise ValueError(f"invalid {name}: only letters, digits, underscore, dash and dot are allowed; '..' is forbidden")
    return raw


def _oss_uri(key: str) -> str:
    return f"oss://{OSS_BUCKET_NAME}/{key}"


def _tail(text: str, limit: int = 6000) -> str:
    text = str(text or "")
    return text[-limit:] if len(text) > limit else text


def write_publish_result(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
