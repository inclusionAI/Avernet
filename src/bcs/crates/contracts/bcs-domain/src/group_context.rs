//! Group Context 纯领域类型。
//!
//! 对应 `specs/2026-09-09-group-context-api/spec.md` §2、`plan.md` §3.3。
//! 本文件只放 **数据结构**(struct / enum),不放 trait、不写 async、不碰数据库。
//!
//! ╭── 给 Java 出身读者的 Rust 速记 ────────────────────────────────────────╮
//! │ #[derive(Debug, Clone, Serialize, Deserialize)]                          │
//! │   ≈ Lombok 的 @Data + Jackson 的可序列化注解。derive 让编译器              │
//! │   自动生成这些 trait 的实现，不用手写 getter/clone/to_string。            │
//! │ #[serde(rename_all = "lowercase")]                                       │
//! │   序列化时枚举变体名转小写：Granularity::Session → "session"。            │
//! │ Option<T>  = null 安全版的 Optional<T>，null 不能直接出现。                │
//! │ Vec<T>     = Java 的 List<T>（不可变长度但自身可增删）。                   │
//! │ enum 没有 Java 那种隐式 .name()/.valueOf()，as_str()/from_str() 手写。     │
//! ╰────────────────────────────────────────────────────────────────────────╯
//!
//! # 首期范围（plan.md §1「交付范围」）
//!
//! spec §2 定义了 ContextEntry 的三层（数据面 / 推断注解 / 策略面 / 治理面），
//! 但首期只做核心子集：
//! - 数据面：content + origin + time
//! - 策略面：flow（只 visible_to + collect_from）+ consistency
//! - 治理面：lineage（supersedes + superseded_reason）+ policy_version + audit_ref
//!
//! 以下能力首期**不做**，文件里不留这些 struct（避免空壳膨胀，将来补不返工）：
//! - 推断注解(spec §2.2)：type / sensitivity / derived / provenance
//!   （首期没有产这些数据的抽取器，建出来也是空字段）
//! - 策略面其余：propagate_to / allowed_purposes / redact_on_export
//! - 治理面其余：verification / governance.owner / forget_request

use serde::{Deserialize, Serialize};

// ─────────────────────────────────────────────────────────────────────────
// 枚举
// ─────────────────────────────────────────────────────────────────────────

/// 版本链的作用域粒度（spec §2.3 / plan.md §2.3）。
///
/// 每个 (domain, granularity) 在同一时刻只有 ≤1 条活跃版本
/// （valid_to 为空）。granularity 声明版本链的作用域，让同一个 domain
/// 在不同粒度下独立演进，互不覆盖。
///
/// 例：`player_state` 在 session A 记录玩家 1、在 session B 记录玩家 2，
/// 靠 `Session` 粒度自然隔离。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Granularity {
    /// 同 run 下互为版本，run 结束后不再更新。例：intermediate_result
    Run,
    /// 同 session 下互为版本，不同 session 独立。例：player_state
    Session,
    /// 同 group 下所有 session 共享一个版本。例：game_rule
    #[default]
    Group,
    /// 跨 group（租户级）。例：cross_board_insight
    Tenant,
}

impl Granularity {
    /// 落库/出库用的小写字符串。
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Run => "run",
            Self::Session => "session",
            Self::Group => "group",
            Self::Tenant => "tenant",
        }
    }

    /// 从存储/请求里的字符串还原。Java 没有自动 valueOf，这里手写。
    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "run" => Some(Self::Run),
            "session" => Some(Self::Session),
            "group" => Some(Self::Group),
            "tenant" => Some(Self::Tenant),
            _ => None,
        }
    }
}

/// 过期策略（spec §2.3）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum FreshnessClass {
    /// 很快过期，需定期重验，过期后不可检索。revalidate_due 必填。
    Volatile,
    /// 长期有效，被 supersede 时才失效。默认。
    #[default]
    Stable,
    /// 长期有效，写权限严格管控。
    Audit,
}

impl FreshnessClass {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Volatile => "volatile",
            Self::Stable => "stable",
            Self::Audit => "audit",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "volatile" => Some(Self::Volatile),
            "stable" => Some(Self::Stable),
            "audit" => Some(Self::Audit),
            _ => None,
        }
    }
}

/// status 接口里对每个 context / template 返回的权限标记（plan.md §2.4）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum Permission {
    /// 仅可写（createByTemplate / updateContent）
    W,
    /// 仅可读（retrieve）
    R,
    /// 可读可写
    WR,
}

impl Permission {
    pub fn can_read(self) -> bool {
        matches!(self, Self::R | Self::WR)
    }

    pub fn can_write(self) -> bool {
        matches!(self, Self::W | Self::WR)
    }

    /// 拼一个能写、能读出新的权限。
    pub fn add_write(self) -> Self {
        match self {
            Self::R | Self::WR => Self::WR,
            Self::W => Self::W,
        }
    }

    pub fn add_read(self) -> Self {
        match self {
            Self::W | Self::WR => Self::WR,
            Self::R => Self::R,
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────
// 数据面
// ─────────────────────────────────────────────────────────────────────────

/// 来源（出生地证明）。框架注入，不可篡改（spec §2.1）。
///
/// 四要素 + actor_id。`tenant_id` / `actor_id` 必填，其余按 granularity 选填
/// （group 级不做 session/run 就留 None）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct Origin {
    pub tenant_id: String,
    pub group_id: Option<String>,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    pub actor_id: String,
}

/// 双时间线（spec §2.1）。统一用 Unix 毫秒（i64），避免 Rust 时区类型困扰。
///
/// - valid_from / valid_to: 内容有效期。出生时 valid_to = None 表示 ∞，
///   被 supersede 时由系统回填 valid_to = tx_time。
/// - tx_time: 系统写入时间。
///
/// 落库时 store 负责和 date/timestamp 列互转。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ContextTime {
    pub valid_from: i64,
    pub valid_to: Option<i64>,
    pub tx_time: i64,
}

// ─────────────────────────────────────────────────────────────────────────
// 策略面
// ─────────────────────────────────────────────────────────────────────────

/// 可见范围 —— 谁可以读（spec §2.3）。
///
/// 四要素 + user_ids。模板里可带 `{param}` 占位符，实例化时填充。
/// `tenant_id` 必填；未声明的字段（如模板没写 session_id）实例化时
/// 留 None，store 检索时不额外限制。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct VisibleTo {
    pub tenant_id: String,
    pub group_id: Option<String>,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    /// 可见的具体 user 列表（如底牌对裁判 + 该玩家两人可见）。
    pub user_ids: Vec<String>,
}

/// 写入范围 —— 谁可以写（spec §2.3）。
///
/// 与 VisibleTo 类似但 user_id 是单个 actor（写权限聚焦）。
/// 创建时刻从模板冻结成快照，写入条目后不可变。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct CollectFrom {
    pub tenant_id: String,
    pub group_id: Option<String>,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    pub user_id: Option<String>,
}

/// 读写控制（plan.md §3.3：首期只做 visible_to + collect_from）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct Flow {
    pub visible_to: VisibleTo,
    pub collect_from: CollectFrom,
    // TODO(spec §2.3): propagate_to 当前写死 {groups:1, tenant:0}
    // （组内可传播、跨租户不可）。首期不作为可配置项，硬编码在 store 侧。
    // allowed_purposes / redact_on_export 首期不做。
}

/// 版本与陈旧度（spec §2.3）。
///
/// `domain` 是版本标识：同 (domain, granularity) 条目互为版本。
/// 模板中 domain 可含 `{param}` 占位符，实例化成具体字符串
/// （如 `player_word_{player_id}` → `player_word_player_1`）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct Consistency {
    pub domain: String,
    pub granularity: Granularity,
    /// 默认 stable。
    pub freshness_class: FreshnessClass,
    /// 过期时间兜底（Unix 毫秒）。仅 volatile 必填。
    pub revalidate_due: Option<i64>,
}

// ─────────────────────────────────────────────────────────────────────────
// 治理面
// ─────────────────────────────────────────────────────────────────────────

/// 取代链（spec §2.4 / plan.md updateContent）。
///
/// 首期只做正向链：新条目 supersedes 指向被取代的旧条目的 **context_id
/// （UUID 字符串）**，superseded_reason 记录「为何取代」。
/// 反向链（superseded_by / superseded_at）按约定去掉——旧条目数据面
/// 不可变，生命周期只回填 valid_to，反向前景可由正向链反推，没必要。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct Lineage {
    /// 我取代了谁（旧条目的 context_id）。首次创建为 None。
    pub supersedes: Option<String>,
    /// 取代原因（对应 API 的 change_reason）。
    pub superseded_reason: Option<String>,
    // TODO(spec §2.4): verification / governance.owner / audit_ref / forget_request
}

// ─────────────────────────────────────────────────────────────────────────
// 条目本体 + 策略快照
// ─────────────────────────────────────────────────────────────────────────

/// 一条上下文条目（首期子集）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ContextEntry {
    /// 面向调用方的唯一标识，application 层生成 UUID。
    pub context_id: String,
    pub content: String,
    pub origin: Origin,
    pub time: ContextTime,
    pub flow: Flow,
    pub consistency: Consistency,
    pub lineage: Lineage,
    // TODO(spec §2.1 §2.2): provenance（信任链签名）/ type / sensitivity / derived
    // —— 推断注解首期无数据来源，不建模。
    /// 受哪版 policy 管辖（治理面）。
    pub policy_version: String,
    /// 审计日志指针（治理面）。首期 retrieve 落审计用独立审计表，audit_ref 暂留字段。
    pub audit_ref: Option<String>,
}

/// 策略面快照 —— 创建时从模板冻结（实例化后的实际值），与 entry 一同写入，
/// 写入后不可变（store-plan §2.2）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct PolicySnapshot {
    /// 这条快照属于哪个 entry（用 context_id 关联）。
    pub context_id: String,
    pub version: String,
    pub domain: String,
    pub granularity: Granularity,
    /// 两个 flow 字段落库为 JSON 字符串（SQLite TEXT / MySQL JSON）。
    pub collect_from_json: String,
    pub visible_to_json: String,
    pub freshness_class: FreshnessClass,
    pub revalidate_due: Option<i64>,
    /// 附带义务。首期仅「检索必须落审计」会用到，存 JSON。
    pub obligations: Option<String>,
}

// ─────────────────────────────────────────────────────────────────────────
// 模板
// ─────────────────────────────────────────────────────────────────────────

/// 模板的形参声明（plan.md §2.2）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct TemplateParam {
    pub name: String,
}

/// 策略模板定义（管理员预设，bot 通过 template_id 创建 context）。
///
/// flow / consistency 里的字符串字段可含 `{param}` 占位符，createByTemplate
/// 时用系统环境变量 + 入参实例化。见 plan.md §2.3 模板示例。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct PolicyTemplate {
    pub template_id: String,
    pub description: String,
    pub params: Vec<TemplateParam>,
    pub flow: Flow,
    pub consistency: Consistency,
}

// status 视图（只带展示需要的字段）。

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ContextView {
    pub domain: String,
    pub granularity: Granularity,
    pub description: String,
    pub permission: Permission,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct TemplateView {
    pub template_id: String,
    pub granularity: Granularity,
    pub description: String,
    pub params: Vec<String>,
}

// ─────────────────────────────────────────────────────────────────────────
// retrieve 返回的单条
// ─────────────────────────────────────────────────────────────────────────

/// retrieve 结果条目（plan.md §2.4 retrieve）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct RetrievalItem {
    pub context_id: String,
    pub domain: String,
    pub granularity: Granularity,
    pub content: String,
    /// 用 Unix 毫秒（i64），外部展示再转格式。
    pub valid_from: i64,
}
