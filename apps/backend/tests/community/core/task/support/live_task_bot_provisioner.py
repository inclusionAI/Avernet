from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx


class LiveTaskBotProvisioner:  # pragma: no cover — live task E2E provisioning helper (HTTP create bot / install skill); exercises a real local backend, not CI LOCAL line coverage
    """Live task E2E provisioning helper(建 bot + 装 skill),非框架运行时。

    framework 运行时(``task execution transport``)不感知 bot/skill 存在;本类复刻产品界面操作,
    供 e2e 跑前 reproducible 拉起环境(local stack restart clear 后一键重建 bot+skill)。仅本地集成用:
    ``task execution transport`` 收 bot_id 即开跑,本类负责把那个 bot_id 连同其 skill 准备出来。

    - ``create_bot`` → ``POST /api/bots`` + 轮询等 ACTIVE + set public=1 → 返 ``bot_id``
    - ``install_skills`` → 逐 skill 目录 upload → 建 skill set → 加技能(自动激活)→ 落 activate → 返 ``skill_set_id``
    """

    def __init__(
        self,
        *,
        backend_base_url: str,
        user_id: str,
        http_client: httpx.AsyncClient | None = None,
        default_engine_type: str = "openclaw",
        wait_active_timeout: float = 120.0,
        wait_active_interval: float = 1.5,
    ) -> None:
        self._backend = backend_base_url.rstrip("/")
        self._user_id = user_id
        self._default_engine = default_engine_type
        self._wait_active_timeout = wait_active_timeout
        self._wait_active_interval = wait_active_interval
        self._http = http_client or httpx.AsyncClient(timeout=60.0)

    async def _aclose(self) -> None:
        await self._http.aclose()

    def _hdrs(self) -> dict[str, str]:
        return {"x-user-id": self._user_id, "accept": "application/json"}

    # ===== create bot =====
    async def _list_my_bots(self) -> list[dict[str, Any]]:
        """``GET /api/bots/by-owner-or-collaborator`` 取本 user 的 bot 列表(LocalAuth ``x-user-id``)。"""
        r = await self._http.get(
            f"{self._backend}/api/bots/by-owner-or-collaborator",
            params={"user_id": self._user_id},
            headers=self._hdrs(),
        )
        if r.status_code != 200:
            return []
        return (r.json().get("data") or {}).get("items") or []

    async def _find_existing_bot(self, bot_name: str | None) -> dict[str, Any] | None:
        """按 bot_name 在本 user bot 列表里查已存在的 ACTIVE bot(幂等:已建则复用,不重复建)。

        反复跑集成用例时复用上次 provisioned 的 bot(同进程 session 内 bot 仍在内存);local 重启清空后判定为空→正常新建。
        """
        if not bot_name:
            return None
        return next(
            (it for it in await self._list_my_bots() if it.get("bot_name") == bot_name),
            None,
        )

    async def create_bot(
        self,
        *,
        bot_name: str | None = None,
        bot_desc: str | None = None,
        engine_type: str | None = None,
        bot_type: str = "personal",
        set_public: bool = True,
        wait_active: bool = True,
    ) -> str:
        """``POST /api/bots`` 建个人 bot(local mock passport 即时签发)→ 轮询等 ACTIVE → set public=1。返 ``bot_id``。

        local 本地走 LocalAuth(``x-user-id`` 无 ctoken);engine 默认 openclaw(对齐 local 主链路)。
        public=1 仅为 BCSFuse discover 可见,设置失败不阻断(本地直连 WS 不经 discover)。

        幂等:按 ``bot_name`` 先查已存在的 ACTIVE bot,复用其 id(确保 public),不重复建。
        """
        if bot_name:
            existing = await self._find_existing_bot(bot_name)
            if existing:
                bot_id = existing.get("bot_id")
                if (
                    str(existing.get("status") or "").upper() != "ACTIVE"
                    and wait_active
                ):
                    try:
                        await self.wait_active(bot_id)  # 残留/并发 PENDING bot 等就绪
                    except Exception:  # noqa: BLE001  等不到 ACTIVE 仍返回(下游 WS 自愈)
                        pass
                if set_public:
                    try:
                        await self.set_public(bot_id, True)
                    except Exception:  # noqa: BLE001  public 设置失败不阻断
                        pass
                return bot_id  # 复用已建 bot(任意状态)
        body: dict[str, Any] = {
            "bot_name": bot_name,
            "bot_desc": bot_desc,
            "engine_type": engine_type or self._default_engine,
            "bot_type": bot_type,
            "entity_id": self._user_id,
            "entity_type": "staff",
        }
        r = await self._http.post(
            f"{self._backend}/api/bots", headers=self._hdrs(), json=body
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            msg = str(data.get("message") or data)
            if "already exists" in msg.lower() and bot_name:
                # 竞态/残留同名 bot(PENDING):按名取回,等 ACTIVE 复用,不报错
                existing = await self._find_existing_bot(bot_name)
                if existing:
                    bot_id = existing.get("bot_id")
                    if wait_active:
                        try:
                            await self.wait_active(bot_id)
                        except Exception:  # noqa: BLE001
                            pass
                    if set_public:
                        try:
                            await self.set_public(bot_id, True)
                        except Exception:  # noqa: BLE001
                            pass
                    return bot_id
            raise RuntimeError(f"create_bot failed: {msg}")
        bot = (data.get("data") or {}).get("bot") or {}
        bot_id = bot.get("bot_id")
        if not bot_id:
            raise RuntimeError(f"create_bot: no bot_id in response: {data}")
        if wait_active:
            await self.wait_active(bot_id)
        if set_public:
            try:
                await self.set_public(bot_id, True)
            except Exception:  # noqa: BLE001  public 设置失败不阻断主流程
                pass
        return bot_id

    async def wait_active(self, bot_id: str) -> dict[str, Any]:
        """轮询 ``GET /api/bots/by-owner-or-collaborator`` 等 ``bot_id`` 状态 ACTIVE,返该 bot dict。"""
        deadline = time.monotonic() + self._wait_active_timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            r = await self._http.get(
                f"{self._backend}/api/bots/by-owner-or-collaborator",
                params={"user_id": self._user_id},
                headers=self._hdrs(),
            )
            if r.status_code == 200:
                items = (r.json().get("data") or {}).get("items") or []
                for it in items:
                    if it.get("bot_id") == bot_id:
                        last = it
                        if str(it.get("status") or "").upper() == "ACTIVE":
                            return it
            await asyncio.sleep(self._wait_active_interval)
        raise RuntimeError(
            f"bot {bot_id} not ACTIVE within {self._wait_active_timeout}s (last={last})"
        )

    async def set_public(self, bot_id: str, public: bool = True) -> dict[str, Any]:
        """``POST /api/bots/{bot_id}/public`` 设公开(供 BCSFuse discover 可见)。"""
        r = await self._http.post(
            f"{self._backend}/api/bots/{bot_id}/public",
            headers=self._hdrs(),
            json={"public": "1" if public else "0", "user_id": self._user_id},
        )
        r.raise_for_status()
        return r.json()

    async def onboard_to_bcn(
        self, bot_id: str, bot_desc: str | None = None
    ) -> dict[str, Any]:
        """``PUT /api/bots/{bot_id}`` 改 ``bot_desc`` → 触发 ``bot_service._sync_bot_to_bcn`` → ``BcnService.onboard_bot``
        → BCN ``POST /admin/bots/onboard`` 把 ``{bot_id}:{owner_id}`` 注册进协作网。

        **为何需要**:`create_bot` 对 ``openclaw+personal`` bot 主动 skip BCN provider 注册
        (``_should_register_bcn_provider`` 返回 False + DRM 默认关),provisioned bot 默认不在 BCN;
        ``form_coop_group`` 建群校验成员时 BCS 按 ``{bot_id}:{owner_id}`` 查会 404 ``bot_not_found``。
        本方法走 **update(上行 onboard)路**——不经 ``_should_register_bcn_provider``/DRM gate——把 bot 入网 BCN。
        **coop_group 成员 bot 建群前必须 onboard**(single_bot/BBS 用不到,调了也无害)。
        """
        r = await self._http.put(
            f"{self._backend}/api/bots/{bot_id}",
            headers=self._hdrs(),
            json={"bot_desc": bot_desc or "e2e fixture bot"},
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(
                f"onboard_to_bcn failed (PUT /api/bots/{bot_id}): {data.get('message') or data}"
            )
        return data

    async def set_bcs_visibility(
        self, bot_id: str, visibility: str = "public"
    ) -> dict[str, Any]:
        """``PUT /bots/{bot_uuid}/visibility`` 到 BCS(:21000) 设**单个 bot** 的 BCS visibility。

        bot_uuid = ``{bot_id}:{owner_id}``(BCN onboard 时的 bcn_bot_id 格式,owner=本 provisioner 的
        user_id)。local ``BCS_AUTH_MOCK=1``,无需真 token。

        为何需要:BCS 建群 ``ensure_reachable`` 对 ``visibility=="protected"`` 的成员 bot 做好友校验
        (403 "not friends");``visibility=="public"`` 直接放行(不查好友)。local bcs-config
        ``default_visibility="protected"`` 是公共配置不能动,故对**要进协作群的成员 bot** 单独 PUT 成
        ``public``,绕开好友校验、让真 ``form_coop_group`` 建群过(UI 可见真群)。只调你要的 bot,不改全局。
        """
        bcs_url = os.environ.get("BCS_API_BASE_URL", "http://127.0.0.1:21000").rstrip(
            "/"
        )
        bot_uuid = f"{bot_id}:{self._user_id}"
        r = await self._http.put(
            f"{bcs_url}/bots/{bot_uuid}/visibility",
            headers={"accept": "application/json"},
            json={"visibility": visibility},
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"set_bcs_visibility failed (PUT {bcs_url}/bots/{bot_uuid}/visibility "
                f"visibility={visibility}): {r.status_code} {r.text[:200]}"
            )
        return r.json() if r.text else {}

    async def set_bbs_task_dream_mode(
        self, bot_id: str, enabled: bool = True
    ) -> dict[str, Any]:
        """开启单个 bot 的 BCS ``task_dream_mode``(BBS 主动 bid roster 入选开关)。

        唯一 setter 是 principal-gated 的 BCS openapi ``PATCH /openapi/v1/collaboration/bots/{bot_id}``
        (``bcs-api-http`` openapi v1,经 ``GatewayPrincipalTokenVerifier`` 校验)。``task_dream_mode`` 的
        读写与 ``set_bcs_visibility`` 不同:visibility 走 bcs-http mock-auth 面,无 token;dream-mode 只在
        openapi v1 面,必须带 gateway principal token。

        local launcher 已设 ``AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE``(默认
        ``avernet-dev-signing-key-NOT-FOR-PROD``,见 ``scripts/modules/bcs.sh:884`` /
        ``scripts/modules/backend.sh:86``)。本方法自铸一个 gateway principal token:
        HS256 / iss=gateway / aud=bcs / kid=bare / principals=[user(subject.id=user_id)]。
        token claim shape 对齐 BCS ``wire.rs:GatewayUserPrincipal``(与 Avernet gateway_principal 共享
        gateway 签发契约)。user_id 即 bot 的 owner staff_no(创建时 ``entity_id=user_id``),经 BCS
        ``authorize_bot_management`` 的 owner 匹配(``caller_actor_id == created_by``)放行。

        bot_uuid=``{bot_id}:{user_id}``(同 ``set_bcs_visibility``)。PATCH 只传 task_dream_mode
        (BCS ``UpdateBotRequest`` 各字段 Option,仅更新传入项)。非 2xx 抛错带 status/body,
        便于定位 principal 形状 / owner 匹配问题(若 403 可调 principal subject.id)。
        """
        import time as _t

        import jwt  # PyJWT(Avernet gateway_principal verifier 同库;HS256)

        bcs_url = os.environ.get("BCS_API_BASE_URL", "http://127.0.0.1:21000").rstrip(
            "/"
        )
        key = os.environ.get(
            "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE",
            "avernet-dev-signing-key-NOT-FOR-PROD",
        )
        bot_uuid = f"{bot_id}:{self._user_id}"
        now = int(_t.time())
        claims: dict[str, Any] = {
            "iss": "gateway",
            "aud": "bcs",
            "iat": now,
            "exp": now + 300,
            "principals": [
                {
                    "type": "user",
                    "subject": {
                        "id": self._user_id,
                        "username": self._user_id,
                        "display_name": "task-e2e",
                        "full_name": None,
                        "tenant_id": "default",
                    },
                }
            ],
        }
        token = jwt.encode(claims, key, algorithm="HS256", headers={"kid": "bare"})
        r = await self._http.patch(
            f"{bcs_url}/openapi/v1/collaboration/bots/{bot_uuid}",
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            json={"task_dream_mode": enabled},
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"set_bbs_task_dream_mode failed (PATCH "
                f"{bcs_url}/openapi/v1/collaboration/bots/{bot_uuid} "
                f"task_dream_mode={enabled}): {r.status_code} {r.text[:300]}"
            )
        return r.json() if r.text else {}

    # ===== install skills =====
    async def install_skills(
        self,
        bot_id: str,
        skill_dirs: list[str],
        *,
        skill_set_name: str = "task-framework-skills",
        skill_set_desc: str | None = None,
        upload_mode: str = "create",
        entity_type: str = "staff",
    ) -> str:
        """装 skill:逐目录(含 ``SKILL.md``)upload → 建 skill set → 加技能(自动激活)→ 落 activate。返 ``skill_set_id``。

        **幂等**:先查该 bot 已装 skill 的 name 集合(``GET /api/skills?bot_id=...``),frontmatter.name 命中已装 →
        跳过 upload(不重复建同名 skill);skill set 同名复用(``_ensure_skill_set``);add/activate 均幂等。
        每个 ``skill_dir`` 形如 ``…/skills/planning/(SKILL.md)``;``SKILL.md`` frontmatter.name 即 skill 名。
        """
        import os as _os

        # 0) readiness guard:装 skill 前确保 bot 已创建完成(status=ACTIVE);PENDING→ACTIVE 约 15s
        await self.wait_active(bot_id)

        # 1) upload each skill dir (幂等:跳过已装同名 skill)
        installed = await self._installed_skill_names(bot_id)
        skill_ids: list[str] = []
        for sd in skill_dirs:
            skill_md_path = _os.path.join(sd, "SKILL.md")
            sname = self._read_skill_name(skill_md_path)
            if sname and sname in installed:
                continue  # 已装同名 skill,跳过 upload
            with open(skill_md_path, "rb") as fh:
                content = fh.read()
            files = {"files": ("SKILL.md", content, "text/markdown")}
            form = {"file_paths": json.dumps(["SKILL.md"])}
            r = await self._http.post(
                f"{self._backend}/api/skills/upload",
                params={
                    "user_id": self._user_id,
                    "bot_id": bot_id,
                    "upload_mode": upload_mode,
                },
                headers=self._hdrs(),
                files=files,
                data=form,
            )
            r.raise_for_status()
            body = r.json()
            sid = (body.get("data") or {}).get("id") if body.get("success") else None
            if not sid:
                raise RuntimeError(
                    f"upload skill {sd} failed: {body.get('message') or body}"
                )
            skill_ids.append(str(sid))

        # 2) ensure skill set (同名已存则复用其 id,避免重复建)
        skill_set_id = await self._ensure_skill_set(
            bot_id, skill_set_name, skill_set_desc
        )

        # 3) add skills to set (自动激活;重复添加返回 already-in-set,不视为失败)
        if skill_ids:
            r = await self._http.post(
                f"{self._backend}/api/skillsets/{skill_set_id}/skills",
                params={"user_id": self._user_id, "bot_id": bot_id},
                headers=self._hdrs(),
                json={
                    "skill_ids": skill_ids,
                    "user_id": self._user_id,
                    "bot_id": bot_id,
                },
            )
            r.raise_for_status()

        # 4) 落 activate (幂等:已激活返 already active)
        await self._activate_skill_set(skill_set_id, bot_id, entity_type)
        return skill_set_id

    async def get_active_skill_bots(self, bot_id: str) -> list[dict[str, Any]]:
        """``GET /api/skills/active/list`` 取 bot 当前激活 skill 列表(注:local community 模式下可能为空,
        因 active/list 扫 symlink 而 device_sync 为 no-op;以 ``skill_sets.json`` / 实际触达判定为准)。"""
        r = await self._http.get(
            f"{self._backend}/api/skills/active/list",
            params={"entity_id": self._user_id, "bot_id": bot_id},
            headers=self._hdrs(),
        )
        r.raise_for_status()
        return r.json().get("data") or []

    async def list_skill_sets(self, bot_id: str) -> list[dict[str, Any]]:
        """``GET /api/skillsets`` 取 bot 的能力集列表(is_active 标志即引擎加载态)。"""
        r = await self._http.get(
            f"{self._backend}/api/skillsets",
            params={"user_id": self._user_id, "bot_id": bot_id},
            headers=self._hdrs(),
        )
        r.raise_for_status()
        return r.json().get("data") or []

    async def _installed_skill_names(self, bot_id: str) -> set[str]:
        """``GET /api/skills?bot_id=...&user_id=...`` 取该 bot 已装 skill 的 name 集合(幂等跳过判定依据)。

        local community 模式 active/list 扫 symlink 可能为空;DB 查询 ``/api/skills`` 更可靠(查 skill 表该 bot 名下记录)。
        """
        names: set[str] = set()
        r = await self._http.get(
            f"{self._backend}/api/skills",
            params={
                "user_id": self._user_id,
                "bot_id": bot_id,
                "page": 1,
                "page_size": 200,
            },
            headers=self._hdrs(),
        )
        if r.status_code == 200:
            for sk in r.json().get("data") or []:
                n = sk.get("name")
                if n:
                    names.add(str(n))
        return names

    @staticmethod
    def _read_skill_name(skill_md_path: str) -> str | None:
        """从 ``SKILL.md`` frontmatter 解析 ``name:`` 字段(幂等跳过判定依据);无 frontmatter 返 None。"""
        import os as _os

        if not _os.path.exists(skill_md_path):
            return None
        with open(skill_md_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        if not text.startswith("---"):
            return None
        end = text.find("---", 3)
        if end < 0:
            return None
        for line in text[3:end].splitlines():
            ls = line.strip()
            if ls.startswith("name:"):
                return ls[len("name:") :].strip().strip('"').strip("'")
        return None

    async def _ensure_skill_set(self, bot_id: str, name: str, desc: str | None) -> str:
        """建 skill set;同名已存则复用其 id(幂等)。"""
        r = await self._http.get(
            f"{self._backend}/api/skillsets",
            params={"user_id": self._user_id, "bot_id": bot_id},
            headers=self._hdrs(),
        )
        if r.status_code == 200:
            for s in r.json().get("data") or []:
                if s.get("name") == name:
                    return str(s.get("id"))
        r = await self._http.post(
            f"{self._backend}/api/skillsets",
            params={"user_id": self._user_id, "bot_id": bot_id},
            headers=self._hdrs(),
            json={
                "name": name,
                "description": desc or "",
                "user_id": self._user_id,
                "bot_id": bot_id,
            },
        )
        r.raise_for_status()
        body = r.json()
        sid = (body.get("data") or {}).get("id") if body.get("success") else None
        if not sid:
            raise RuntimeError(f"create skillset failed: {body}")
        return str(sid)

    async def _activate_skill_set(
        self, skill_set_id: str, bot_id: str, entity_type: str
    ) -> dict[str, Any]:
        r = await self._http.post(
            f"{self._backend}/api/skills/skillset/activate",
            headers=self._hdrs(),
            json={
                "skill_set_id": skill_set_id,
                "entity_id": self._user_id,
                "entity_type": entity_type,
                "bot_id": bot_id,
            },
        )
        r.raise_for_status()
        return r.json()


