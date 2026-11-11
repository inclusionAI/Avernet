# Product domain vocabulary

本词汇表为产品设计、契约、代码、数据迁移和评审提供中英文对照的共享领域语言。

This glossary defines shared domain language for product design, contracts,
code, data migrations and reviews. Keep stable concepts and semantic boundaries
here; put module-specific behavior and implementation details in the owning
module. Do not use a glossary term as evidence of a specific deployment.

## Skills

**技能（Skill）**:
跨越多个版本、拥有稳定身份的可复用能力。技能是用户管理、下游消费者引用的对象。
_避免使用_: 用“技能”表示某一条数据库记录、某一个文件包或某一个版本

**English:** A reusable capability with a stable identity across versions, managed by users and referenced by downstream consumers. Avoid using it for a single database row, package or version.

**技能版本（Skill Version）**:
归属于且仅归属于一个技能的不可变已发布内容快照。同一技能的不同版本是前后继版本，不是不同技能。
_避免使用_: 技能、版本记录

**English:** An immutable published content snapshot belonging to exactly one Skill. Versions of one Skill are successive versions, not separate Skills. Avoid: Skill, version record.

**版本序号（Version Ordinal）**:
同一技能内用于排列成功发布版本的正整数，从 1 开始单调递增；草稿放弃、驳回或技术失败不会消耗版本序号。产品将序号 `n` 展示为 `Vn`；版本序号不是版本记录主键，也不是技能中心或 Skill Repo 的外部版本键。
_避免使用_: 十进制版本号、语义化版本、外部版本号

**English:** A positive, monotonically increasing number ordering successful publications of one Skill, starting at 1 and displayed as `Vn`. Abandoned, rejected or technically failed drafts do not consume it. It is neither the version row primary key nor the external version key. Avoid: decimal version, semantic version, external version key.

**目标版本序号（Target Version Ordinal）**:
活动技能草稿创建时确定的下一已发布版本候选序号。它允许草稿稳定展示为 `Vn`，但在发布成功前不代表已存在正式技能版本；未发布成功的草稿被放弃后，该候选序号可以由后续草稿继续使用。
_避免使用_: 技能版本 ID、已发布版本

**English:** The next published-version candidate fixed when an active draft is created, allowing a stable `Vn` display. It is not a published version before success and may be reused after an unsuccessful draft is abandoned. Avoid: version ID, published version.

**技能草稿（Skill Draft）**:
某个技能当前可变的工作副本，用于准备首次发布或下一次升级；同一环境下一个技能同时最多只有一个活动技能草稿。技能草稿不是已发布技能版本，发布成功后才形成不可变版本。
_避免使用_: 草稿版本、开发中版本

**English:** The current mutable working copy used for initial publication or an upgrade. At most one active draft per Skill exists in an environment. It becomes an immutable published version only after publication succeeds. Avoid: draft version, in-development version.

**发布尝试（Publication Attempt）**:
对某份冻结发布内容执行自动规范校验、安全扫描和外部发布的一次持久化过程记录。相同幂等键的请求重发和瞬时技术故障的自动重试属于同一次发布尝试；终态失败后用户主动再次发布形成新的发布尝试，但不会因此生成多个业务版本。
_避免使用_: 人工审核、技能版本、技能状态

**English:** A persistent record of one execution of automated standards checks, security scanning and external publication for frozen content. Resends with the same idempotency key and automatic retries of transient failures belong to the same attempt; an explicit retry after terminal failure creates a new attempt, not multiple business versions. Avoid: manual review, Skill Version, Skill status.

**发布内容快照（Publication Snapshot）**:
启动发布操作时从技能草稿冻结的不可变内容及其 Hash。自动规范校验、安全扫描、外部发布和最终技能版本必须使用同一份发布内容快照。
_避免使用_: 可变草稿、已发布版本

**English:** The immutable content and hash frozen from a draft when publication starts. Automated checks, security scanning, external publication and the resulting Skill Version must use this same snapshot. Avoid: mutable draft, published version.

**已发布技能版本（Published Skill Version）**:
已经可供下游选择的技能版本。“已发布”描述可用性，不表示该技能正在实际执行。
_避免使用_: 运行中技能、活跃技能

**English:** A version available for downstream selection. Published describes availability, not whether it is currently executing. Avoid: running Skill, active Skill.

**技能升级（Skill Upgrade）**:
为现有技能创建并发布后继技能版本的过程。升级不会改变技能身份。
_避免使用_: 克隆技能、创建新技能

**English:** Creating and publishing a successor version of an existing Skill without changing its identity. Avoid: cloning a Skill, creating a new Skill.

**技能来源（Skill Source）**:
技能内容的取得渠道，例如文件上传、Git 快照或外部市场。技能来源与技能的所有权、协作范围相互独立。
_避免使用_: 空间、归属范围

**English:** A channel from which content is acquired, such as upload, Git snapshot or external market. It is independent of ownership and collaboration scope. Avoid: Space, ownership scope.

**来源快照（Source Snapshot）**:
从上传内容或 Git 仓库取得并保存的一次不可变内容快照。Git 手动更新始终跟随首次导入时的同一分支，只在完整下载和校验成功后替换技能草稿，不会修改已经发布的技能版本。
_避免使用_: Git 实时同步、仓库镜像

**English:** An immutable snapshot acquired and stored from an upload or Git repository. A manual Git update follows the same branch as the initial import and replaces the draft only after complete download and validation; published versions remain unchanged. Avoid: live Git sync, repository mirror.

**技能内容元数据（Skill Content Metadata）**:
定义在 `SKILL.md` frontmatter 中的技能名称和描述。当前草稿的 `SKILL.md` 是名称和描述的唯一事实来源；已发布技能版本保存发布时冻结的元数据快照。数据库列表字段和搜索索引只能是投影，不能成为独立写入源。
_避免使用_: 独立基本信息、主表名称、卡片描述

**English:** The name and description in `SKILL.md` frontmatter. The current draft's `SKILL.md` is their sole source of truth; a published version stores a frozen snapshot. Database list fields and search indexes are projections, not independent write sources. Avoid: independent basic information, primary-table name, card description.

**历史技能身份（Historical Skill Identity）**:
曾经分配过的技能身份；即使可见的技能记录已经不存在，相关引用、权限、审计记录或外部产物仍可能保留。
_避免使用_: 无用 UUID、已删除记录

**English:** An identity previously assigned to a Skill, whose references, grants, audit records or external artifacts may remain even if the visible Skill record is gone. Avoid: unused UUID, deleted row.

## Spaces and collaboration

**空间（Space）**:
管理技能所有权与协作关系的边界。空间不是技能内容的来源。
_避免使用_: 仓库、来源、Skill Center 团队

**English:** The boundary for Skill ownership and collaboration, not a content source. Avoid: repository, source, Skill Center team.

**技能空间归属（Skill Space Ownership）**:
一个受空间管理的技能归属于某个空间的唯一事实。个人空间和团队空间复用同一种归属关系；同一环境下一个技能最多只有一条空间归属。历史未迁入空间的技能可以暂时没有该关系。
_避免使用_: 普通多对多绑定、Skill 类型、`scope_type`

**English:** The single fact associating a managed Skill with a Space. Personal and Team Spaces use the same relationship; at most one ownership relationship exists per Skill per environment. Legacy Skills not yet migrated may temporarily lack one. Avoid: ordinary many-to-many binding, Skill type, `scope_type`.

**个人空间（Personal Space）**:
以单个用户的所有权和协作边界为中心的空间。
_避免使用_: 私有技能、本地技能

**English:** A Space centered on one user's ownership and collaboration boundary. Avoid: private Skill, local Skill.

**团队空间（Team Space）**:
由一个团队共享协作边界的空间。
_避免使用_: 共享技能、Skill Center 团队

**English:** A Space with a shared team collaboration boundary. Avoid: shared Skill, Skill Center team.

**跨空间技能引用（Cross-space Skill Reference）**:
另一个空间以只读方式使用某个技能的关系，不改变该技能的空间归属。把内容复制到另一个空间会创建新的技能身份，不属于跨空间引用。
_避免使用_: 多空间归属、共享所有权

**English:** A read-only relationship allowing another Space to use a Skill without changing ownership. Copying content creates a new Skill identity and is not a cross-space reference. Avoid: multiple ownership, shared ownership.

**空间成员关系（Space Membership）**:
用户在空间中拥有某种角色的关系。空间成员关系本身不表示用户拥有某个具体技能的管理权或编辑权。
_避免使用_: 技能权限、技能成员

**English:** A user's role in a Space; membership alone does not confer management or editing rights over an individual Skill. Avoid: Skill grant, Skill member.

**空间管理员（Space Administrator）**:
通过空间成员关系获得空间成员治理权限的人。空间创建者在创建时成为首位空间管理员，但创建行为只形成审计事实，不赋予永久特权；空间管理员不会自动成为空间内所有技能的管理员或编辑者。
_避免使用_: 空间创建者、技能管理员、所有 Skill 的 Owner

**English:** A member with Space membership governance authority. The creator becomes the first administrator at creation; creation is an audit fact, not a permanent privilege. Administrators do not automatically manage or edit every Skill. Avoid: Space creator, Skill manager, owner of every Skill.

**技能授权（Skill Grant）**:
独立于空间成员关系、授予某个用户的技能级权限。当前只允许 `OWNER` 与 `MANAGER` 两种角色；普通空间成员没有技能授权。
_避免使用_: 空间角色、成员

**English:** Skill-level permission granted independently of Space Membership. Only `OWNER` and `MANAGER` roles are supported; ordinary Space members have no Skill Grant. Avoid: Space role, member.

**技能负责人（Skill Owner）**:
对某个技能最终负责并管理其 Managers 的人。创建技能的用户默认成为 Owner；每个技能在正常状态下有且仅有一个 Owner，离开所属空间前必须转移所有权。Skill Owner 不同于 Space Administrator。
_避免使用_: 技能管理员、空间所有者、团队所有者

**English:** The person ultimately responsible for a Skill and its Managers. Its creator is the default Owner; normally each Skill has exactly one Owner, and ownership must be transferred before leaving the owning Space. This is distinct from Space Administrator. Avoid: Skill manager, Space owner, team owner.

**技能协作者（Skill Manager）**:
由 Skill Owner 授予操作权限的人。一个技能可以有多个 Managers；Manager 可以编辑并执行产品允许的技能操作，但不能管理 Managers 或转移 Owner。
_避免使用_: Skill Editor、技能成员、普通空间成员

**English:** A person granted operational rights by the Skill Owner. A Skill can have multiple Managers who may edit and perform allowed operations, but cannot manage Managers or transfer ownership. Avoid: Skill Editor, Skill member, ordinary Space member.

**编辑租约（Edit Lease）**:
某个用户在有限时间内独占编辑团队空间技能草稿的权利。它用于协调并发编辑和阻止被抢占页面的旧写入，不是永久权限，也不替代技能授权；个人空间技能不使用编辑租约。
_避免使用_: 技能权限、永久锁

**English:** A time-limited exclusive right to edit a Team Space Skill draft. It coordinates concurrent editing and prevents stale writes after takeover; it is neither permanent permission nor a substitute for Skill Grants. Personal Space Skills do not use it. Avoid: Skill permission, permanent lock.

## Bot consumption

**技能类型（Skill Type）**:
技能在产品中的内容管理与交付类别，当前包括本地技能、Repo 技能与空间技能。技能类型不表示技能通过直接安装还是技能集进入 Bot。
_避免使用_: 激活来源、安装方式、技能范围

**English:** The content-management and delivery category: local, Repo or Space Skill. It does not specify direct installation versus Skill Set activation. Avoid: activation source, installation method, Skill scope.

**技能引用（Skill Reference）**:
对可被 Bot 消费的技能建立的稳定引用目标。空间技能使用 OCB Skill Identity；外部市场技能使用“来源系统 + 外部稳定标识”。技能引用不包含固定版本。
_避免使用_: 版本引用、本地技能副本

**English:** A stable reference target consumable by a Bot. Space Skills use OCB Skill Identity; external market Skills use source system plus external stable identifier. It contains no fixed version. Avoid: version reference, local Skill copy.

**技能集（Skill Set）**:
用于组织多个技能引用、并可由 Bot 整体激活的具名集合。技能集是可选的消费侧组织方式，不是技能进入 Bot 的必经路径，也不是技能的所有权边界。
_避免使用_: 能力集、技能、空间

**English:** A named optional collection of Skill References that a Bot can activate together. It is neither mandatory for consumption nor an ownership boundary. Avoid: capability set, Skill, Space.

**技能集成员关系（Skill Set Membership）**:
某个技能引用属于某个技能集的关系。它只表达集合内容，不表示任何 Bot 已经激活或实际获得该技能。
_避免使用_: 直接技能安装、Bot 技能绑定

**English:** The relationship placing a Skill Reference in a Skill Set. It does not mean a Bot activated or obtained the Skill. Avoid: direct installation, Bot Skill Binding.

**技能集激活（Skill Set Activation）**:
某个 Bot 选择启用一个技能集的关系。被激活技能集中的技能会成为该 Bot 有效技能集合的一种来源。
_避免使用_: 技能激活、直接技能安装

**English:** A Bot's choice to enable a Skill Set; its members become one source of the Bot's Effective Skill Set. Avoid: individual Skill activation, direct installation.

**直接技能安装（Direct Skill Installation）**:
某个 Bot 不经技能集而直接选择并启用一个技能引用的关系。该关系与技能类型无关；本地技能、Repo 技能与空间技能均可被直接安装。
_避免使用_: 默认技能集、技能集成员关系、Bot 本地技能

**English:** A Bot enabling a Skill Reference without a Skill Set. Local, Repo and Space Skills can all be installed directly. Avoid: default Skill Set, membership, Bot-local Skill.

**有效技能集合（Effective Skill Set）**:
某个 Bot 当前应获得的技能引用集合，由已启用的直接技能安装与已激活技能集中的成员取并集并按技能身份去重。它描述期望配置，不表示运行时推送已经成功。
_避免使用_: 单一技能集、运行时实际状态、Default SkillSet

**English:** The desired union of enabled direct installations and activated Skill Set members, deduplicated by Skill identity. It does not prove successful runtime delivery. Avoid: single Skill Set, observed runtime state, Default SkillSet.

**Bot 技能绑定（Bot Skill Binding）**:
表示 Bot 通过直接技能安装或已激活技能集持续依赖某个市场或空间技能引用的关系。绑定始终跟随该引用目标最新的合格已发布版本，Bot 不拥有对引用内容的编辑权。
_避免使用_: 复制技能、技能归属

**English:** A Bot's continuing dependency on a market or Space Skill Reference through direct installation or an activated set. It tracks the latest eligible published version; the Bot cannot edit the referenced content. Avoid: copied Skill, Skill ownership.

**Bot 本地技能（Bot-local Skill）**:
直接上传到单个 Bot、且不持续引用受管理技能及其版本的技能资产。它只属于该 Bot；它进入 Bot 的消费关系仍称为直接技能安装。
_避免使用_: 直接技能安装、空间技能、Bot 技能绑定

**English:** A Skill asset uploaded directly to one Bot without a continuing reference to a managed Skill and its versions. It belongs only to that Bot and is consumed through Direct Skill Installation. Avoid: direct installation, Space Skill, Bot Skill Binding.

**包级删除（Package Deletion）**:
以一次运行时操作删除一个 Bot 本地技能的完整内容根。它不表示技能元数据与运行时文件属于同一个原子事务。
_避免使用_: 逐文件删除、跨存储原子删除

**English:** Removing the complete content root of a Bot-local Skill in one runtime operation. It does not imply an atomic transaction between metadata and runtime files. Avoid: per-file deletion, cross-storage atomic deletion.

**跟随最新版本（Track Latest）**:
将市场或空间技能的 Bot 技能绑定和 Bot 草稿运行态解析到最新一个符合条件的已发布技能版本的固定规则。它不修改已经发布的服务 Bot Artifact。
_避免使用_: 自动复制

**English:** The fixed rule resolving market/Space Skill Bot bindings and draft runtimes to the latest eligible published version. It does not modify a published Service Bot Artifact. Avoid: automatic copying.

**绑定解析结果（Binding Resolution）**:
某个 Bot 技能绑定在草稿态被解析出的目标技能版本，以及该版本是否已成功进入草稿容器的可观测结果。它不是用户选择的固定版本策略。
_避免使用_: Bot 绑定版本、发布状态

**English:** The target Skill version resolved for a Bot binding in draft state and the observable result of whether it reached the draft container. It is not a user-selected fixed-version policy. Avoid: Bot binding version, publication status.

**Bot 草稿运行态（Bot Draft Runtime）**:
Bot 当前可编辑配置对应的依赖解析和预览容器状态。Skill 升级会推进这里的目标版本并触发容器更新，但不会修改已经发布的服务 Bot Artifact。
_避免使用_: 已发布 Bot、生产发布物

**English:** Dependency resolution and preview-container state for a Bot's editable configuration. Skill upgrades advance its target version and trigger container updates without modifying published Service Bot Artifacts. Avoid: published Bot, production artifact.

**服务 Bot 发布物（Service Bot Artifact）**:
某次服务 Bot 发布从 Bot 草稿态生成的不可变运行时制品。发布物至少固化每个技能依赖的 `skill_uuid + 外部版本键`；同一发布物的多实例部署、重启和回滚不会重新解析最新版本，也不参与后续 Skill 升级的草稿容器推送。
_避免使用_: Bot 当前配置、动态 latest 引用

**English:** An immutable runtime artifact produced by a service Bot publication from its draft. It freezes at least each dependency's `skill_uuid + external version key`; deployment to multiple instances, restart and rollback do not resolve latest again or participate in later draft pushes. Avoid: current Bot configuration, dynamic latest reference.

**Skills Pool 准入策略（Skills Pool Admission Policy）**:
决定尚未认领 Pool 布局的 Bot 是否可以首次 claim 的环境级策略。规则只包含精确 Bot、Owner + Engine、Environment + Engine 和精确 Bot exclusion；策略不负责迁移执行、恢复或回滚。
_避免使用_: Batch、验收批次、迁移状态

**English:** An environment-level policy governing the first Pool layout claim by an unclaimed Bot. Rules cover exact Bot, Owner + Engine, Environment + Engine and exact Bot exclusions; it does not execute migration, recovery or rollback. Avoid: Batch, acceptance batch, migration status.

**Engine 准入开关（Engine Admission Switch）**:
首次 claim 前按 Engine 评估的运维保险丝，优先于所有 allow 规则。关闭只阻止该 Engine 的新 claim，不取消、暂停或回滚已经 claim 的 Bot。
_避免使用_: 停止运行中 Bot、自动回滚、Batch close

**English:** An operational fuse evaluated per Engine type before first claim, ahead of all allow rules. Disabling it only blocks new claims for that Engine; it does not cancel, pause or roll back existing claims. Avoid: stopping running Bots, automatic rollback, Batch close.

**策略修订号（Policy Revision）**:
一次 Skills Pool Admission Policy 原子变更的不可变标识。写请求使用上一 revision 做 CAS；Bot 首次 claim 冻结命中的 revision 和 admission reason，用于观测而不构成后续放量门禁。
_避免使用_: Accepted Batch、Promotion Ready

**English:** An immutable identifier for an atomic Skills Pool Admission Policy change. Writes use the previous revision for CAS. First claim freezes the matched revision and admission reason for observation, not as a later rollout gate. Avoid: Accepted Batch, Promotion Ready.

**运行时技能依赖（Runtime Skill Dependency）**:
服务 Bot 发布物中记录的、已经从 Skill Reference 解析出的具体技能版本。它是某次发布的确定性输入，不等同于控制面的 Track Latest 绑定策略。
_避免使用_: Bot 技能绑定、固定版本策略

**English:** A specific Skill Version resolved from a Skill Reference and recorded in a Service Bot Artifact. It is a deterministic publication input, distinct from the control-plane Track Latest binding policy. Avoid: Bot Skill Binding, fixed-version policy.

## Skills runtime layout

**Pool-native Bot Layout**:
Bot 创建时即以 Pool 作为唯一技能内容权威的运行时布局。它不曾以 Legacy 作为内容权威，也不经过 Legacy 到 Pool 的迁移切换。
_避免使用_: 新 Bot Cutover、空 Legacy 迁移

**English:** A Bot runtime layout that uses Pool as its sole Skill content authority from creation and never undergoes a Legacy-to-Pool cutover. Avoid: new Bot cutover, empty Legacy migration.

**Legacy Layout Migration**:
将创建时使用 Legacy 技能内容权威的既有 Bot 前滚到 Pool 的布局迁移。它只适用于存量 Legacy Bot，不适用于以 Pool-native 布局创建的新 Bot。
_避免使用_: Pool-native 初始化、新 Bot Cutover

**English:** Forward migration to Pool of an existing Bot created with Legacy as its Skill content authority. It applies to existing Legacy Bots, not Pool-native Bots. Avoid: Pool-native initialization, new Bot cutover.

## Lifecycle

**Bot 回收（Bot Recycling）**:
由个人 Bot Owner 主动停止 Bot 的平台托管运行资源，并将 Bot 转入 `RECYCLED` 状态的生命周期操作。Bot 回收不同于平台基于活动度执行的沉寂治理；当前仅适用于由平台管理运行资源的 personal Bot，不适用于 desktop、service 或 Teclaw Bot。
_避免使用_: Bot 删除、Bot 重启、沉寂扫描

**English:** A personal Bot Owner's action to stop platform-managed runtime resources and move the Bot to `RECYCLED`. It differs from activity-based dormant governance and currently applies only to personal Bots with platform-managed resources, not desktop, service or Teclaw Bots. Avoid: deletion, restart, dormant scan.

**Bot 重新激活（Bot Reactivation）**:
由个人 Bot Owner 将 `RECYCLED` Bot 恢复到可运行状态的异步生命周期操作。接口接受操作后，调用方必须查询 Bot 状态，直到 Bot 达到 `ACTIVE` 且运行时就绪，或进入失败状态。
_避免使用_: Bot 重启、许可证单独上线、同步激活

**English:** An asynchronous Owner operation restoring a `RECYCLED` personal Bot. After acceptance, the caller must query status until `ACTIVE` and runtime ready, or failure. Avoid: restart, license-only activation, synchronous activation.

**沉寂治理（Dormant Governance）**:
平台依据 Bot 活动度、预警冷静期及保护策略自动决定是否预警和回收的治理流程。沉寂治理受 Bot 白名单和 Owner 保护名单约束；Owner 主动发起的 Bot 回收不属于沉寂治理，也不受这些保护配置阻断。
_避免使用_: Owner 主动回收、单 Bot 运维回收

**English:** A platform process deciding warnings and recycling based on activity, warning cool-down and protection policies. It respects Bot and Owner protection lists; Owner-initiated recycling is outside this process and is not blocked by those protections. Avoid: Owner-initiated or single-Bot operational recycling.

**取消技能创建（Skill Creation Cancellation）**:
删除一个从未提交发布、没有发布尝试、外部映射、已发布版本或历史引用的纯草稿技能。它表示本次创建从未形成可保留的对外历史，是普通流程中唯一允许物理移除技能身份的场景。
_避免使用_: 删除已发布技能、技能退役

**English:** Deleting a pure draft Skill that has never submitted publication and has no publication attempts, external mappings, published versions or historical references. This is the only ordinary-flow case allowing physical removal of Skill identity. Avoid: published Skill deletion, retirement.

**技能身份墓碑（Skill Identity Tombstone）**:
技能形成发布尝试、外部映射、已发布版本或历史引用后，即使整体退役也必须保留的最小身份记录。墓碑用于维系历史引用、审计和 UUID 不复用，不表示技能仍可被选择。
_避免使用_: 可用技能、历史版本

**English:** The minimum identity record retained after a Skill has publication attempts, external mappings, published versions or historical references, even after retirement. It preserves references, audit and UUID non-reuse, not availability. Avoid: available Skill, historical version.

**历史技能版本（Historical Skill Version）**:
因后继版本发布而不再是当前最新版本、但内容和精确版本寻址仍被保留的已发布版本。历史版本继续服务既有 Service Bot Artifact，不等同于下线或删除。
_避免使用_: 下线版本、已删除版本

**English:** A published version no longer latest because a successor was published, whose content and exact version addressing remain available for existing Service Bot Artifacts. It is not withdrawn or deleted. Avoid: withdrawn or deleted version.

**版本下线（Version Withdrawal）**:
停止让下游继续选择某一个已发布技能版本，同时保留技能本身及其历史。只要仍存在有效 Bot Binding 或 Service Bot Artifact 引用，该操作就必须被阻断。
_避免使用_: 删除技能、笼统使用“下线”

**English:** Stopping downstream selection of one published version while retaining the Skill and history. It must be blocked while any active Bot Binding or Service Bot Artifact references that version. Avoid: Skill deletion, ambiguous withdrawal.

**技能整体退役（Skill Retirement）**:
终止技能整体的可用性，并阻止下游新建对该技能的选择关系。技能整体退役不同于删除数据记录，也不同于只下线某一个版本。
_避免使用_: 删除、笼统使用“下线”

**English:** Ending availability of an entire Skill and preventing new downstream selections. It differs from deleting records and from withdrawing one version. Avoid: deletion, ambiguous withdrawal.

## Market and favourites

**市场资源（Market Resource）**:
能力市场中可发现的技能条目，可能来自 Skill Center、Git 同步市场或 OCB 空间。市场资源是发现侧投影，不天然等同于 OCB 管理的技能主体。
_避免使用_: 技能主表、空间技能

**English:** A discoverable market Skill entry, potentially from Skill Center, a Git-synchronized market or an OCB Space. It is a discovery projection, not necessarily an OCB-managed Skill entity. Avoid: Skill primary table, Space Skill.

**空间收藏（Space Favourite）**:
某个空间对市场资源建立的快捷访问关系。收藏不复制内容、不转移所有权，也不会让 Bot 自动获得该技能。
_避免使用_: 安装、绑定、导入

**English:** A Space's shortcut relationship to a Market Resource. It does not copy content, transfer ownership or automatically give the Skill to a Bot. Avoid: installation, binding, import.

## External publication

**技能中心（Skill Center）**:
用于发布和分发技能内容的外部系统。技能中心不是 OCB 空间成员关系或技能授权的权威来源。
_避免使用_: 空间、能力集

**English:** An external system for publishing and distributing Skill content, not the authority for OCB Space Membership or Skill Grants. Avoid: Space, capability set.

**外部技能映射（External Skill Mapping）**:
一个 OCB 技能与其在技能中心中的对应身份之间的显式关系。该关系不能根据技能名称推导。
_避免使用_: 技能名称、来源定位符

**English:** An explicit relationship between an OCB Skill and its Skill Center identity; it cannot be inferred from the name. Avoid: Skill name, source locator.

**外部版本键（External Version Key）**:
技能中心或 Skill Repo 用于精确寻址某个已发布技能版本的不可变字符串，例如 `1.0.0`。它通过外部技能映射关联到 OCB 技能版本，不参与 OCB 内部版本排序，也不能充当 OCB 版本身份。
_避免使用_: 版本序号、展示版本、latest

**English:** An immutable string used by Skill Center or Skill Repo to address a published version, such as `1.0.0`. It connects through External Skill Mapping to an OCB Skill Version; it neither orders OCB versions nor serves as their identity. Avoid: Version Ordinal, displayed version, latest.

## Manifest MCP configuration

**Bot MCP 覆盖配置（Bot MCP Override）**:
通过 Manifest 为一个 Bot 和一个 MCP 显式声明的连接配置。它只保存覆盖字段，不复制用户配置或 MCP Center 元数据。
_避免使用_: 用户 MCP 配置、最终运行时配置

**English:** A Manifest-declared connection override for one Bot and MCP. It stores only overridden fields, without copying user configuration or MCP Center metadata. Avoid: user MCP configuration, final runtime configuration.

**有效 MCP 配置（Effective MCP Configuration）**:
针对一个 Bot 和一个 MCP，将 Bot 覆盖、用户配置、MCP Center 元数据及平台规则解析后得到的运行时连接配置。
_避免使用_: Manifest 原文、MCP 安装关系

**English:** Runtime connection configuration resolved for one Bot and MCP from the Bot override, user configuration, MCP Center metadata and platform rules. Avoid: raw Manifest, MCP installation relationship.

**Manifest Direct claim**:
Manifest Apply 将显式 Skill 或 MCP 转换后的 Bot 级直接声明。普通 SkillSet membership 会被移除；Default/platform 供应以 exclusion 抑制，并与 Installation 共同表达 Direct 状态。该状态由现有事实推断，不是持久化 provenance。
_避免使用_: managed-by-Manifest、Manifest 持续所有权

**English:** A Bot-level direct claim produced by Manifest Apply from an explicit Skill or MCP. Ordinary SkillSet membership is removed; Default/platform supply is suppressed by exclusion, with Direct state expressed together with Installation. This state is inferred from existing facts, not persistent provenance. Avoid: managed-by-Manifest, continuing Manifest ownership.

## Backend runtime vocabulary

**运行绑定（Runtime binding）**:
可信 Bot 请求选定的既有绑定，与技能绑定不同。解析返回绑定身份和来源，不负责创建运行实例或选择物理设备。

**English:** The existing association selected for a trusted Bot request, distinct from a Skill binding. Resolution returns a binding identity and source; it does not provision or select a physical device. See the [runtime-binding boundary](apps/backend/src/agentclaw/community/core/runtime_binding/README.md).

**运行阶段（Runtime stage）**:
所属接口定义的 draft、verify 或 online 运行目标，与部署环境不同。

**English:** The draft, verify or online target defined by the owning API and [engine stage contract](apps/backend/src/agentclaw/community/core/engine_runtime/stage.py). Stage is separate from deployment environment.

**运行设备目标（Runtime device target）**:
已解析运行态中的 provider 特定目标。对于 Cron，device UUID 可选择一个实例；Bot ID 或 binding ID 本身不是该 UUID。

**English:** A provider-specific destination within a resolved runtime. For Cron, a device UUID can select one instance; a Bot ID or binding ID alone is not that UUID. See the [Cron contract](apps/backend/src/agentclaw/community/core/cron/README.md).
