//! BCS Group Context service —— core 实现 + application 编排。
//!
//! 分层（CLAUDE.md）：
//!   - core 实现持 `Arc<dyn GroupContextRepoPort>`，做行为与编排，**不直接依赖 DB plugin**；
//!   - application 实现持 `Arc<dyn GroupContextCoreService>`，做 HTTP DTO ↔ core Command 翻译
//!     与 ServiceResult ↔ UseCaseError 映射，是 use-case 编排层。
//!
//! ⚠️ 本轮重点：core impl 每个方法都带「执行过程 + 伪代码」注释，对应 plan.md
//!    四个 API 的 step 序列。可逐函数 review。不保证整 crate 本轮直接编译过。

pub mod application;
pub mod core;
mod noop;

pub use bcs_group_context_store::MemoryGroupContextRepo;
pub use application::GroupContextManagement;
pub use core::GroupContextCore;

pub type GroupContextStore = GroupContextCore;
