# Service Bot CLI发布物名单开关

## 需求与边界
默认停止构建Home/.agentclaw和Home/.local的CLI发布物，后续性能问题解决后再按名单打开。延续现有范围：完整.agentclaw、仅.local/bin。不修改运行态所有权、历史版本恢复或原有引擎构建；不修改线上名单数据，不部署/重启Bot。

源码worktree来自2026-10-20远端dev a558e89670；前置CLI发布物PR2915已合并。使用新分支feat/service-bot-cli-artifact-allowlist。

## 名单模型与判定
复用ac_entity_user_list：user_list_type=service_bot_runtime_artifact，entity_id为源Bot所属用户ID（不是发布操作者），env为注入的运行环境。没有记录默认false；同类型同环境存在entity_id='*'时全部开启；否则精确匹配用户。使用已有UserListRepositoryProtocol参数化exists，不改变其精确匹配语义。每次build解析一次并传入migration，本次构建使用固定结果；DB读取失败fail closed并记录异常类型。

通用名单correct API对所有登录用户可写，新增服务端专用type须在既有领域服务拒绝修改并映射清晰的4xx；其他前端名单行为保持。运维通过数据库维护开关，提供SELECT与单用户/全量INSERT/DELETE示例但不执行。

## 方法与接线
BotBuildService注入必需的UserListRepositoryProtocol，由composition root提供；构建resolve一次include_runtime_artifacts布尔，_migrate_bot_instance默认false。不为测试增加可选DI或getattr兜底。
两个同步helper增加enabled参数（独立底层调用默认true，生产编排显式传布尔），关时只检查并清空固定版本目标已有快照，不读取/遍历源目录、不复制CLI；无历史目标时不创建快照。清空仅限版本化.agentclaw与.local/bin，不能删除草稿、引擎或其他Home数据；继续采用既有安全rsync清理及符号链接校验。开启时保留原复制语义及build_ignore规则。关闭后同版本重试不得携带旧CLI内容。

## 日志
记录开关判定request/success/failure：bot_id、entity_id、env、user_list_type、enabled、匹配来源global/user/none/error、elapsed_ms；不记录凭据。两个helper关闭时skip/cleanup日志reason=feature_disabled，清理失败传播。

## 测试/Review
先写失败行为用例再实现。默认关闭、指定用户、全量*、不同env/type隔离、查询失败关闭；source损坏/软链时关闭不读取源；开→关同版本重试清理、其他Home及引擎数据保留、清理失败传播；全量开短路用户查询；build和DI实际传递；普通登录用户无法correct新类型而旧类型保持。更新必要手工构造器调用及已有迁移测试显式开启。执行相关构建、名单、DI、架构/ruff检查；独立review和回归。

## Ship
沿用用户已授权的远端dev PR流程，仅推送Avernet GitHub源码PR；本任务不涉及OCB运行态变更。发布前按名单开关使用说明评估，不预置*或用户名单。
