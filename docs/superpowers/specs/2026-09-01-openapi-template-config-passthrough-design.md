# OpenAPI 创建/查询面的 template_config 快照透传设计

- 日期:2026-09-01
- 状态:已评审拍板(用户确认 v3 契约)
- 关联:`docs/bot-create-api-guide.zh-CN.md`(接入指南,随本设计同步改写 §1.1/§1.3/§1.4)、`src/backend/specs/2026-08-24-engine-properties-polymorphic-create/design.md`(上一轮多态创建设计)

## 1. 背景与问题

AC 侧模板工厂的模板已通过 `/openapi/v1/bot-templates/available-tc-list` 暴露,返回项即 OCB create payload 形态(`engine_type/bot_type/template_type/template_config`),`template_config` 是 resolved 快照(含 `template_key/template_uid/template_version_id/image/resource_spec/envs/capabilities` 等)。模板工厂的 `template_type` 是开放值域:官方模板(`normalCC`/`architect`)+ 用户自建任意值,后端已删除按 `template_id` 枚举识别的常量(`TEMPLATE_CONFIG_CONSUMING_TYPES`),工厂 bot 统一靠 `template_config` 的工厂标记键识别。

现状 `POST /openapi/v1/bots` 的 `engine_properties` 只收一个键 `template`,固定等价 `template_type="applicationCoding"`(strategy 在 `prepare_create` 里写死),`PUBLIC` 校验拒 `template_uid` 等标记字段——工厂模板在这条面**没有创建入口**;查询面上 `template_config` 是 6 键 allowlist 投影,工厂快照的 image/resource_spec/envs 等一律不回,前端也无法读回。

## 2. 目标 / 非目标

**目标**

1. 前端把 available-tc-list 的 item 逐字段照抄即可创建 bot(零映射、零二次 resolve 调用)。
2. 我们原样落库快照,查询时透传回读(存什么回什么),运行时消费走既有工厂快照链路,后端不新增 AC resolve 依赖。
3. 键名与老 API、available-tc-list item、DB 列、内部 `PreparedBotCreate` 对齐:`engine_properties.template → template_config`,并新增并列键 `template_type`。
4. 普通 bot 与 legacy applicationCoding 手填路径行为零变化。

**非目标**

- 不改老 `POST /api/bots`(TC 存量链路不动)。
- 不做后端 resolve/校验 `image` 合法性(透传即信任调用方,风险见 §9)。
- 不做 `template_type` 值域校验(开放值域,透传)。
- 不迁移既有 bot 的存量快照(老公的 allowlist 投影行为不变)。

## 3. 契约(v3,终稿)

### 3.1 创建 `POST /openapi/v1/bots?user_id={uid}`

```json
{
  "bot_name": "my-bot",
  "bot_desc": "...",
  "engine": "claude_code",
  "cluster_name": "ACRA",
  "bot_type": "personal",
  "engine_properties": {
    "template_type": "applicationCoding",
    "template_config": {
      "template_key": "applicationCoding",
      "template_version": "V1",
      "template_version_id": 2800006,
      "template_uid": "aicoding_bot_template",
      "template_name": "应用 Bot",
      "image": "reg.antgroup-inc.cn/...",
      "resource_spec": { "cpu": "4", "memory": "8g", "disk": "50" },
      "envs": { "...": "..." },
      "capabilities": { "...": false },
      "bot_template_config": { "...": {} }
    }
  }
}
```

Front-end 映射:`engine ← item.engine_type`,`bot_type ← item.bot_type`,`engine_properties.template_type ← item.template_type`,`engine_properties.template_config ← item.template_config` 整段 + 动态表单值追加为 `custom_field_values`。

### 3.2 查询面

`GET /openapi/v1/bots`、`GET /openapi/v1/bots/{bot_id}`、`GET /openapi/v1/bots/all` 一致:工厂模板 bot 的 `template_config` = 落库快照减敏感键(§6),`template_type` = 透传值。legacy applicationCoding bot 的回显字段保持现状(allowlist 投影)。

## 4. 行为分支(创建)

`engine_properties` 键域:`{template_type, template_config}`(都可选,`extra="forbid"`)。

| 形态 | 判定 | template_type | template_config 落库 | gates |
|---|---|---|---|---|
| 普通 bot | `engine_properties` 为空 | — | — | 既有组合约束 |
| 手填 applicationCoding(legacy 路径) | 有 `template_config`,无工厂标记,无(或不冲突的)`template_type` | 写死 `applicationCoding` | 按现有 `_validate_application_coding_config` 校验类型 | 既有 5 条(cloud/claude_code/personal bot_type/personal space)|
| 工厂快照透传(新) | `template_config` 有工厂标记键 | 调用方透传值(校验非空) | 原样 deepcopy + 密钥加密(§7) | 同 5 条组合 gates(见 §5 冲突规则) |

判定用现有 `is_template_factory_config`(`capabilities.py:18`,键 `template_key/template_uid/template_version_id/template_version` 任一命中)。

## 5. 校验规则(键级,错误码)

| # | 规则 | 错误 |
|---|---|---|
| 1 | `engine_properties` 出现 `template_type/template_config` 以外的键 | 422 `unsupported engine_properties fields: [...]`(沿用既有文案,键名集合更新) |
| 2 | 工厂形态:`template_type` 缺失或空串 | 422 `engine_properties.template_type is required for template-config creates` |
| 3 | 任何形态:`template_config` 缺失或空 | 422(沿用 `applicationCoding template_config must not be empty` 语义,文案去掉 applicationCoding 字样) |
| 4 | 工厂标记与手填专用键(`devflow_workflow/code_repos/yuque_kb_repos` 等外层契约键)混传 | 422 工厂路径不解读手填键,明确拒绝 |
| 5 | 任何形态的 `template_config` 顶层出现拒收 server-managed 字段:`bot_id/workspace_id/workspace_status/workspace_state/start_status/engine_form` | 422 `template_config contains server-managed fields: [...]`(现有错误类型复用,放行清单新增工厂四键) |
| 6 | 手填路径 `template_type` 传了且 ≠ `applicationCoding` | 422(防形态冒充;工厂值必须走工厂标记) |
| 7 | 组合 gates 违反(cloud-only、engine 非 claude_code、bot_type 非 personal、非个人空间) | 409(沿用 `BotCombinationUnsupportedError` 既有文案与顺序) |
| 8 | 已知外层键类型不符 | 422(仅手填路径,沿用 `_validate_application_coding_config` 检查;工厂路径**不做**键级类型校验,透传) |

`support_engines` 不做校验(透传信任;顶层 `engine` 的真实引擎/cluster 匹配校验既有逻辑不变)。

## 6. 查询投影(bots/{id}/all 三面)

现状三面共用 `project_template_config_for_public`(`template_public_view.py`,allowlist 6 键)。设计:**dispatch,不改 allowlist 语义**——

```python
def project_template_config_for_public(config):
    if is_factory_snapshot(config):
        return project_factory_snapshot(config)   # 新函数
    return ... # 现有 allowlist 逻辑原样保留
```

- 判定函数与创建面同一套工厂标记键,保证"存取路径一致"。
- `project_factory_snapshot`:deepcopy 后剔除敏感位置(§7),其余键原样回,包括嵌套 `bot_template_config`(减 thetaKey)、`envs/capabilities/image/resource_spec/template_name/custom_field_values`。
- 分派在一个函数内完成,`_to_bot`(router.py:193)与 `_attach_page_templates`(bot_inventory_service.py:167)两处调用点零改动;`/all` 的 attach 条件(`template_type` 非空才拉快照,bot_inventory_service.py:178)对工厂 bot 天然成立(透传值非空)。
- 投影后空 dict → 返回 None,与现有行为一致。
- **有意变更(非回归)**:老 TC 链路已落库的工厂 bot(快照带工厂标记,如 `template_key="applicationCoding"` 的 aicoding 模板)在三个查询面会从 6 键 allowlist 切到透传投影——这正是本设计要的效果;无工厂标记的老公行为不变。

## 7. 密钥处理(不透传的底线)

落库加密(工厂路径与 legacy 同一套):

- 顶层 `token`(str 非空,出现即校验)→ 复用现有加密(`enc:v1:`)。
- `bot_template_config.ext_config.thetaKey`(strategy.py `_THETA_KEY_PATH` 已有路径常量)→ 复用现有加密。

查询剔除(工厂投影专用,新常量):

- 顶层 `token` → 剔除。
- `bot_template_config.ext_config.thetaKey` → 原路径剔除(嵌套其余键保留)。

不放宽现有"密文也不回显"的安全结论(template_public_view.py 文件头注释是 security 评审产物,新投影路径服从同一结论)。

## 8. 改动文件清单

| 位置 | 改动 |
|---|---|
| `adapters/http/openapi_v1/bots/schemas.py` | `BotCreateEngineProperties`:`template` → `template_config` + 新增 `template_type: str \| None`;描述文案泛化 |
| `core/bot_management/engines/aicoding/strategy.py` | `prepare_create`:键域更新、新增工厂分支(§4)、放行工厂四键进 `to_internal_template_config` 的 PUBLIC 模式(§5.5)、错误文案 |
| `core/bot_management/engines/provisioning.py` | 预计零改动(工厂四键本就不在 `TEMPLATE_SERVER_RESERVED_FIELDS`),实现时核对确认 |
| `core/bot_management/template_public_view.py` | 新增 `project_factory_snapshot` + dispatch(§6) |
| `adapters/http/openapi_v1/bots/router.py` | 请求→strategy 的传参适配;`_to_bot` 无改动(dispatch 内完成) |
| `src/gateway/configs/schemas/bots.openapi.json` | `BotCreateEngineProperties` schema 手工精准 patch(禁止全量 regen;含 description/example) |
| `src/gateway/tests/fixtures/bots.openapi.json` | 同步 patch(fixture 与 configs 一致) |
| ocb 仓库 `src/gateway/configs/schemas/bots.openapi.json` + fixtures | 双写同步(既有约定) |
| `tests/community/adapters/http/openapi_v1/test_bots_endpoints.py` | 创建/查询用例(§10) |
| `tests/community/core/bot_management/test_application_coding_create.py` | strategy 单测(§10);全部 `template` 键断言迁移 |
| `tests/community/core/bot_inventory/services/test_bot_inventory_service.py` | /all attach 投影用例 |
| `docs/bot-create-api-guide.zh-CN.md` | §1.1/§1.3/§1.4/§3/§4 改写为 v3 契约 |

## 9. 风险与 trade-off(拍板记录)

- **信任调用方**:透传等于接受调用方指定的 `image/resource_spec/envs`(可指任意镜像/规格)。内部租户 + 受控调用方场景接受;若要收紧,只需在创建工厂分支加一条"`image` 必须等于该模板 key 已 resolve 版本的镜像"校验(留口,本期不做,记为后续收紧点)。
- **`template_type` 冒充**:调用方传 `template_type=applicationCoding` + 无工厂标记 = legacy 路径,行为等同现状(gates 最严);传带工厂标记 + 自定义 `template_type=x` 时,五条 gates 照旧拦组合,单靠字符串不触发任何 legacy 副作用(memory init/BCN/容器消费都改为看快照标记,已在 TEMPLATE_CONFIG_CONSUMING_TYPES 删除时落地)。
- **列表体积**:`/all` 每卡片回整段快照(image/envs/bot_template_config),分页 20 条可接受;如将来卡片列表嫌大,再压缩(本期 YAGNI)。
- **改名窗口**:openapi 面未上线、无存量调用,`template` → `template_config` 零兼容成本;上线后键名冻结。

## 10. 测试计划

创建面(端点级,mock strategy 依赖按现有测试模式):

1. 普通 bot:不传 `engine_properties` → 201,行为不变(回归)。
2. 手填路径:传 `template_config` 无工厂标记 + `template_type` 省略 → 落 `applicationCoding`,既有断言全量迁移通过(回归)。
3. 手填路径传 `template_type=personalCoding` → 422(§5.6)。
4. 工厂路径:tc-list item 照抄体 → 201,落库快照断言(密钥位置为密文)、`template_type` 为透传值。
5. 工厂路径缺 `template_type` → 422(§5.2)。
6. 工厂路径带 `engine_form`/`workspace_id` → 422 server-managed(§5.5)。
7. 工厂 + 手填专用键混传 → 422(§5.4)。
8. 工厂 + `bot_type=service` / `engine=openclaw` / 团队空间 → 409(§5.7)。
9. `engine_properties` 传旧键 `template` → 422 unsupported fields(改名反向回归)。

strategy 单测(test_application_coding_create.py):

10. 上述 2-9 的核心层映射,含错序 gates 不变断言(错误类型/消息镜像保持)。
11. 工厂快照 token/thetaKey 加密后为 `enc:v1:` 前缀。

查询面:

12. 三面(bots/{id}/all)工厂 bot 的 `template_config` 回显 = 全量减 `token`/`ext_config.thetaKey`;`envs/image/resource_spec/template_name/custom_field_values` 原样在。
13. legacy bot(无工厂标记)投影回归:仍只回 6 键 allowlist。
14. `/all` 对 `template_type` 非空但快照投影为空的卡片行为不变(None,不挂)。
15. 存量工厂快照(老 TC 链路落的,带工厂标记)在三个查询面切到透传投影(§6 有意变更的回归锚点)。

覆盖率:新增/改动行按仓库 80% 门槛跑 `ci_test.sh` 验证(推 PR 前本地复现)。

## 11. 上线与同步

- 分支:从 `origin/dev` 新开功能分支(当前 `w3-source-credentials-cr-rework` 是 W3 凭据主题,不混入)。
- gateway spec 双写:avernet + ocb 两侧同步提交(既有约定)。
- 前端契约冻结:v3 键名(`template_type`/`template_config`)上线后不再改;联调方以本文件 §3 为准。
