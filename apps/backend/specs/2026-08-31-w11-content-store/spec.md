# W11 — 平台侧物化与留存（#1510）

> 计划来源:`docs/bot-config-manifest/work-items.zh-CN.md` §5 W11、§2.7、§2.8。
> 分工归属:lucas-xzp(机件)。**依赖:W2**(产出被它存储的那个 fetcher 的输出形状)。**阻塞于:—**。
> 验收:#1510 为唯一权威;本 spec 把它的五条验收线映射到代码形状。

## 交付物

- `core/bot_config_manifest/content/`——内容存储包:
  - `service.py` + `service_protocol.py`:`store()`(FetchedObject→持久副本+溯源行)、
    `read()`(下发与审计共用的唯一读路径)、`records()`(审计查询)。
  - `models.py`:`StoredContentRecord`(业务记录)、`ManifestContentModel`(`ac_manifest_content`
    ORM,tenant guard 注册)、`ContentScope`(env/entity_id/bot_id)。
  - `errors.py`:错误词汇(缺失/损坏/配置类),消息里 URL 只出现去 query 形态。
  - `settings.py`:`content_store_root_from_config`——application.yaml
    `user_config.bot_config_manifest.content_store_dir` 的纯解析(中性默认
    `./data/manifest_content`;组合根装配是 W4 的事,core 不碰文件系统之外的世界)。
- `core/repository/protocols/bot/manifest_content.py` +
  `implementations/bot/manifest_content.py`(ORM 仓储,sync,`DatabasePlugin`),
  DI 仅绑定仓储 Protocol(service 的构造留给 W4 装配,与 W2 fetcher 同款"声明机件"边界)。
- `sql/2026_08_31_manifest_content.sql`——平台 DDL 约定形态,头注释写明全部存储决策。

## 存储形状(两个半件,一个机制)

- **字节块(blob)——文件系统,内容寻址。**`<root>/blobs/<hex[:2]>/<hex64>`;digest
  (`sha256:<hex>`,W2 的词汇)就是地址,同 digest 只写一次(幂等;临时文件+`os.replace`
  原子落位)。不进数据库:schema §5 单条上限 100–200 MiB,那是 BLOB 列的自毁形态
  (max_allowed_packet、行大小、备份全量放大);文件系统也是 skill_scan 等既有
  平台侧落盘的同一先例。
- **溯源行——`ac_manifest_content`,append-only 审计日志。**每次 store 一行:
  `(avernet_tenant, env, entity_id, bot_id)`(entity_id 是存储键不公开,同 W1)+
  digest + 来源两 URL + 凭证**名** + content_type + size + fetched_at + modifier。
  无唯一键——同一 digest 被再次拉取就是新的一行,这正是"何时从哪来"的审计事实。

## 关键验收 → 实现要点

| #1510 验收 | 实现 |
| --- | --- |
| 溯源持久化(来源/ref 或 digest/时间/字节) | 溯源行 + 不可变 blob;`store()` 输入就是 W2 `FetchedObject` |
| 下发读存储,重试绝不重拉 | `read(digest)` 是唯一读路径:流式读 blob 同 pass 校验 sha256,坏块吵闹失败;不存在 → 报缺失,绝不"顺手重取" |
| keep_last 同一机制 | keep_last = W4 apply 记录里的 digest + 本存储的 `read`;无第二套存储,无第二寻址 |
| 留存策略显式 | **v1 策式:行与字节一律不删。**依据审计需求(§2.8 对账要回答"当时收到的是什么")——留存期未定前,删除只会制造审计漏洞;清理机件显式不做、留到审计口径给出后(见 README) |
| 凭证绝不与内容持久化 | 列只有 `credential_name`(W3 的名字,upsert 轮换语义);blob 是纯字节;错误消息只带名;测试钉住"URL 有 query 也不落库" |

**两个脱敏决定,和 W2 同一口径:**
- **存储的 URL 带 path、不带 userinfo、不带 query。**userinfo 本就被 fetcher 拒绝;
  query 是签名源 token 所在(W2 日志只出 host 的同一理由)。对账锚是 digest,不是
  一次性的签名 URL。
- **存两条 URL:`source_url`(manifest 条目源,`${BOT_*}` 替换后)与 `fetched_url`
  (最终跳达;两者不同=发生了重定向,"从哪来"的两个事实都在)。**

## 范围外(W 显式不做)

- DI 里装配 service、接进 apply(编排属 W4——它决定 root 何时何地注入,fetcher/凭证/
  存储三件都同此边界);HTTP/公开面(§2.7 的 last-apply/审计读出属 W4/W8);
  git 源的 ref/SHA 列的写法属 W7(列为 varchar 可空,DDL 注释说明)。

## 执行记录(2026-08-31)

- 组件就位:`content/{service,service_protocol,models,errors,settings}.py`、
  `sql/2026_08_31_manifest_content.sql`、repository 协议/实现、schema.py 登记、
  DI 绑定仓储 Protocol(service 装配留给 W4)、application.yaml 键
  `bot_config_manifest.content_store_dir`(中性默认 `./data/manifest_content`)+ golden
  三快照重生、flow_coverage 条目扩至 cover W11、模块 README 增"The content store
  (W11)"节与 Context Boundary。
- 测试与门禁:详见 tasks.md 台账。

## 批量终审台账(2026-09-01 落回,7 findings 全闭环)

- **H1(高) 入库字符串宽度无校验**——`content_type` 是 wire 侧可控的 header(无长度
  上限),超长值会在 blob 落盘**之后**才 DB 失败:strict=500 类错误浮出且每次重试复现;
  非 strict=append-only 审计行被静默截断且永不可修复;SQLite 测试(varchar 不生效)
  结构上抓不到。**修**:`store()` 在 `_write_blob` 之前对五个入库字符串
  (source_url/fetched_url 脱敏后、credential_name/content_type/modifier)按列宽显式
  裁决,超长 `ContentStoreError`;测量对象=脱敏后形态(4000 字符签名 query 被丢弃后
  照常入库,有测试钉住这个分工)。schema 侧 PUT 期 URL 长度规则留给 W5/W7 真正消费
  URL 源时定(follow-up 记录在案)。
- **M1 写序**:URL 脱敏/校验先于 blob 落盘——拒写=盘上无痕成为不变量,补
  "无 blob 无行"断言;DB 插入失败留孤儿 blob 是内容寻址标准形态(地址有效、字节已
  验、下次同 digest 直接复用),docstring 写明并有测试。
- **M2 幂等捷径盲信已存在 blob**:改为尺寸核对(截断/追加类损坏近零成本抓住,手上
  正确字节顺带自愈重写);同尺寸位腐留给 read 吵闹——该取舍写进 docstring,不为
  最稀有损坏模式给每次 store 加 200MiB 复哈希。
- **M3 unparseable URL 错误消息回显全文**(可能含签名 query):消息只报长度,测试
  断言令牌不出现在异常文本里。
- **L1** records_for 排序对齐索引与协议文档(gmt_create desc, id tiebreak);
  **L3** digest 正则上移为 `fetch/limits.DIGEST_RE` 单一词汇源(guarded_fetcher 改
  引用)、`DEFAULT_RECORD_LIMIT` 归位协议、重复日志行删并、负数 limit 钳 0(SQLite
  LIMIT -1=无上限的方言陷阱);**L2** 测试补钉:userinfo/fragment 参数化、digest 三
  变体拒绝、空字节体回环、DB 失败孤儿 blob+自愈、截断 blob 自愈重写。
- 终审同时确认无问题的方向:ORM↔DDL 逐列零漂移(含索引预算算术)、read 绝不
  fetch、凭证面、tz 归一化、原子写、并发收敛、AGENTS 不可量(含 user_config AST
  门禁绕行)、tenant guard、README 双侧 provides/internal_dependencies 登记。
