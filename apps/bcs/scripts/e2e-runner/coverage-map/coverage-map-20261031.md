# BCS E2E 41 用例覆盖图 · 2026-10-31（B 树证据化）
> 生成时间 2026-10-31T03:22:12+0800；基线 `1f996eda4`（已提交口径唯一基准）；采集观测 HEAD `76a79fd00`（03:04，基线后 1 笔 TLS 波次）；交付复核 HEAD `992f9f815`（基线后共 7 笔 committed 波次：P1×1/P3×1/P4×3/P6×2，§3）。
> 口径不变：41 行数值、双环境化桶、verdict 桶、义务 674/parsed 638 均按基线 committed 判定；landing 波次只作观测披露，不改闭合判定。交付时在飞 dirty 收敛为 draft_delivery 2 文件 + untracked workspace 契约（§3）。
> 置信标注：【观测】=直接读到的文件/收据/账本；【推断】=由观测推导的归属/排序。

## 0. 口径纪律
1. 基线=已提交 `1f996eda4`；基线后全部 committed 波次（交付复核共 7 笔 76a79fd00→992f9f815，§3）与 dirty/untracked 路径只作 landing 观测。六个补丁族（P0 workspace 字段契约、P1 TLS、P2 operator profile 环境变量名、P3 CLI stderr、P4 provider 活体准入、P5 fixed-loop DUAL 注册、P6 edge 红边）涉及的 23 行一律标 ⏳pending-landing——语义=「基线口径缺口义务未闭合」，L1-L7 landed 波次不改判；P0/P2/P5 截至交付无落地证据（无证据≠落地否定）。
2. 不伪造分母：17 只全绿†单例保持 †；NOT_RUN/BLOCKED 不美化为 PASS；快照旧值与 committed 新值双记（漂移注 §7）。
3. dual 三分法以 committed `executor-migration.json` 为权威：DUAL=36 + #30 EDGE_OBLIGATION_IN_FAMILY 族内近似计入 → 37；LOCAL_ONLY=4（#23-26 DYNAMIC_OWNED_LOCAL）；NONE=0（#20 subbots 基线已 dual 注册，快照 NONE=1 过时）。
4. 远程记账规则：r5-r8 全部预发收据 `remote_validation=NOT_RUN`、`grants_release_credit=false`（2026-09-20 起规则）。本图「预发 verdict」桶按 preprod-tier 实弹收据 status；该记账字段全局披露一次，不逐行重复。
5. 观测 vs 推断分离；空结果（如 operator 近空收据、group-lifecycle 无 state 目录）如实记，不当负证据。
6. 绝不回显凭据值；仅引用环境变量名、文件路径、二进制 digest 前缀。

## 1. 机读合计
| 指标 | 值 | 注 |
|---|---:|---|
| 用例行数 | 41 | 28 stories + 13 edge_permission（roster 附件） |
| 双环境化 | DUAL=37 / LOCAL_ONLY=4 / NONE=0 | committed executor-migration 实测；快照 27/13/1 过时 |
| 缺口 NONE 行 | 6 | #16 #21 #22 #29 #30 #36 |
| 预发最新 verdict | PASS=17 / PARTIAL=1 / BLOCKED=15 / NOT_RUN=8 | #7=PARTIAL(9/10 叶)；#30 PASS* 族内近似 |
| pending-landing 行 | 23 | 六补丁族并集；另 #12/#19 仅在飞 dirty 观察（族归属未定） |
| 义务合计（committed 锚） | 674 | 契约 requirements/obligations + WP-B 代码锚 44/87/12；快照 B.1「31 执行器/309 义务」为 07:39 旧口径不重算 |
| 上游 parsed 已知合计 | 638 | † 17 只分母未给不伪造；#20 为 1 wrapper（131 checks 未义务化）；#37† |
| 上游 poolab 参照 | 324绿/386 | 2026-10-29 第三发；与本图义务面不直接相减 |

## 2. 41 用例 × 六列主表
| # | 用例 id | 上游 parsed 绿/总 | 本地投影 PASS/NOT_RUN（叶；obl） | 双环境化 | 预发最新 verdict | 缺口点 |
|---:|---|---|---|---|---|---|
| 1 | `story_human_manages_delivery_policy` | 全绿†（parsed 22） | PASS 1/1 叶（obl 10） | DUAL | PASS(r6) | parsed 22 − 义务 10 = 12（上游无原生 assertion-id，未逐条定位） |
| 2 | `story_user_prepares_agent_network` | 全绿†（parsed 11） | PASS 8/8 叶（obl 8） | DUAL | PASS(r7) | parsed 11 − 义务 8 = 3（未逐条定位） |
| 3 | `story_user_runs_structured_collaboration` | 全绿†（parsed 43） | PASS 1/1 叶（obl 17） | DUAL | PASS(r7) | parsed 43 − 义务 17 = 26（structured Group/Join/Leave/Message 逐字段） |
| 4 | `story_provider_operator_publishes_agent` | 全绿†（parsed 98） | NOT_RUN 0/1 叶（obl 98） | DUAL | BLOCKED(r8) | 密度闭合 98/98；执行缺口 98（凭证门：人类 cookie 2026-10-29 过期 + provider 物料）；⏳P4；⏳P4 |
| 5 | `story_provider_owner_isolation` | 全绿†（parsed 1） | NOT_RUN 0/1 叶（obl 24） | DUAL | BLOCKED(r8) | 上游 parsed† 无分母（snapshot parsed=1）；义务 24 全部未执行；⏳P4；⏳P4 |
| 6 | `story_session_owner_permissions` | 全绿†（parsed 1） | NOT_RUN 0/1 叶（obl 9） | DUAL | BLOCKED(r8) | 上游 parsed† 无分母（snapshot parsed=1）；义务 9 全部未执行；⏳P4；⏳P4 |
| 7 | `story_user_operates_group_workspace` | 26绿/45（parsed 59） | PASS 10/10 叶（obl 17） | DUAL | PARTIAL(r8) | parsed 59 − 义务 17 = 42（快照口径）；在飞契约 43=17+26field 层；P0 落地后残余 16；⏳P0 pending-landing；⏳P0 |
| 8 | `story_user_builds_trusted_team` | 2绿/17（parsed 18） | PASS 8/BLOCKED 5/总叶 13（obl 58） | DUAL | BLOCKED(r8) | 义务 58 已超上游 parsed 18（密度超集不冲抵）；阻塞点=上游 15 条红（2/17）+5 叶 BLOCKED；⏳P6 部分叶；⏳P6 |
| 9 | `story_operator_coordinates_with_cli` | 23绿/26（parsed 28） | NOT_RUN 0/1 叶（obl 13） | DUAL | BLOCKED(r8) | parsed 28 − 义务 13 = 15（health/list/get/discover/visibility/connect/onboard-web/direct-onboard/update-status/chat/cancel/group-list/friend-list 逐字段）；⏳P2/P3；⏳P2/P3 |
| 10 | `story_user_validates_external_channel_setup` | 6绿/8（parsed 13） | PASS 1/1 叶（obl 6） | DUAL | PASS(r8) | parsed 13 − 义务 6 = 7（disabled bridge 错误码/message、pause/delete no-op、target-scoped empty list、no phantom binding）；⏳P1/P3 诊断性；⏳P1/P3 |
| 11 | `story_cli_operator_validates_channel_management` | 2绿/4（parsed 16） | NOT_RUN 0/1 叶（obl 9） | DUAL | BLOCKED(r8) | parsed 16 − 义务 9 = 7（真实 DingTalk/OAuth conversation lookup、redacted bind contract、unbind missing、no phantom binding）；⏳P3；⏳P3 |
| 12 | `story_collaboration_draft_delivery` | 0绿/2（parsed 32） | NOT_RUN 0/1 叶（obl 32） | DUAL | BLOCKED(r8) | parsed 32 − 义务 32 = 0（密度闭合）；执行缺口 32；office OAuth SDK（RC1 public bcs-cli）+ 身份通道 302（RC2 产品缺陷在案）；在飞 dirty 观察 |
| 13 | `story_user_has_direct_agent_conversation` | 0绿/2（parsed 14） | NOT_RUN 0/1 叶（obl 5） | DUAL | BLOCKED(r8) | parsed 14 − 义务 5 = 9（async 202→run/session id、read-back state、cancel 幂等/终态完整链路）；cancel 终态缺陷在案；⏳P1/P3；⏳P1/P3 |
| 14 | `story_cli_operator_creates_custom_collaboration` | 0绿/1（parsed 37） | NOT_RUN 0/1 叶（obl 3） | DUAL | BLOCKED(r8) | parsed 37 − 义务 3 = 34（YAML two-role/nodes、permission session/group、one-shot graph、pending Human、AixUI panel、cancel terminal、persistent group）；trial_permission 合同缺陷在案；⏳P3；⏳P3 |
| 15 | `story_cli_operator_builds_collaboration_team` | 13绿/14（parsed 30） | NOT_RUN 0/1 叶（obl 8） | DUAL | BLOCKED(r8) | parsed 30 − 义务 8 = 22（protect/request/reject CLI、driver/member、current-bot list vs formal API、fuse recommendation）；站内 legacy friend CLI 路径；⏳P3；⏳P3 |
| 16 | `story_cli_operator_runs_sessions_and_services` | 0绿/1（parsed 44） | PASS 4/4 叶（obl 44） | DUAL | PASS(r6) | 密度闭合（44≥44）；WP-B 2026-10-31 committed |
| 17 | `story_cli_operator_manages_group_context` | 0绿/1（parsed 18） | PASS 1/1 叶（obl 9） | DUAL | PASS(r6) | parsed 18 − 义务 9 = 9（group/session/round add/get/update/list 逐字段与清理） |
| 18 | `story_authority_user_journey` | 0绿/1（parsed 42） | owned-local LIVE 42/42（obl 42）（obl 42） | DUAL | BLOCKED(r8) | 密度闭合 42/42（本地 LIVE）；预发执行缺口 42（route-probe 声明绑定 vs 实际 + 准入境）；⏳P4；⏳P4 |
| 19 | `story_retired_service_policy` | 0绿/1（parsed 23） | NOT_RUN 0/1 叶（obl 8） | DUAL | BLOCKED(r8) | parsed 23 checks − 义务 8 = 15（legacy policy absent create/read、410 retired、invocation not blocked、running conflict、chat completion guard）；410/200 形态差；⏳在飞 dirty 观察（不在六补丁命名族） |
| 20 | `story_provider_operator_manages_subbots` | 0绿/1（parsed 1） | NOT_RUN 0/1 叶（obl 15） | DUAL | BLOCKED(r8) | 上游 parsed=1 wrapper（131 live HTTP checks 未义务化）；义务 15 全未执行；⏳P4；⏳P4 |
| 21 | `story_user_runs_and_shares_sessions` | 8绿/9（parsed 9） | CLI 8/8 PASS + owned-local 87/87（obl 87） | DUAL | PASS(r8) | 密度闭合（义务 87 ≥ parsed 9）；WP-B 9→87 committed；public/flow 腿为历史阻塞注记非当前缺口 |
| 22 | `story_session_file_workspace` | 0绿/1（parsed 12） | PASS 1/1 叶（obl 12） | DUAL | PASS(r7) | 密度闭合（12≥12）；WP-B 10→12 committed；r3 303 为瞬时镜头不实列为缺口 |
| 23 | `story_fixed_loop_human_editor_workflow` | 0绿/1（parsed 1） | NOT_RUN 0/1 叶（obl 8） | LOCAL_ONLY | NOT_RUN(—) | parsed†=1 ≤ 义务 8；动态 owned runtime+fresh fixture；⏳P5；⏳P5 |
| 24 | `story_nested_fixed_loop_workflows` | 0绿/1（parsed 1） | NOT_RUN 0/1 叶（obl 16） | LOCAL_ONLY | NOT_RUN(—) | parsed†=1 ≤ 义务 16（nested Bot/三级 Human routing/isolation/skips/history/stale-event fences）；⏳P5 |
| 25 | `story_state_machine_result_queue` | 0绿/1（parsed 1） | NOT_RUN 0/1 叶（obl 18） | LOCAL_ONLY | NOT_RUN(—) | parsed†=1 ≤ 义务 18（owner Send/observer Inject/bound context/unique history）；⏳P5 |
| 26 | `story_collaboration_draft_group_launch` | 0绿/1（parsed 1） | NOT_RUN 0/1 叶（obl 17） | LOCAL_ONLY | NOT_RUN(—) | parsed†=1 ≤ 义务 17（3-bot author/driver/reviewer draft group launch 与授权）；⏳P5 |
| 27 | `story_user_receives_group_event_webhooks` | 1绿/2（parsed 2） | NOT_RUN 0/1 叶（obl 20） | DUAL | NOT_RUN(—) | 义务 20 ≥ parsed 2（超集）；receiver origin GAP；缺 8 操作 edge_case_bindings + principal_signing_key_env 引用 + mock sink；⏳P1；⏳P1 |
| 28 | `story_provider_callback_survives_slow_judge` | 1绿/2（parsed 2） | NOT_RUN 0/1 叶（obl 22） | DUAL | NOT_RUN(—) | 义务 22 ≥ parsed 2（超集）；judge request observation/slow delay/provider callback/retire；⏳P1；⏳P1 |
| 29 | `test_ep_admission` | 全绿†（parsed 2） | PASS 1/1 叶（obl 2） | DUAL | PASS(r7) | 覆盖 FULL（义务 2 ≥ parsed 2；r5/r7 实弹 PASS） |
| 30 | `test_ep_ensure_bot` | 全绿†（parsed 1） | PASS 1/1（族内近似；wpc ENSURE-BOT-01 PASS）（obl 1） | DUAL*（族内近似） | PASS*(r7) | 覆盖 FULL（族内近似 1≥1）；独立腿注册为近似非精确——如实注 |
| 31 | `test_ep_bot_search` | 全绿†（parsed 13） | PASS 1/1 叶（obl 13） | DUAL | PASS(r7) | 快照 delta=1（parsed 13−义务 12）；committed 契约 13 已追平 parsed 13——漂移resolved 注记，不改 NONE=6 计数；如actionable 余 1 条字段级核对 |
| 32 | `test_ep_error_and_admission_branches` | 全绿†（parsed 7） | PASS 1/1 叶（obl 1） | DUAL | PASS(r5) | parsed 7 − 义务 1 = 6（未逐条定位） |
| 33 | `test_ep_connect_private_and_discover_branches` | 全绿†（parsed 8） | PASS 1/1 叶（obl 1） | DUAL | PASS(r5) | parsed 8 − 义务 1 = 7 |
| 34 | `test_ep_friend_connections_friend_request_and_list` | 全绿†（parsed 3） | PASS 1/1 叶（obl 2） | DUAL | PASS(r7) | parsed 3 − 义务 2 = 1 |
| 35 | `test_ep_friend_connections_friend_accept_reject_cancel` | 全绿†（parsed 2） | PASS 1/1 叶（obl 1） | DUAL | PASS(r5) | parsed 2 − 义务 1 = 1 |
| 36 | `test_ep_friend_connections_friend_requests_list` | 全绿†（parsed 1） | PASS 1/1 叶（obl 3） | DUAL | PASS(r7) | 覆盖 FULL（义务 3 ≥ parsed 1） |
| 37 | `test_ep_friend_connections_friend_revoke` | 全绿†（parsed 分母未给） | PASS 1/1（wpc 2026-10-31 刷新；快照 0/1 NOT_RUN 已过时）（obl 1） | DUAL | BLOCKED(r5/r6) | 上游 parsed† 分母未给；E9 缺陷在案（产品在案）；义务 1；wpc 本地 PASS；预发 dual 腿 ⏳P6；⏳P6 |
| 38 | `test_ep_friend_connections_full_lifecycle` | 全绿†（parsed 7） | PASS 1/1（wpc 2026-10-31 刷新；快照 0/1 NOT_RUN 已过时）（obl 1） | DUAL | BLOCKED(r5/r6) | parsed 7 − 义务 1 = 6；E10 缺陷在案；⏳P6；⏳P6 |
| 39 | `test_ep_friend_connections_mutual_auto_approve` | 全绿†（parsed 8） | PASS 1/1 叶（obl 1） | DUAL | PASS(r5/r6) | parsed 8 − 义务 1 = 7 |
| 40 | `test_ep_tc_friend_auth_sync_readdressed_principal` | 1绿/2（parsed 2） | PASS（wpc owned-local 2026-10-31 刷新；快照 NOT_RUN 已过时）（obl 5） | DUAL | NOT_RUN(r8) | 义务 5 ≥ parsed 2（超集）；预发执行缺口（准入凭证窗口）；station bot token 环境变量引用 GAP；⏳P6；⏳P6 |
| 41 | `test_ep_dept_scope_parses_object_friend_ext_entries` | 5绿/6（parsed 4） | PASS（wpc owned-local 2026-10-31 刷新；快照 NOT_RUN 已过时）（obl 7） | DUAL | NOT_RUN(r8) | 义务 7 ≥ parsed 4（snapshot）/roster 总 6（双口径注）；预发执行缺口；station bot token 环境变量引用 GAP；⏳P6；⏳P6 |

图例：†=roster 全绿单例分母未给（parsed 数来自 07:39 快照断言解析口径）；obl=本地义务数（committed 契约/代码锚，非上游断言）；⏳Pn=pending-landing 补丁族（§3）；verdict 后无轮次=无预发腿。
双环境化注：#30 为 EDGE_OBLIGATION_IN_FAMILY 族内近似（无独立 dual 命令），计入 DUAL 桶带星；#23-26 为 DYNAMIC_OWNED_LOCAL（LOCAL_ONLY 车道，预发不按 dual 计）。

## 3. 基线后 landing 波次与在飞残余（一律不改基线闭合判定）
采集时刻（03:04）dirty 35 modified + 4 untracked；交付复核 dirty 收敛为 2 modified + 2 untracked——六补丁主体已随下表 committed 落地（观测=提交文件清单；族/行映射部分为推断）。
| 波次 | sha | 族 | committed 内容 | 关联行（面） |
|---|---|---|---|---|
| L1 | `76a79fd00` | P1 TLS | preprod child transports fail-closed 钉死（authority/draft_delivery/service_policy story 面 + 专用 test） | #10 #13 #27 #28（传输面，推断） |
| L2 | `f2905e2d3` | P4 站内 duo 凭证面 | station duo faces 校验 Human cookie 伴 driver Bot bearer 附着（值不落盘）；draft_delivery/service_policy/slow_judge 代码面 + 两份 dual 契约 cookie_reference 描述文本（计数不变） | #12 #19 #28（代码面）；#4 #5 #6 #18 #20（station duo 凭证受益面，推断） |
| L3 | `b8de7082b` | P3 CLI stderr | bounded scrubbed stderr 细节透传（CLI 家族收据 error_detail 镜头可诊断；operator_cli 仅 +1/+7 管道行，非 P2 环境变量名修复） | #9 #10 #11 #13 #14 #15 |
| L4 | `3c6d6f374` | P4 provider 准入 | honest read-only preprod admission surface（dual_case_run.py +343、准入 test +432） | #4 #5 #6 #18 #20 |
| L5 | `341e19f62` | P6 edge 红边 dual | create_as_bot route 绑定；#40/#41 inventory lane 翻 DUAL_EXECUTION_READY、execution_command=dual-edge-friend-mutation-run、+station_profile 参数；红边 guards test +204 | #8(部分叶) #37 #38 #40 #41 |
| L6 | `a632d9749` | P4 authority probe | station ACE HTML gate route probes refreeze | #18（probe 面） |
| L7 | `992f9f815` | P6 guards | edge friend-mutation 契约 guards 同步 DUAL_EXECUTION_READY | #37 #38 #40 #41（guard 面） |

**仍未落地（无 committed 波次且当前无 dirty 证据；无证据≠落地否定）**：P0 workspace 字段密度契约（workspace 路径无提交；仅 untracked `workspace-migration-contract.json` requirements=43=17+26field；#7）；P2 operator profile 环境变量名（L3 的 operator 改动仅 stderr 管道；#9）；P5 fixed-loop DUAL 注册（L5 翻的是 edge friend-mutation lane 而非 dual-fixed-loop 注册；committed/dirty 树均未见 fixed-loop dual 注册；#23-26）。

**交付时在飞 dirty**：`bcs_e2e/draft_delivery/story.py` + `tests/test_draft_delivery_story.py`（#12 附加观察，族归属未定）。

**纪律注**：① L1-L7 均为基线 1f996eda4 之后的 committed 波次，按任务口径一律不进入本图已闭合判定；⏳Pn 语义=「基线口径缺口义务未闭合」。② 波次缩小的是代码面/登记面；预发实弹腿（remote_validation=NOT_RUN 收据）与用户物料门控（M1-M12）不因 landing 自动闭合。③ executor-migration.json 在 1f996eda4..992f9f815 无 diff（dual mode 权威稳定）；两份被触契约（service-policy-dual-/slow-judge-dual-）经 f2905e2d3 仅 cookie_reference 描述文本 2 行，计数口径不变。

## 4. 迁移队列（缺口量×实弹可行性；score=gap×feasibility 1-3）
| 秩 | # | 用例 | gap 量 | 可行性 | score |  basis（含缺物料） |
|---:|---:|---|---:|---:|---:|---|
| 1 | 7 | `story_user_operates_group_workspace` | 42 | 3 | 126 | 密度 delta=42×3；零新物料（r8 9/10 基础）；P0 契约落地后逐字段展开，残余 16 |
| 2 | 18 | `story_authority_user_journey` | 42 | 3 | 126 | 执行缺口 42×3；本地 42/42 已闭，预发腿准入诊断后零新物料 |
| 3 | 4 | `story_provider_operator_publishes_agent` | 98 | 1 | 98 | 执行缺口 98×1；物料门控（cookie 刷新需用户授权+P4+frozen S04 本地形态） |
| 4 | 3 | `story_user_runs_structured_collaboration` | 26 | 3 | 78 | 密度 delta=26×3；零新物料，最高纯行动项 |
| 5 | 14 | `story_cli_operator_creates_custom_collaboration` | 34 | 2 | 68 | 密度 delta=34×2；缺陷修复先决 |
| 6 | 12 | `story_collaboration_draft_delivery` | 24 | 2 | 48 | 执行缺口按密度口径 24（parsed 32−legacy 8）×2；SDK/302 门控 |
| 7 | 5 | `story_provider_owner_isolation` | 24 | 2 | 48 | 执行缺口 24×2；P4 准入落地后可实弹 |
| 8 | 15 | `story_cli_operator_builds_collaboration_team` | 22 | 2 | 44 | delta=22×2；站内路径物料 |
| 9 | 1 | `story_human_manages_delivery_policy` | 12 | 3 | 36 | 密度 delta=12×3；零新物料，可直接 dual 腿内逐字段展开 |
| 10 | 9 | `story_operator_coordinates_with_cli` | 15 | 2 | 30 | 密度 delta=15×2；5-bot actor token 套件+duo 凭据+health 先绿 |
| 11 | 19 | `story_retired_service_policy` | 15 | 2 | 30 | delta=15×2；形态差核对+P4 关联 |
| 12 | 20 | `story_provider_operator_manages_subbots` | 15 | 2 | 30 | 执行缺口 15×2；probe 9/9 已 mounted，凭证物料到位即可实弹 |
| 13 | 13 | `story_user_has_direct_agent_conversation` | 9 | 3 | 27 | delta=9×3；缺陷修复即重跑 |
| 14 | 17 | `story_cli_operator_manages_group_context` | 9 | 3 | 27 | delta=9×3；零物料（既有 dual-group-context-run 内扩展） |
| 15 | 28 | `story_provider_callback_survives_slow_judge` | 22 | 1 | 22 | obl 22×1；judge mock 门控 |

其余 20 项（机读序=score 降序×手工秩 tiebreak）：#10 21、#27 20、#6 18、#25 18、#26 17、#24 16、#8 15、#41 14、#38 12、#40 10、#2 9、#23 8（LOCAL_ONLY 车道，推断补入）、#39/#11/#33 7、#32 6、#37 3、#31/#34/#35 1；见 JSON `migration_queue.ranked`。未入队（密度 NONE 缺口）：#16 #21 #22 #29 #30 #36；LOCAL_ONLY 车道（预发不按 dual 计）：#23-26。

## 5. 材料缺口登记
任务点名三种：**5-bot actor token 套件**（#9，原名 GAP）、**webhook receiver origin**（#27，另需 8 操作 edge_case_bindings + principal_signing_key_env 环境变量引用 + mock sink）、**station bot token 环境变量引用**（#40/#41，名称 GAP）。全量 M1-M12 见 JSON `material_gaps`；产品在案缺陷（M11）不阻塞本地登记但阻塞预发实弹。

## 6. 证据索引（绝对路径）
- 预发轮次根：`/Users/shenlifang/.codex/bcs-e2e/preprod-live-evidence/`
  - r5：`20261031-regression-r5-bulk`（54 叶；bulk-results.tsv wrapper_rc 43×0/11×2）、`-r5-edge-read`（4 PUBLIC-EDGE PASS）、`-r5-edge-mutation`（6 LOCAL-EDGE：4PASS/2BLOCKED E9/E10）、`-r5-{channel-bindings,cli-defects,collab-team,custom-collab,direct-chat,delivery-policy,friend-decisions,friend-public-accept,group-context,network,operator-cli,session,session-file,session-service,structured,workspace}` 与 r5b-r5f 重试轮
  - r6：`20261031-regression-r6-{bulk,channel-bindings,cli-defects,collab-team,custom-collab,delivery-policy,direct-chat,edge-mutation,edge-read,friend-decisions,friend-public-accept,group-context,network,operator-cli,provider,session,session-file,session-public,session-service,structured,workspace}`
  - r7：`20261031-regression-r7-preprod/`（账本 r7-progress.tsv 24 行=17PASS+7BLOCKED；session-file r2/r4 PASS、r3 BLOCKED 瞬时 303）
  - r8（最新权威轮）：`20261031-regression-r8-preprod/`（r8-progress.tsv 37 行；r8-blocked-verdicts.txt；workspace×10、session×8、channel-bindings、friend×2 r3、provider×4、service-policy、authority、draft-delivery、cli-defects×2、collab-team、operator-cli、custom-collab、direct-chat、edge-permission-backlog×4）
  - wpa/wpb/wpc/wp2/wpe3/wps：`20261030-migration-wpa-provider-subbots-routeprobe-r1`（probe 9/9 mounted）、`20261031-migration-wpa-provider-subbots-local-r1..r3`、`~/.codex/bcs-e2e/20261031-migration-wpb-{r1,r2,split}`（WP-B 44/87/12 实弹）、`20261031-migration-wpc-{local,tc-preprod,dept-preprod}`、`20261031-migration-wp2-authority-{local,preprod}`、`20261031-migration-wpe3-draft-delivery-{local-live-r5,preprod}`、`20261031-migration-wps-service-policy-preprod`、`20261031-migration-wp-mock-material`
- 本地 owned runtime LIVE：`~/.codex/bcs-e2e/e2e-dev-fresh-authority-live-1`、`-live-2-reports-offline`（#18 42/42）
- B 树契约面：`apps/bcs/scripts/e2e-runner/*-migration-contract.json`（39 份）+ `group-workspace-density-contract.json`（atom_count=63）+ `trusted-team-manifest.json`（assertions=58）+ `executor-migration.json` + `manifest.json` + `migration-contracts.json`（作业树只读引用，基线 1f996eda4）

## 7. 快照对账与漂移注（07:39 快照 → 本图）
- 双环境化：快照 27/13/1 → committed 实测 36 DUAL + 1 EDGE_OBLIGATION_IN_FAMILY + 4 DYNAMIC_OWNED_LOCAL → 任务三分法 37/4/0（subbots/service-policy/draft-delivery/publishes/provider-owner/session-owner/authority/custom/slow-judge/webhook 的 dualize 提交均 ≤ 基线：a1e822861 谱系至 1f996eda4）
- #4 义务 32→98（provider-publishes-migration-contract 96bb3ab9e；r8 verdict.json baseline42+field56=98）
- #16 4→44、#21 9→87、#22 10→12（WP-B 2026-10-31 committed：代码锚+运行时 total_count 双证）
- #18 16→42（authority-dual story_checks=42；快照 B.2「42/16」之 16 为 WP-A 前旧值）
- #31 12→13、#40 1→5、#41 1→7（committed 契约实测；98e893e87 edge backlog 义务扩展）
- #40/#41 本地 NOT_RUN→PASS、#37/#38 本地 0/1→PASS（wpc owned-local junit 9/9，2026-10-31 今日观测；快照 07:39 未含）
- #7 义务口径三重：快照 17（case 级）/ committed density 契约 atom_count=63（api26+cli11+tail26，密度预言机非义务）/ 在飞 43=17+26field（⏳P0）
- r5 bulk wrapper-rc 43/11（今日实测 tsv）vs 快照 case-status 42/12：口径差（wrapper rc vs case status）如实保留
- 快照 B.1「执行器 31/309、backlog 6/52、independent 4/60、maps 33/274」为 07:39 口径，本图不重算该分母（不伪造）；本地 assertions tally 152=101PASS+51NOT_RUN 同注
- caller_execution_story（caller-execution-migration-contract.json requirements=9）不在 41 名录——independent 切片，不入本图
- cases_preprod_20261031-r6.tsv 为 45 腿 recipe（上一轮批次口径）；本图 41 用例名册以 roster 附件为准，粒度不同不逐腿对冲
- session-migration-contract requirements=9 中含 public-jwt-ws 切片（r6 session-public BLOCKED 历史腿）——#21 最新 verdict 取 r8 session 8/8，public/flow 腿历史注记
- 【交付复核】基线后 7 笔 committed 波次（76a79fd00→992f9f815，§3 L1-L7）；executor-migration.json 在 1f996eda4..992f9f815 无 diff（dual mode 权威稳定）；service-policy-dual-/slow-judge-dual- 两契约经 f2905e2d3 仅 cookie_reference 描述文本各 2 行，计数口径不变
- 第三源对账（新观测）：executor-migration.json 登记器 preprod_verdict（PASS=19/BLOCKED_INVENTORIED=7/NOT_RUN=10/NOT_APPLICABLE=5）vs 本图实弹桶（PASS=17/PARTIAL=1/BLOCKED=15/NOT_RUN=8）——同向 27 行；NOT_APPLICABLE 5=LOCAL_ONLY #23-26 + EDGE_OBLIGATION_IN_FAMILY #30（本图 #30 族内近似 PASS*）；滞后差异 9 行（登记器字段未随 r8 实弹收据更新，推断）：下行 3 行 #3 trusted_team 登记 PASS vs 本图 BLOCKED(r6)、#4 provider 登记 PASS vs 本图 BLOCKED(r8 0/15)、#7 workspace 登记 PASS vs 本图 PARTIAL(9/10 叶)；NOT_RUN→BLOCKED 6 行 #6 #12 #18 #19 #20 #21；本图以 preprod-tier 实弹收据 status 为准（§0.4）

## 8. 收据卫生与记账规则注
- r8 edge-permission-backlog MISSING_CONSOLE：判定器按 *-console.txt 查找，实际文件命名 <label>-<env>-console.txt——命名卫生缺口，非执行缺口；preprod=NOT_RUN 以 r8-progress.tsv 环境分栏为权威
- r8 channel-cli/friend-cli console 为混合非纯 JSON；以 state/run.json 为权威（0/9、0/1）
- r8 operator-cli 近空收据：仅 binding/profile，console JSON 载 ready_to_execute=false 与 duo 凭据要求原文（无密文）
- r8 workspace/group-lifecycle BLOCKED 但无 state 目录（exec=None 全空）——落盘卫生缺口同族登记
- r8 CLI 二进制 ~/.codex/bcs-e2e/binaries/bcs-cli-preprod-r8（sha256 前缀 5abecdc6…）≠ B 树 apps/bcs/target/debug/bcs-cli（前缀 0e9f2e3f…b1e95）；收据按此注记，不作等价主张
- r7 session-file r3 BLOCKED（FileTransportError HTTP 303）→ r4 同参 PASS 12/12：瞬时镜头，r4 为最新
- mutation 族（#32/#33/#35/#37/#38/#39）收据形态：LOCAL-EDGE-* case_id + environment tier=preprod（gateway public-v1 / station bcs-direct）+ status 实弹值 + remote_validation=NOT_RUN；verdict 桶按实弹 status，记账字段全局披露
- wpc-local owned-local 套件 LOCAL_SUITE_PASS（junit 9/9）但 summary full_release_status=NOT_QUALIFIED：本地套件绿 ≠ release qualification
- r5-bulk bulk-results.tsv 54 叶无 status 列（wrapper_rc 推断：rc=0×43 / rc=2×11）；快照 case-status 口径 42/12 与 wrapper-rc 口径 43/11 的差异如实保留
- committed migration_inventory.py 残留 legacy 切片 status=LOCAL_EXECUTOR_ONLY（如 retired-service-policy/authority 旧切片行）与 executor-migration.json DUAL 并存：mode 权威取 executor-migration.json（观测）；trusted-team-manifest registration_status 同注（#8）
- provider-subbots r8 收据内嵌 preprod_probe 9/9 mounted PASS + assertion_summary required=131 observed=0 obligations=15；wpa routeprobe（2026-10-30）故事级 BLOCKED 但 probe 9/9 mounted——修正「控制面缺失」旧读法

## 9. 方法：观测 vs 推断
- 【观测】今日直接读取：executor-migration.json mode 分布（41 行）、39 份契约 requirements/obligations 计数（dirty 两份取 `git show 1f996eda4` 基线版）、r8-progress.tsv/r8-blocked-verdicts.txt、各家族 run.json status/error_type/remote_validation/credential_kind/assertion_summary/documented_gaps、wpc junit.xml 9/9 PASS、wpb 运行时 total_count（12/44 锚）、bulk-results.tsv 54 叶 rc 分布、r5/r6/r7 edge 收据 status、git log/status、density 契约 atom_count=63、trusted-team assertions=58、tc/dept obligations=5/7。
- 【观测快照引用】07:39 快照：parsed 数（59/18/28/…）、r5 时代本地叶粒度、B.1 总量、B.2 逐条缺口、C 材料分类——凡被今日实测刷新者已在 §7 标注。
- 【推断】dual 桶的三分法映射（#30 族内近似计入 DUAL）、迁移队列排序与 score、dirty 文件→补丁族归属、#23 LOCAL_ONLY 队列补入（标注「推断补入」）、#19/#12 在飞 dirty 族归属未定。
- 【不伪造】† 17 只全绿单例分母；#37 parsed 分母；#20 wrapper 131 checks 义务化；快照 31/309 等旧分母不重算。
