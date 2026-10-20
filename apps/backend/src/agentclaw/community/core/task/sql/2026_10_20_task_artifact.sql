-- task_artifact: 产物领域对象(TaskArtifact)的持久化,本 spec §3 领域模型的表形态。
-- append-only 不可变清单:仓储不提供任何 UPDATE 路径;内容修订(未来 supersedes)以新行表达。
-- 写入由 save_graph 事务内嵌完成(同 TaskCallbackModel 先例);读侧经 TaskArtifactRepositoryProtocol。
-- 无外键(与 task_trajectory 同款约束:task 域表间不建 FK)。
-- 幂等(无唯一键,用户定稿):select-first 同事务查重(spec §5.3),查重路径由 idx_dedupe 支撑;
-- 同任务写入经 task_info 行 FOR UPDATE 预锁串行化,残余竞态由到达层事件级幂等抑制。
-- 索引语义(不带索引级 COMMENT——内部 SQL 审核解析器不识别,且 trajectory 同款无索引注释先例;
-- 语义收拢于此):idx_artifact_id=按产物 ID 取行(唯一性由生成器 uuid4 保证,不由索引锁定);
-- idx_dedupe=幂等查重键,最左前缀同时服务 task_id / (task_id,node_id) 检索;
-- idx_scope_session=按会话列产物+时间序;idx_task_kind=任务内按形态过滤+时间序;
-- idx_task_name=任务内按业务名检索(LIKE 'x%' 前缀走索引,'%x%' 中缀不走)。
-- 可空性统一以裸 NULL 声明(无 DEFAULT NULL,用户定稿):content 为 TEXT 家族禁显式 DEFAULT,
-- 其余可空列与隐式行为等价,统一写法消除混排。
-- 除 artifact_name/scope_session_id/storage(真可空:未提供语义)外,node_id/attempt/
-- content_hash/creator/modifier 为"列可空、发布必填":NULL 仅为 schema 容错/人工修数留口;
-- task_id/content(连同 id/artifact_id/kind 与两条 time 房型列)为列级 NOT NULL 强制:
-- task_id 是主检索维度与 idx_dedupe 首列、产物本体 content 亦不容悬空。
-- "列可空、发布必填"组的 node_id/attempt/content_hash 任一为 NULL 时,select-first 查重
-- 失配(SQL NULL 不参与等值比较)——空值行回放即可能重复;故领域构造器与发布校验(§3/§4)
-- 不放宽,仍强制 task_id+node_id+attempt+content+hash+creator。
-- storage 为存储介质元信息列(用户定稿,置于 content 前,可空、varchar(2048)):非 file 分支持恒为 inline,
-- file 分支必须与 FileContent.storage_type 同值——由构造器从 content 派生并强校验,不构成第二首领;
-- 发布路径必填,NULL 仅为容错/人工修数留口;2048 长度为介质元信息 JSON 化预留(当前只放介质名)。
-- 查询价值:inline 粗筛"内容自带"产物、外部介质产物分开运营,不用反序列化 content。
-- creator/modifier/gmt_create/gmt_modify 为表结构房型列(用户定稿):行不可变,
--   modifier 恒等于 creator、gmt_modify 恒等于 gmt_create,列保留纯为 schema 惯例兼容;
--   gmt_create 由仓储以领域 created_at(ms)同事务双写,DB DEFAULT 仅为 fallback(trajectory 同款惯例)。
-- 列名沿用已部署表的实际名 gmt_modify(非 gmt_modified,trajectory 部署形态同款命名)。
-- content 取 MEDIUMTEXT(16MB) 而非 TEXT(64KB):TEXT 分支装 bot 全文交付物(报表类轻松超 64KB),
-- 且 relay 上报的 text_content 不经 task_node_run_info.output(TEXT 档)没有天然截断保证;
-- 变长按需占用,存 1KB 只占 1KB,不为此预付代价。
CREATE TABLE IF NOT EXISTS `task_artifact` (
    `id`              bigint(20)    NOT NULL AUTO_INCREMENT                       COMMENT '主键ID',
    `artifact_id`     varchar(32)   NOT NULL                                      COMMENT '产物 ID(task-art-+uuid4hex16;唯一性由生成器保证,非索引锁定)',
    `artifact_name`   varchar(255)  NULL                                          COMMENT '业务展示名(NULL=未提供;file 产物发布时回落 file_name)',
    `task_id`         varchar(128)  NOT NULL                                      COMMENT '归属任务 ID(与报文定位交叉校验后落库;列级强制非空——主检索维度与 idx_dedupe 首列不许悬空)',
    `node_id`         varchar(128)  NULL                                         COMMENT '产出节点 ID(发布路径必填,NULL 仅为容错/人工修数留口;同 task_id 的空值豁免后果)',
    `attempt`         int           NULL                                         COMMENT '节点 harness_retries 快照(0 起;与报文交叉校验;发布路径必填,NULL 仅为容错——空值行使 select-first 查重失配)',
    `scope_session_id` varchar(512) NULL                                         COMMENT '图绑定协作会话 ID(图绑定优先,未绑定采纳上报值;NULL=均无)',
    `kind`            varchar(64)   NOT NULL                                      COMMENT '内容形态(text|file|url_link|code),governed 枚举',
    `storage`         varchar(2048) NULL                                         COMMENT '存储介质元信息:inline=内容本体在 content 列(text/url_link/code);bot_workspace|session_file=字节在外部介质,content 仅为引用(与 FileContent.storage_type 强一致,构造器推导校验;发布路径必填,NULL 仅为容错/人工修数留口,长度冗余预留给介质 JSON 化)',
    `content`         mediumtext    NOT NULL                                      COMMENT '内容分支 JSON(kind-tagged,tag=分支判别;不含字节/临时凭证/存储 handle;产物本体,列级强制非空;TEXT 家族禁显式 DEFAULT',
    `content_hash`    char(64)      NULL                                         COMMENT 'sha256(canonical_json(content)),idx_dedupe 幂等查重成分;发布路径必填——NULL 使查重失配',
    `creator`         varchar(128)  NULL                                         COMMENT '产出者(bot_id/group_id;system=系统写入,如控制信号;NULL=未记录)',
    `modifier`        varchar(128)  NULL                                         COMMENT '修改者(行不可变:恒等于 creator,房型惯例列;NULL=未记录)',
    `gmt_create`      timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP            COMMENT '发布时间(仓储以领域 created_at 毫秒值双写,DB 默认仅 fallback)',
    `gmt_modify`      timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后修改时间(行不可变:恒等于 gmt_create,房型惯例列)',
    PRIMARY KEY (`id`),
    KEY `idx_dedupe` (`task_id`, `node_id`, `attempt`, `kind`, `content_hash`),
    KEY `idx_artifact_id` (`artifact_id`),
    KEY `idx_scope_session` (`scope_session_id`, `gmt_create`),
    KEY `idx_task_kind` (`task_id`, `kind`, `gmt_create`),
    KEY `idx_task_name` (`task_id`, `artifact_name`)
) DEFAULT CHARSET = utf8mb4 COMMENT='task 产物不可变清单(spec 2026-10-20-task-artifact-domain)';