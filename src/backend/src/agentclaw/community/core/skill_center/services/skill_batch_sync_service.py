"""批量同步技能到 SkillCenter — 扫描 Git 仓库 skills/ 目录，打包上传并发布。

两阶段架构：
  Phase A — 全量发布（3 并发，2s 间隔）：打包 + 上传 OSS + 调 publish，不等状态
  Phase B — 批量轮询（5 并发）：3 轮梯度查询，统一收割结果
"""

import io
import logging
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from injector import inject

from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient

logger = logging.getLogger(__name__)

_OSS_BATCH_PREFIX = "skill-batch-sync"
_SIGN_URL_EXPIRES = 7200  # 2 hours — 两阶段模式整体耗时短，2小时足够
_PUBLISH_WORKERS = 3
_POLL_WORKERS = 5
_PUBLISH_DELAY_SECONDS = 2  # 每次 publish 请求间隔，避免触发限流

_POLL_WAIT_SECONDS = [0, 60, 180]  # 立即 / +1min / +3min

_DEDUP_RULES: dict[str, str] = {
    "cct-create-rule-workflow": "content-dsp",
    "release-banche": "ha-publish",
    "data-security-metrics": "datasec",
}

_TERMINAL_SUCCESS = {"PUBLISHED"}
_TERMINAL_FAIL = {"PUBLISH_FAILED", "SECURITY_REJECTED", "REJECTED",
                  "SECURITY_CHECK_FAILED", "STANDARD_CHECK_FAILED"}
_PENDING_STATES = {"EDITING", "PRE_RELEASING", "SECURITY_SCANNING", "REVIEWING"}


def _normalize_version(v: str) -> str:
    v = str(v) if v else ""
    return re.sub(r"^[vV]", "", v.strip())


@dataclass
class SyncResult:
    skill_code: str
    success: bool
    status: str = ""
    error: str = ""
    author_anomaly: bool = False
    skipped: bool = False


@dataclass
class BatchSyncReport:
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    results: list[SyncResult] = field(default_factory=list)
    trace_id: str = ""
    env: str = ""  # 标记来源 env，方便日志追溯


class SkillBatchSyncService:
    """扫描 skills/ 目录，批量打包上传并发布到 SkillCenter。

    两阶段架构：
      Phase A — 全量发布（不等状态）
      Phase B — 批量轮询（3 轮梯度收割）
    """

    @inject
    def __init__(
            self,
            skill_center_client: SkillCenterClient,
            oss: ObjectStoragePlugin,
            skill_repo: SkillRepository,
    ) -> None:
        self._sc_client = skill_center_client
        self._oss = oss
        self._skill_repo = skill_repo

    @staticmethod
    def get_default_skills_dir() -> Path:
        """获取本地 skills 仓库路径，优先使用 GitSync 本地目录，回退到 NAS bolt_shared 目录。"""
        from agentclaw.community.core.skill_center.services.git_sync import GitSyncConfig
        from agentclaw.community.core.workspace.path_factory import get_global_skills_repo_dir

        local_dir = GitSyncConfig().skills_target
        if local_dir.is_dir():
            return local_dir

        nas_dir = get_global_skills_repo_dir()
        if nas_dir.is_dir():
            logger.info("Local skills dir %s not found, falling back to NAS dir %s", local_dir, nas_dir)
            return nas_dir

        # 两个都不存在时返回 local_dir（后续 is_dir 检查会报清晰错误）
        return local_dir

    # ========================================================================
    # 主入口
    # ========================================================================

    def run(
            self,
            skills_dir: str | Path | None = None,
            skill_codes: list[str] | None = None,
            env: Optional[str] = None,
    ) -> BatchSyncReport:
        """执行批量同步：扫描 → 幂等检查 → 全量发布 → 批量轮询。

        Args:
            skills_dir: 技能仓库根目录，默认从 GitSyncConfig 获取
            skill_codes: 只同步指定的 skill（按目录名过滤），为 None 时同步全部
            env: 环境标识，用于从 DB 查询对应环境的 skill 记录（元数据覆盖）
        """
        skills_dir = Path(skills_dir) if skills_dir else self.get_default_skills_dir()
        if not skills_dir.is_dir():
            from agentclaw.community.core.skill_center.services.git_sync import GitSyncConfig
            from agentclaw.community.core.workspace.path_factory import get_global_skills_repo_dir
            raise FileNotFoundError(
                f"skills directory not found: {skills_dir} "
                f"(also tried local={GitSyncConfig().skills_target}, "
                f"nas={get_global_skills_repo_dir()})"
            )

        all_skill_dirs = self._scan_skills(skills_dir)
        # 基于全量扫描计算子目录排除集，filter 后仍能正确排除子目录文件
        skill_dirs_set = self._collect_child_exclusions(all_skill_dirs)

        if skill_codes:
            filter_set = set(skill_codes)
            all_skill_dirs = [sd for sd in all_skill_dirs if sd.name in filter_set]
            logger.info("Filtered to %d skills by skill_codes: %s", len(all_skill_dirs), skill_codes)
        logger.info("Scanned %d skills (after dedup) from %s", len(all_skill_dirs), skills_dir)

        report = BatchSyncReport(total=len(all_skill_dirs))

        # 查询 DB 获取 env 对应的 skill 记录
        db_skill_map: dict[str, dict] = {}
        effective_env = env
        if effective_env:
            try:
                db_skills = self._skill_repo.list_skills(bolt_id=None, env=effective_env)
                db_skill_map = {s.get("name") or s.get("link_name"): s for s in db_skills if s.get("name") or s.get("link_name")}
                logger.info("[batch_sync][env=%s] loaded %d skills from DB", effective_env, len(db_skill_map))
            except Exception as exc:
                logger.warning("[batch_sync][env=%s] failed to load DB skills: %s", effective_env, exc)

        # 1) 解析元数据
        skill_metas: list[dict] = []
        for sd in all_skill_dirs:
            parsed = SkillParser.parse(sd)
            if parsed is None:
                report.results.append(SyncResult(skill_code=sd.name, success=False, error="parse failed"))
                report.failed += 1
                continue
            skill_code = sd.name
            db_meta = db_skill_map.get(skill_code, {})
            meta = self._build_metadata(parsed, sd, skills_dir, db_meta=db_meta)
            meta["_skill_path"] = sd
            meta["_child_dirs"] = skill_dirs_set.get(sd, set())
            skill_metas.append(meta)

        # 2) 幂等检查：跳过已 PUBLISHED 且版本一致的
        to_publish, skipped = self._idempotency_check(skill_metas)
        for meta in skipped:
            report.results.append(SyncResult(
                skill_code=meta["skillCode"], success=True,
                status="PUBLISHED", skipped=True,
            ))
            report.skipped += 1
        logger.info("Idempotency check: %d to publish, %d skipped", len(to_publish), len(skipped))

        if not to_publish:
            return report

        # 3) Phase A：全量打包 + 上传 + 发布（不等状态）
        published_codes: list[tuple[str, bool]] = []  # (skill_code, author_anomaly)
        with ThreadPoolExecutor(max_workers=_PUBLISH_WORKERS) as pool:
            futures = {
                pool.submit(self._publish_one, meta): meta
                for meta in to_publish
            }
            for future in as_completed(futures):
                meta = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = SyncResult(skill_code=meta["skillCode"], success=False, error=str(exc))

                if result.success:
                    published_codes.append((result.skill_code, result.author_anomaly))
                else:
                    report.results.append(result)
                    report.failed += 1

        logger.info("Phase A done: %d published, %d failed at publish stage",
                    len(published_codes), report.failed)

        # 4) Phase B：批量轮询
        if published_codes:
            poll_results = self._batch_poll(published_codes)
            for r in poll_results:
                report.results.append(r)
                if r.success:
                    report.success += 1
                else:
                    report.failed += 1

        logger.info(
            "[batch_sync][env=%s] run() completed: total=%d, success=%d, failed=%d, skipped=%d",
            effective_env or "current",
            report.total,
            report.success,
            report.failed,
            report.skipped,
        )
        return report

    # ========================================================================
    # 扫描 & 去重
    # ========================================================================

    def _scan_skills(self, skills_dir: Path) -> list[Path]:
        """扫描所有含 SKILL.md 的目录，按目录名去重。"""
        candidates: dict[str, Path] = {}
        for path in sorted(skills_dir.rglob("SKILL.md")):
            skill_path = path.parent
            code = skill_path.name
            if code in candidates:
                winner = self._resolve_dedup(code, candidates[code], skill_path)
                candidates[code] = winner
            else:
                candidates[code] = skill_path
        return list(candidates.values())

    @staticmethod
    def _resolve_dedup(code: str, existing: Path, challenger: Path) -> Path:
        preferred_parent = _DEDUP_RULES.get(code)
        if preferred_parent:
            if preferred_parent in challenger.parts:
                return challenger
            return existing
        parsed_e = SkillParser.parse(existing)
        parsed_c = SkillParser.parse(challenger)
        desc_e = len((parsed_e or {}).get("description", ""))
        desc_c = len((parsed_c or {}).get("description", ""))
        if desc_c > desc_e:
            return challenger
        return existing

    @staticmethod
    def _collect_child_exclusions(skill_dirs: list[Path]) -> dict[Path, set[Path]]:
        """对每个 skill 目录，找出其下含 SKILL.md 的子目录（打包时需排除）。"""
        all_paths = set(skill_dirs)
        exclusions: dict[Path, set[Path]] = {}
        for sd in skill_dirs:
            children = {
                other for other in all_paths
                if other != sd and sd in other.parents
            }
            exclusions[sd] = children
        return exclusions

    # ========================================================================
    # 幂等检查
    # ========================================================================

    def _idempotency_check(
            self, metas: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """批量查询 SkillCenter 状态，跳过已 PUBLISHED 且版本一致的 skill。"""
        to_publish: list[dict] = []
        skipped: list[dict] = []

        def _check_one(meta: dict) -> tuple[dict, bool]:
            try:
                resp = self._sc_client.query_publish_status(meta["skillCode"])
                data = resp.get("data") or {}
                status = data.get("status") or data.get("publishStatus") or ""
                remote_version = data.get("versionNumber") or data.get("version") or ""
                if status in _TERMINAL_SUCCESS and _normalize_version(remote_version) == _normalize_version(
                        meta["versionNumber"]):
                    return meta, True  # skip
            except Exception:
                pass
            return meta, False

        with ThreadPoolExecutor(max_workers=_POLL_WORKERS) as pool:
            futures = [pool.submit(_check_one, m) for m in metas]
            for future in as_completed(futures):
                meta, should_skip = future.result()
                if should_skip:
                    skipped.append(meta)
                else:
                    to_publish.append(meta)

        return to_publish, skipped

    # ========================================================================
    # Phase A：单个 skill 打包 + 上传 + 发布（不等状态）
    # ========================================================================

    def _publish_one(self, meta: dict) -> SyncResult:
        """打包 → 上传 OSS → 调 publish 接口。不轮询状态。"""
        skill_code = meta["skillCode"]
        logger.info(
            "[_publish_one] skill=%s, version=%s, env=%s, author_anomaly=%s",
            skill_code,
            meta.get("versionNumber", ""),
            meta.get("env", ""),
            meta.get("_author_anomaly", False),
        )
        skill_path: Path = meta["_skill_path"]
        child_dirs: set[Path] = meta.get("_child_dirs", set())
        author_anomaly = meta.get("_author_anomaly", False)

        try:
            package_url = self._pack_and_upload(
                skill_path, skill_code, meta.get("versionNumber", "1.0.0"),
                exclude_dirs=child_dirs,
            )
        except Exception as exc:
            return SyncResult(skill_code=skill_code, success=False, error=str(exc))

        payload = {k: v for k, v in meta.items() if not k.startswith("_")}
        payload["packageUrl"] = package_url
        logger.info("Publishing %s, payload: %s", skill_code, payload)

        # 请求间隔 2s，避免触发 SkillCenter 限流
        time.sleep(_PUBLISH_DELAY_SECONDS)

        try:
            resp = self._sc_client.upload_and_publish(payload)
        except Exception as exc:
            return SyncResult(skill_code=skill_code, success=False, error=str(exc))

        if not resp.get("success"):
            msg = resp.get("message") or resp.get("errorMsg") or resp.get("errorMessage")
            if not msg and isinstance(resp, dict):
                # 嵌套结构：resp.data.message / resp.data.errorMsg
                data = resp.get("data") or {}
                if isinstance(data, dict):
                    msg = data.get("message") or data.get("errorMsg")
            if not msg:
                msg = str(resp.get("error") or resp.get("errorMessage") or resp)
            return SyncResult(skill_code=skill_code, success=False, error=str(msg))

        # 发布请求成功受理，返回 success=True 让主流程把它放进轮询队列
        return SyncResult(skill_code=skill_code, success=True, author_anomaly=author_anomaly)

    def _pack_and_upload(
            self, skill_path: Path, skill_code: str, version: str,
            exclude_dirs: set[Path] | None = None,
    ) -> str:
        """打包 zip（排除含 SKILL.md 的子目录）→ 上传 OSS → 返回 signed URL。"""
        exclude_dirs = exclude_dirs or set()
        ts = str(int(time.time()))
        oss_path = f"{_OSS_BATCH_PREFIX}/{skill_code}/{ts}/{version}.zip"

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(skill_path.rglob("*")):
                if not f.is_file():
                    continue
                if any(f.is_relative_to(exc) for exc in exclude_dirs):
                    continue
                zf.write(f, f.relative_to(skill_path))

        zip_bytes = buf.getvalue()
        self._oss.put_object(oss_path, zip_bytes)
        signed_url = self._oss.sign_url(oss_path, _SIGN_URL_EXPIRES)

        logger.info("Packed %s: %s (%d bytes)", skill_code, oss_path, len(zip_bytes))
        return signed_url

    # ========================================================================
    # Phase B：批量轮询
    # ========================================================================

    def _batch_poll(self, codes: list[tuple[str, bool]]) -> list[SyncResult]:
        """3 轮梯度批量轮询，收割所有 skill 的最终状态。"""
        pending: dict[str, bool] = {
            code: anomaly
            for code, anomaly in codes
        }
        results: list[SyncResult] = []
        prev_settled = 0

        for round_idx, wait_sec in enumerate(_POLL_WAIT_SECONDS):
            if not pending:
                break

            if wait_sec > 0:
                logger.info("Poll round %d: waiting %ds before querying %d skills",
                            round_idx + 1, wait_sec, len(pending))
                time.sleep(wait_sec)

            round_results = self._poll_round(pending)

            for skill_code, (status, error) in round_results.items():
                anomaly = pending[skill_code]
                if status in _TERMINAL_SUCCESS:
                    results.append(SyncResult(
                        skill_code=skill_code, success=True,
                        status=status, author_anomaly=anomaly,
                    ))
                    del pending[skill_code]
                elif status in _TERMINAL_FAIL or status not in _PENDING_STATES:
                    results.append(SyncResult(
                        skill_code=skill_code, success=False,
                        status=status, error=error or f"unexpected status: {status}",
                        author_anomaly=anomaly,
                    ))
                    del pending[skill_code]
                # else: still pending, keep in set

            settled_this_round = len(codes) - len(pending) - prev_settled
            prev_settled = len(codes) - len(pending)
            logger.info("Poll round %d done: %d settled this round, %d total settled, %d still pending",
                        round_idx + 1, settled_this_round, prev_settled, len(pending))

        # 超时：第 3 轮后仍在 pending 的全部记为超时
        for skill_code, anomaly in pending.items():
            results.append(SyncResult(
                skill_code=skill_code, success=False,
                status="TIMEOUT", error="still pending after 3 poll rounds",
                author_anomaly=anomaly,
            ))

        return results

    def _poll_round(self, pending: dict[str, bool]) -> dict[str, tuple[str, str]]:
        """单轮并发查询所有 pending skill 的状态。"""
        out: dict[str, tuple[str, str]] = {}

        def _query(skill_code: str) -> tuple[str, str, str]:
            try:
                resp = self._sc_client.query_publish_status(skill_code)
                data = resp.get("data") or {}
                status = data.get("status") or data.get("publishStatus") or "UNKNOWN"
                logger.info("[_poll_round] skill=%s, status=%s, raw=%s", skill_code, status, data)
                return skill_code, status, ""
            except Exception as exc:
                return skill_code, "ERROR", str(exc)

        with ThreadPoolExecutor(max_workers=_POLL_WORKERS) as pool:
            futures = [pool.submit(_query, code) for code in pending]
            for future in as_completed(futures):
                code, status, error = future.result()
                out[code] = (status, error)

        return out

    # ========================================================================
    # 元数据构建
    # ========================================================================

    @staticmethod
    def _build_metadata(
        parsed: dict,
        skill_path: Path,
        skills_root: Path,
        db_meta: Optional[dict] = None,
    ) -> dict:
        skill_code = skill_path.name

        author = str(parsed.get("author") or "").strip()
        author_anomaly = not bool(author) or not author.isdigit()
        creator_nick = author or ""
        creator_work_no = author if author and author.isdigit() else ""

        raw_ver = str(db_meta.get("version") or parsed.get("version") or "1") if db_meta else str(parsed.get("version") or "1")
        version = raw_ver if "." in raw_ver else f"{raw_ver}.0.0"
        name = str(parsed.get("name") or skill_code)
        description = str(parsed.get("description") or "")
        category = str(parsed.get("category") or "general")

        skill_uuid = db_meta.get("skill_uuid") if db_meta else None

        meta: dict = {
            "skillCode": skill_code,
            "skillName": name,
            "description": description,
            "versionNumber": version,
            "visibility": "INTERNAL",
            "_author_anomaly": author_anomaly,
        }
        if category and category != "general":
            meta["category"] = category
        if creator_work_no:
            meta["creatorWorkNo"] = creator_work_no
            meta["creatorNickName"] = creator_nick
        if skill_uuid:
            meta["skillUuid"] = skill_uuid

        logger.info(
            "[_build_metadata] skill=%s, env=%s, version=%s (db=%s, git=%s), uuid=%s",
            skill_path.name,
            (db_meta or {}).get("env", ""),
            version,
            (db_meta or {}).get("version", ""),
            parsed.get("version", ""),
            skill_uuid,
        )
        return meta


def generate_report(report: BatchSyncReport, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    lines = [
        f"# Skill Batch Sync Report",
        f"",
        f"- Trace ID: {report.trace_id}",
        f"- Total: {report.total}",
        f"- Skipped (already published, same version): {report.skipped}",
        f"- Attempted this run: {report.total - report.skipped}",
        f"- Success: {report.success}",
        f"- Failed: {report.failed}",
        f"",
    ]

    failed = [r for r in report.results if not r.success]
    if failed:
        lines.append("## Failed Skills")
        lines.append("")
        lines.append("| skillCode | status | error |")
        lines.append("|-----------|--------|-------|")
        for r in failed:
            lines.append(f"| {r.skill_code} | {r.status} | {r.error} |")
        lines.append("")

    anomalies = [r for r in report.results if r.author_anomaly and not r.skipped]
    if anomalies:
        lines.append("## Author Anomalies (missing or non-workno format)")
        lines.append("")
        for r in anomalies:
            lines.append(f"- {r.skill_code}")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Report written to %s", output_path)
    return output_path
