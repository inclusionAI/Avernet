# 专家模板发现与创建 API

专家目录与运行中的公开 Bot 是独立资源。部署选择受控来源仓库，既有 GitSync
取得一次完整 commit 后扫描 `agents/<expert_id>/expert.yaml`。客户端不能指定
tenant、环境、来源仓库或 Git 版本；查询范围来自已验证 principal 与服务环境。

| 方法与路径 | 请求与响应 |
| --- | --- |
| `GET /openapi/v1/experts` | `keyword` 最长200字符，trim后按名称/说明/单个tag字面子串OR匹配；英文不区分大小写。`category`单选并AND；`sort=hot\|newest`默认hot；`page_num=1`，`page_size=20`且1..100。返回标准 `Envelope[Page[ExpertSummary]]`。 |
| `GET /openapi/v1/experts/categories` | 当前可见有效目录的全部分类字符串数组；不受某页搜索结果限制。 |
| `POST /openapi/v1/experts/{expert_id}/bots` | 原 `BotCreate` body；必填 `user_id` query 沿原用户授权。返回原 `BotCreateWithManifestAccepted` 的202受理句柄，复用原状态轮询。 |
| `GET /openapi/v1/experts/{expert_id}` | 返回元信息、声明顺序的身份正文 `identity: [{type,content}]` 和标准Local Skill元信息 `skills: [{name,description}]`。不可见、已下架或未知条目返回标准404。 |

Summary字段为 `expert_id/name/description/category/tags/creation_count/first_added_at`。
接口不返回执行Manifest、完整取源配置、凭证、commit或后台诊断。空结果为成功且
`total=0/items=[]`，身份型专家的`skills=[]`是合法内容。

`hot`使用真实Bot初始来源的历史聚合（包含软删除与后续失败资产）；`newest`使用
首次有效登记时间。两种排序均在完整匹配集上执行后分页，以expert_id、source_repo_id
稳定收敛。目录没有派生Bot时次数为0；更新内容不重置首次登记。

单专家配置/引用无效保留最近有效内容，其他条目继续推进；完整枚举失败不执行
删除判断。完整枚举确认缺失或有效`enabled:false`仅关闭新发现/创建资格，保留目录
行与历史。有效输入、同commit文件引用及`sha256-json-v1`内容摘要持久存储，不依赖
下一轮覆盖的工作目录。数据库增量迁移见 `core/expert_market/sql/`。

创建读取目录最近有效输入并绑定完整commit，沿原Manifest校验、存储、Passport授权、
配额、名称冲突、空间及Factory合同提交。客户端不提交Manifest或系统来源。
202仅返回 `bot_id/iframe_url/redirect_url`，继续轮询
`GET /openapi/v1/bots/{bot_id}/with-manifest/status?user_id=...` 获取真实创建及配置进度。
首次POST结果未知且无bot_id时不要自动重发；先检查Bot列表，避免重复创建。

原任务绑定最终Manifest的真实存储引用、摘要和来源；读取前核对摘要，内容被修改时
停止并保留当前用户文档。系统来源随真实Bot初始写入，普通PUT/PATCH元数据不能修改
`expert_origin`，更新其他ext键保留来源。公开Bot DTO只增加只读 `expert_name`；普通Bot
为null，不公开commit、摘要或内部来源对象。受理和等待授权不计数；每个真实Bot仅计一次，
service草稿、建行后失败和软删仍计，恢复原Bot不重复。

OpenClaw沿原身份投递；Claude Code按声明顺序、源文件名标题及完整原文确定性组装
`CLAUDE.md`。未证明身份运行消费的Teclaw组合在创建前明确拒绝；其他当前构建不支持的
Manifest扩展同样明确拒绝，不删除作者声明以降级通过。服务Bot保持原草稿及发布流程，
创建/配置完成不代表已发布生产。


## 原 Bot 配置恢复与当前报告

Bot 已存在且创建初始化配置失败（包括 `PARTIAL`）时，在原有 owner-only
`POST /openapi/v1/bots/{bot_id}/config-manifest/apply?user_id=...&recover_creation=true`
显式恢复。此选择器只适用于同一用户存储作用域下的原绑定失败任务；任务仍运行、
已配置成功、没有原绑定，或存储引用/清单摘要不一致时返回标准409。
`dry_run=true` 可在同一绑定约束下预览。缺省 `recover_creation=false` 保持现有普通
Apply 语义，用户创建完成后的配置不永久锁定初始模板。

恢复返回202和新的 `apply_id`，沿既有
`GET .../config-manifest/applies/{apply_id}` 查询该报告；只有 `SUCCEEDED` 表示配置完成，
`PARTIAL` 与 `FAILED` 都保持配置失败。创建状态接口继续描述原创建任务；正式前端在
创建结果及 Bot 管理的“专家配置与恢复”入口读取当前 `last-apply`，展示新编号和条目错误。
恢复不重新创建 Bot、不改来源或累计次数、不重新编译 Git HEAD，也不回写旧清单。

用户显式重试沿原生 Replace：仅省略类目才不操作，显式 resources（含 `[]`）清理
workspace 非保护项后写入；不承诺保留所有用户输出。Git 模板更新或下架本身不触发
已有 Bot 再 Apply，也不下线已有 Bot。首次创建 POST 结果未知且没有 bot_id 的边界保持不变。
