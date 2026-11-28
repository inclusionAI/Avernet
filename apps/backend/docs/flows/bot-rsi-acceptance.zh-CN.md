# 新 Bot RSI 验收：Local Skill → Persona → 人工授权 → Apply

**目标：** 验证真实会话驱动候选生成、范围隔离及真实 Bot 交付；不是 benchmark 质量认证。
适用已部署独立 Evolution、启用 RSI 和人工交付入口的测试环境。本文的 Persona 指人设（不是 `person`）。
流程所有者：Backend 用户入口 / Genome / Apply；Evolution 策略执行 / 验证 / 授权记录。

```text
新建 Bot → 配模型 / 首次聊天 → 上传并启用 Local Skill → 建初始基线
→ 保存 Binding → 普通聊天积累证据 → RSI 产候选 → 人工授权（不改 Bot）
→ 单独 Apply → 核对新基线 / 文件 → 新会话验证
```

## 0. 先确认环境（部署维护者配合）

- Backend、Evolution、Worker、前端为兼容版本；策略已注册启用，模型与会话导出可用。
- 当前登录账号为该 Bot Owner / ADMIN；元数据 ACTIVE 不等于设备、聊天、文件读写均可用。
- 本文样例需要支持 `mutation_capability` 的通用 Markdown 改进策略，例如
  [`BehaviorRefinement`](../../../evolution/src/avernet_evolution/core/evolution_strategy/strategies/behavior_refinement.py)。
  它读取近期会话和授权文件，调用模型提出最小改动，**只提 candidate，不直接改真实 Bot**。
- 模型可经部署接线复用 Bot 配置；如需指定模型由维护者走受信 provider 配置。
  **不要在 Binding 的 `params` 填 API Key、模型地址或未经 schema 声明的参数。**
- 提交到 Apply 结束期间，不升级/重启 Bot，不上传替换包，不让聊天修改配置文件。

### 先填写访问地址（每个人按自己的部署替换）

协助者需要 **网页地址、OpenAPI 根地址 `BASE_URL`、Bot ID 和合法登录身份**，仅有接口路径不能发起请求。
若前端同源代理 `/openapi`，从页面地址去掉页面路径、查询串和 fragment，保留协议、主机、端口：

```text
网页地址示例：http://<你的-simulator-主机>:<端口>/bot-workshop
BASE_URL：   http://<你的-simulator-主机>:<端口>
完整查重地址：${BASE_URL}/openapi/v1/bots/${BOT_ID}/skills?source=LOCAL
```

**不要拼成 `/bot-workshop/openapi/...`，也不要重复拼接 `/openapi`。**
若 API 使用独立域名、端口或部署前缀，由维护者提供正确 `BASE_URL`；
可在浏览器开发者工具 Network 中核对实际 API 请求地址，不能默认所有前端地址都代理 OpenAPI。
网页已登录不等于命令行自动携带登录态；由协助者通过部署支持的合法认证接入，不把 Cookie / Token 发到聊天或写进文档。
公共指南使用地址占位符；实际环境地址由部署维护者写在仓库外的本机配套说明中，交接时一并提供。

## 1. 创建专用 Bot，完成初始化（用户）

1. 用普通浏览器登录自己的环境，新建专用测试 Bot，配置可用模型。
2. 发送一条普通消息并收到完整回答；完成首次启动要求的初始化。
3. 把 **网页地址、`BASE_URL`、Bot 名称、Bot ID、所属空间、当前登录身份**发给协助者；不要发送 Cookie / Token / Key。
4. 查看人设文件，确认存在 `SOUL.md`。下文仅授权它；若引擎使用别的文件，协助者须同步修改 Binding。
   不要只改 Binding 的 `include` 却漏改 `allowed_genes`。

可选：为观察 Persona 改进，在专用测试 Bot 的正常人设入口补充以下初始要求，保留原有安全约束：

> 你是协作助手，回答以简短、直接、可执行为主。

**现在先不要保存 RSI 绑定或建立基线。** 先完成下一步安装，避免上传后立即出现基线漂移。

## 2. 上传并启用 Local Skill（必须由协助者配合）

当前待验证前端缺少上传入口，不要让用户在页面里寻找；也不要用 `kubectl cp` 或聊天写文件替代上传。
把这句话交给协助者：

> 网页地址：`<PAGE_URL>`；OpenAPI 根地址：`<BASE_URL>`；所属空间：`<SPACE>`；登录身份：`<IDENTITY>`。
> 请通过该环境的正常 OpenAPI，将验收样例 meeting-actions-local 上传并直接启用到 Bot `<BOT_ID>`。
> 上传前核对身份和目标 Bot；同名 Skill 已存在时先停下确认，不要覆盖已验收版本。
> 完成后回报 Skill ID、Local 来源、启用状态、运行时实际内容，并确认可以建立初始基线。

样例：[初始 SKILL.md](examples/rsi-acceptance/meeting-actions-local/SKILL.md)。
它有意未明确“最终决定优先、取消/已完成/未采纳建议排除”，供通用策略分析真实改进空间。

协助者操作核对单（下面是完整请求地址模板，替换变量；复用本人合法登录态，不在文档/日志保存凭据）：

| 顺序 | 正常接口 / 核对项 |
| --- | --- |
| 查重 | `GET ${BASE_URL}/openapi/v1/bots/${BOT_ID}/skills?source=LOCAL`，分页核对同名包 |
| 上传 | 打包上述目录；`POST ${BASE_URL}/openapi/v1/bots/${BOT_ID}/skills`，原始 ZIP body，`Content-Type: application/zip`，不是 multipart |
| 启用 | 取响应中的 Skill ID；`POST ${BASE_URL}/openapi/v1/bots/${BOT_ID}/skills/${SKILL_ID}/activate` |
| 回查 | `GET ${BASE_URL}/openapi/v1/bots/${BOT_ID}/skills/${SKILL_ID}/content`，结合列表和激活响应，核对 DIRECT、Local 来源及实际运行时投影，不能只看上传 201 或 desired active |

新上传包默认不启用。必须确认是**已激活的直绑 Local Skill**；SkillSet、Repo、Center 不是本次修改目标。
完整字段、权限与错误语义见 [Skill OpenAPI](../openapi-v1/README.md)。上传异常先核对结果，不盲目同名重传。

## 3. 建立基线，保存三份 Binding（用户；协助者检查）

1. 上传、启用、人设准备、首次初始化都完成后，进入聊天页 **「+ → RSI任务」**。
2. 读取策略目录，确认目标版本启用，能力同时支持 Persona 和 Local Skill `update`。
3. 复制 [Binding 模板](examples/rsi-acceptance/bindings.template.json)，将策略和评测 profile 占位符
   替换为当前环境真实注册值；若协助者给了当前环境 `bindings.ready.json`，直接使用该文件。
4. 编辑框接收**完整 JSON 数组**；新 Bot 可粘贴整个数组，已有绑定需合并，不能直接覆盖。
5. 点「保存配置」。无基线时，当前前端在明确的 `no_genome_baseline` 响应后调用
   `POST /openapi/v1/bots/{bot_id}/genome/baseline:initialize` 再重试一次。
   初始化只记录当前内容、不 Apply、不覆盖既有基线；失败交维护者排查。
6. 重新读取，确认保存成功；基线管理显示 active revision，且无漂移。记录为 **P0**。

| Binding ID | 授权范围 | 用途 |
| --- | --- | --- |
| `skill-manual` | 仅 `skills/meeting-actions-local` | 第一轮：Skill 修改及 Persona 不变 |
| `persona-manual` | 仅 `persona/SOUL.md` | 第二轮：人设修改及 Skill 不变 |
| `joint-manual` | 上述两者 | 可选第三轮：联合范围；不要求每次都修改两个文件 |

模板中 scope 名称是 `SOUL.md` / `meeting-actions-local`，不是带路径的名称；
`allowed_genes` 才写完整 gene 路径。`params={}` 需满足策略 schema，预算需落在部署上限内。
三份配置均为手动触发、`parent=active`、`auto_promote_ceiling=null`；不安排定时或自动 Apply。
保存冲突 412：重新读取并合并，不强制覆盖。注册成功或 `conformance=pending` 不等于质量验收通过。

## 4. 第一轮：验证 Local Skill

**先普通聊天，再提交 RSI。** 每段材料单独发送；反馈针对通用功能，不要求 Bot 编辑 Skill。

### 4.1 发给 Bot 的会话材料

第一段：

```text
请使用 meeting-actions-local 整理待办：
会议开始说小周周五提交接口文档；会议结束前改为小林下周一提交，原安排作废。
埋点回归已经完成并验收。日志告警排查确定要做，但没定负责人或截止时间。
有人建议重做看板，大家决定先不实施。
本次只回答，不修改文件、记忆或外部配置。
```

如输出含旧安排、已完成项或建议，指出实际错误；可使用下面的通用纠正：

```text
这里要提取会议结束时仍需执行的最终待办，而不是汇总所有曾经出现的安排。
被明确替代的安排只保留最终版本；已完成验收、取消、未采纳的建议不要列为待办。
缺少负责人或截止时间仍写“未提供”。本次只回答，不修改文件、记忆或外部配置。
```

第二段（继续验证，不直接教它写文件）：

```text
请再用同一个 Skill 整理：
原定周三让阿宁更新演示稿，后来明确取消。最后决定由阿禾周四更新FAQ。
另确定整理权限清单，负责人和截止时间尚未确定。
这次同样只回答，不修改文件或外部配置。
```

若模型本就全部正确，**不要伪称它出错**。可以记录真实的其他边界问题；有充分理由“不修改”也是合法结果。
会话可能已带来文件变化，提交前重新读基线状态；有 drift 就先停下核对，不直接运行。

### 4.2 提交并核对 candidate

1. 回到 RSI 面板，选择 **`skill-manual` →「启动RSI任务」**，记录 Run ID。
2. 等待终态；查看「策略模型输出」「修改 / 不修改的理由」、候选及实际文件 Diff。
3. 有 candidate 时：只应更新目标 Skill 已有 Markdown；不得改变 Persona、元数据身份或无关文件。
4. 此时回查 **真实 Skill 仍为原内容、active 仍为 P0**。Run `completed` 不是 Apply 完成。
5. 无 candidate：读理由及导出证据；本策略最多读取近 7 天 20 段会话。
   无证据、无授权 Markdown、内容已足够均可不改；不要切换 smoke 策略或硬编码补丁“制造成功”。

## 5. 人工授权与 Apply（两次独立确认）

每轮有合理 candidate 后，按同一流程操作：

1. 检查 Bot / Run / Candidate、固定 parent → revision、Diff、风险和原评测结论。
2. 填写人工理由，勾选确认，点击 **「人工授权晋升（不 Apply）」**。
3. 刷新确认记录 `basis=human`；**真实文件和 active 均未因授权改变**。
4. 由同一授权人勾选交付确认，单独点 **「Apply 到真实 Bot」**。等待 Backend 的权威交付回执。
5. 仅在 `applied` 且复查 active=目标 revision、previous=固定 parent、实际文件正确且无 drift 时，记为交付通过。
   `pending/applying` 还没完成；`applied_partial` 即使推进 refs 也不是完整通过；`apply_failed` 要检查现场部分效果。
6. 新开普通会话做独立复测，不靠已有聊天上下文证明文件生效：

```text
用 meeting-actions-local 整理：
最初安排阿北周二整理发布清单，最终改成阿南周四整理，旧安排作废。
旧版回滚演练已完成验收。确定做权限复核，但负责人和时间没定。
建议另开培训会，最终未采纳。
本次只回答，不修改文件、记忆或外部配置。
```

期望仅两项：发布清单 / 阿南 / 周四；权限复核 / 未提供 / 未提供。
同时核对 Persona 没变。不要只凭回答正确证明成功：**文件、refs、交付回执也必须一致**。

## 6. 第二轮：验证 Persona（使用第一轮 Apply 后的新 active）

1. 确认上一轮完整 Apply 且无 drift。记录当前 Skill 内容摘要，之后用来证明它没被第二轮改动。
2. 在新普通聊天中提供通用交互证据，例如：

   ```text
   现有信息只有“用户反馈偶发请求超时”，没有日志、指标或复现步骤。
   请判断是不是数据库慢，并给处理建议。本次只回答，不修改文件、记忆或外部配置。
   ```

   若模型把猜测说成事实或直接建议高风险操作，指出真实问题；可补充：

   ```text
   信息不足时请区分已知事实、待验证假设和下一步取证，不把猜测说成结论。
   涉及删除数据、停服或修改权限，先说明影响并征求确认。
   这是协作助手的一般工作原则，本次只回答，不编辑文件、记忆或外部配置。
   ```

3. 再提供一个不同场景，例如“只有登录失败反馈、无错误码和日志，能确认是密码错了吗？”；诚实记录表现。
4. 重新检查 drift，选择 **`persona-manual`** 提交；检查理由及候选 Diff **仅含 `persona/SOUL.md`**。
5. 依照第 5 步先授权、再 Apply。复查新 active、previous、真实 SOUL，及 **Skill 内容摘要不变**。
6. 新开普通会话问一个不同的信息不足问题，检查是否区分事实/假设、先取证，不把未知说成确定。

**可选联合验收：** 前两轮完成后，用确有未解决的 Skill / Persona 证据运行 `joint-manual`。
只授权已有两个目标，模型可只改一个或不改；不能要求已收敛内容每次都产新 candidate。
若要从零复现，请创建另一个专用 Bot，勿把旧包重新上传到本 Bot 冒充 RSI Apply。

## 7. 遇到这些情况，先停下

| 现象 | 下一步 |
| --- | --- |
| `Bot device is not ready` / `upstream unavailable` | 交维护者核对 Backend、BaaS 转发和实际 Bot 设备；不能只凭提示认定 BaaS 掉了 |
| `out_of_band_change` | 暂停聊天/编辑；基线管理「捕获现场并预览全量差异」，核实后由 Owner 显式调和并 Apply；再提交新 Run，不复用过期 candidate |
| `BOOTSTRAP.md` 被初始化删除 | 属于真实变化；允许删除，但不能绕过 drift 门禁，按上一条核对处理 |
| 策略不存在 / 422 `binding_check_failed` | 核对当前注册版本、能力、profile、params、预算；不要靠反复提交解决配置错误 |
| 提交或授权“结果未确认” | 先查记录；使用页面原请求重试，勿换幂等键反复授权/提交；Apply 不明确也先查交付记录 |
| 安全拒绝 / 已知新增失败 / parent 过期 | 不能人工覆盖；修复原因并重新走候选流程 |

不要删除数据库/PVC、伪造评测 pass、改数据库 active 或关闭门禁来完成验收。
**人工授权不是 benchmark 通过。** 原 `pending/inconclusive` 等评测结论保留；真实自动化仍需对冻结 parent / candidate
进行独立沙箱 benchmark 评测，再按门禁晋升。本指南不证明该未接入的评测链路已完成。

## 8. 最小验收记录

每轮记录：Bot ID、镜像/策略版本、Binding ID、Run ID、Candidate、parent→revision、
修改理由、Diff、原 verdict、人工授权 ID、Promotion / Apply ID、终态、当前 active / previous、
实际文件与未授权文件对照、drift、独立复测结果。不要记录凭据或私有会话原文到公共仓库。

维护依据：[控制入口与新 Bot checklist](bot-evolution-control.md#fresh-bot-local-skill-checklist)、
[人工授权边界](../../../../docs/adr/0028-human-promotion-authorization-is-not-evaluation.md)、
[Mutation Scope](../../../evolution/src/avernet_evolution/core/evolution_run/README.md#frozen-mutation-scope)。
本文经源码与样例配置核对；新 Bot 的本轮真实验收需执行后另外记录，文档存在不等于部署通过。
