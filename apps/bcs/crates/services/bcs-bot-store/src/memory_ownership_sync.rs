//! Memory ownership sync command (2026-10-29 design §5.1) —
//! the memory projection of the SQL `ownership_sync` command
//! (`bcs-edge-permission-store/src/authority/ownership_sync.rs`):
//! identical outcomes through the shared contract harness —
//! the same frozen strings (`ownership_sync`,
//! `transfer_invalidate`, `system`, `system_transfer_close`),
//! the same audit JSON bytes, the same `OwnershipChange`
//! shapes, the same pinned error codes.
//!
//! One write-guard span (staged clone → validate → mutate →
//! swap) mirrors the SQL twin's one bot-row-locked
//! transaction; the staged clone IS that transaction's in-tx
//! guard — see the span comment in the command body.

use tracing::warn;

use bcs_service_api::types::bot_authority::{
    human_user_id_of_actor, AuthorityActorKind, OwnershipChange,
    OwnershipSync, TransferStatus,
};
use bcs_service_api::ServiceResult;

use super::authority::{
    memory_append_audit, memory_bot_ctx_opt, memory_human_unknown,
    memory_live_humans, memory_operator_forbidden, memory_require_initialized,
    memory_set_edge, memory_state_owner, now_ms, owner_integrity_error,
    owner_integrity_error_message, resolve_env, transfer_audit_state,
    CLOSING_SYSTEM_ACTOR, MemoryAuthorityEdgeKind, MemoryAuthorityEdgeStatus,
};
use crate::memory::MemoryBotRepo;

/// Frozen audit action spelling, harness-pinned across stores
/// (twin of the SQL store's `audit::ACTION_OWNERSHIP_SYNC`).
const ACTION_OWNERSHIP_SYNC: &str = "ownership_sync";
/// Frozen closure action spelling (twin of the SQL store's
/// `audit::ACTION_TRANSFER_INVALIDATE`; the authority module's
/// Task-3 lazy closures use the same string).
const ACTION_TRANSFER_INVALIDATE: &str = "transfer_invalidate";
/// Frozen closure reason (twin of the SQL store's
/// `audit::REASON_OWNERSHIP_SYNC` — one concept, one string,
/// same bytes in the transfer row and the audit row).
const REASON_OWNERSHIP_SYNC: &str = "ownership_sync";

/// Frozen audit state JSON — byte-identical to the SQL spelling
/// (the harness pins the bytes across stores).
fn ownership_audit_state(owner_actor: &str, version: u64) -> String {
    format!("{{\"owner\":\"{owner_actor}\",\"version\":{version}}}")
}

/// Memory ownership sync: one write-guard span carries the
/// staged clone → validate → mutate → swap (same discipline as
/// `manager_sync_impl`).
pub(super) async fn ownership_sync_impl(
    repo: &MemoryBotRepo,
    command: OwnershipSync,
) -> ServiceResult<OwnershipChange> {
    // sync 可信应用专用 (设计 §2).
    if command.mutation.actor.kind() != AuthorityActorKind::Service {
        return Err(memory_operator_forbidden(
            "ownership sync is trusted-service only; Human operators use the ownership transfer commands",
        ));
    }
    let mutation_id = command.mutation.mutation_id.clone();
    // 目标 actor 形态（形状/存续校验推迟到幂等判定之后——
    // 设计 §5.1 步骤 3→4，与 SQL 端同一顺序）。
    let target = command.owner_user_id.clone();
    // Lenient target classification (same warn lens as the SQL
    // store's; the harness pins outcomes, not log bytes): a
    // missing/tombstoned bot is an unchanged no-op; a
    // version-0 live bot still hard-rejects in the span below.
    let Some(ctx) = memory_bot_ctx_opt(repo, &command.bot_id).await? else {
        warn!(
            env = %resolve_env(),
            bot_id = %command.bot_id,
            mutation_id = %mutation_id,
            "ownership sync target bot is missing; returning unchanged"
        );
        return Ok(OwnershipChange::unchanged(&command.bot_id));
    };
    // One write guard spans the whole command (staged clone →
    // validate → mutate → swap). The staged clone IS the in-tx
    // guard of the SQL twin: it is cloned under the exclusive
    // write guard, so EVERY fact this command validates below
    // (version, owner, edges, live humans, pendings) is read
    // from ONE frozen snapshot that no interleaved command can
    // mutate mid-flight, and the committed swap is
    // all-or-nothing. The SQL store needs its in-transaction
    // count guards because its pre-reads race other writers;
    // the memory projection has no such window by
    // construction — a concurrent command either completed
    // before this clone or waits on this very guard. A failed
    // validation (including the audit-conflict leg the harness
    // seeds via S08) discards the clone, so nothing
    // half-written can leak.
    let mut state_guard = repo.authority.write().await;
    // The live-Human registry snapshot is read INSIDE the span
    // (lock order authority → bots → deleted IDs, the same
    // order the manager/transfer commands document): a
    // tombstone racing the span can never leak an edge to a
    // deleted Human.
    let live_humans = memory_live_humans(repo).await;
    let mut staged = state_guard.clone();
    let version = memory_require_initialized(&staged, &ctx)?;
    let owner = memory_state_owner(&staged, &ctx);
    // 幂等判定先于一切目标校验（设计 §5.1 步骤 3）：重放当前
    // owner 永不写状态，也不依赖目标的存活性。
    if target == owner {
        // Deliberate deviation from the plan snippet (Task-4
        // SQL-review carryover): never `.expect` the owner
        // actor shape in a write command — a corrupt
        // non-`human_` owner edge surfaces the pinned
        // integrity error with the SAME message lens as the
        // SQL store's idempotent leg (byte-identical detail).
        let Some(owner_user) = human_user_id_of_actor(&owner) else {
            return Err(owner_integrity_error_message(format!(
                "owner edge subject '{owner}' is not a Human actor id"
            )));
        };
        return Ok(OwnershipChange::unchanged_with_owner(&ctx.bot_id, &owner_user));
    }
    // 目标两阶段判定（设计 §5.1 步骤 4；只在真实切换可能发生
    // 时执行）：actor 形态须剥离回非空无空白的 user id，且其
    // Human 行须在本 span 内复验存活；target ≠ bot 自身是
    // §3.2.4 方向守卫。
    let Some(target_user) = human_user_id_of_actor(&target)
        .filter(|user| !user.is_empty() && user.trim() == *user
            && !user.chars().any(char::is_whitespace))
    else {
        return Err(memory_human_unknown(format!(
            "ownership sync target '{target}' is not a valid Human user id"
        )));
    };
    if !live_humans.contains(&target_user) || target == ctx.bot_id {
        return Err(memory_human_unknown(format!(
            "ownership sync target '{target}' is not a live Human actor"
        )));
    }
    // Edge-state classification on the staged clone (the
    // staged state IS the guard — see the span comment above;
    // the memory twin of the SQL in-tx edge-state count
    // guards). A target carrying an approved owner row on a
    // bot whose approved (ref_id 0) owner count is 1 can only
    // be constraint-outside corruption: this branch already
    // returned on `target == owner`.
    let target_owner_edge = staged.edges.get(&(
        ctx.env.clone(), ctx.bot_id.clone(), target.clone(),
        MemoryAuthorityEdgeKind::Owner,
    ));
    if matches!(target_owner_edge, Some((MemoryAuthorityEdgeStatus::Approved, _))) {
        return Err(owner_integrity_error(&ctx.bot_id, 2));
    }

    let actor_kind = command.mutation.actor.kind().as_str().to_string();
    let actor_id = command.mutation.actor.actor_id();
    let now = now_ms();
    let valid_pendings: Vec<(String, String, String)> = staged
        .transfers
        .iter()
        .filter(|((env, bot, _), row)| {
            *env == ctx.env && *bot == ctx.bot_id && row.status == TransferStatus::Pending
                && row.expires_at > now
        })
        .map(|((env, bot, target), _row)| {
            (env.clone(), bot.clone(), target.clone())
        })
        .collect();
    // 多条 valid pending 是约束外损坏：与 SQL 的
    // `pre_read_bot_pending` 对齐为完整性错误，两侧不分叉 —
    // same `bot_owner_integrity` code, byte-identical detail.
    if valid_pendings.len() > 1 {
        return Err(owner_integrity_error_message(format!(
            "bot {} has {} valid pending transfers",
            ctx.bot_id,
            valid_pendings.len()
        )));
    }

    // ---- mutate (staged clone only; nothing is visible until
    // the swap) ----
    // 1. Old owner FULLY removed: owner edge revoked, any
    //    manager edge revoked as-is (a revoked row stays
    //    revoked — no accept-style manager demotion, 设计 §2).
    memory_set_edge(&mut staged, &ctx, &owner, MemoryAuthorityEdgeKind::Owner,
        MemoryAuthorityEdgeStatus::Revoked);
    memory_set_edge(&mut staged, &ctx, &owner, MemoryAuthorityEdgeKind::Manager,
        MemoryAuthorityEdgeStatus::Revoked);
    // 2. Target owner edge created/restored (the memory map
    //    keeps one row per key, so this covers both the SQL
    //    INSERT and the revoked-row RESTORE legs).
    memory_set_edge(&mut staged, &ctx, &target, MemoryAuthorityEdgeKind::Owner,
        MemoryAuthorityEdgeStatus::Approved);
    // 3. The new owner's manager edge is stripped (owner 不占
    //    manager 名额).
    memory_set_edge(&mut staged, &ctx, &target, MemoryAuthorityEdgeKind::Manager,
        MemoryAuthorityEdgeStatus::Revoked);
    // 4. Version +1 (exactly one CAS-equivalent increment; the
    //    snapshot was validated under the write guard).
    staged.ownership_versions.insert(
        (ctx.env.clone(), ctx.bot_id.clone()),
        version + 1,
    );
    // 5. Audit row 0: the swap (byte-frozen JSON states).
    memory_append_audit(
        &mut staged, &ctx, ACTION_OWNERSHIP_SYNC, &mutation_id, &actor_kind,
        &actor_id, &target,
        Some(&ownership_audit_state(&owner, version)),
        &ownership_audit_state(&target, version + 1),
        0,
    )?;
    // 6. Valid pendings close as invalidated (fixed system
    //    marker, frozen reason) + audit row 1.. (deterministic
    //    target order — sort for retry-stable sequences).
    let mut pendings = valid_pendings;
    pendings.sort();
    for (index, (env, bot, pending_target)) in pendings.iter().enumerate() {
        let key = (env.clone(), bot.clone(), pending_target.clone());
        // Staged-clone self-consistency (NOT a race guard):
        // the key was COLLECTED from this very map a few
        // purely-synchronous statements above, the write
        // guard makes the snapshot exclusive, and nothing in
        // this span removes transfer rows — so the only way
        // these reads/writes can miss is a refactor that
        // breaks that invariant; the expect pins it.
        let row = staged.transfers.get(&key)
            .expect("pending reads under the write span");
        let before = transfer_audit_state(
            "pending", &row.from_owner_actor_id, row.expected_ownership_version,
        );
        let after = transfer_audit_state(
            "invalidated", &row.from_owner_actor_id,
            row.expected_ownership_version,
        );
        let row = staged.transfers.get_mut(&key)
            .expect("pending writes under the write span");
        row.status = TransferStatus::Invalidated;
        row.decided_at = Some(now);
        row.decided_by_kind = Some(AuthorityActorKind::System);
        row.decided_by_id = Some(CLOSING_SYSTEM_ACTOR.to_string());
        row.reason = Some(REASON_OWNERSHIP_SYNC.to_string());
        row.updated_at = now;
        memory_append_audit(
            &mut staged, &ctx, ACTION_TRANSFER_INVALIDATE, &mutation_id,
            AuthorityActorKind::System.as_str(), CLOSING_SYSTEM_ACTOR,
            pending_target,
            Some(&before), &after,
            (index + 1) as u64,
        )?;
    }

    // Single atomic swap inside the span (the memory commit
    // point, the twin of the SQL transaction commit): a
    // failure above — including the harness's seeded audit
    // conflict (S08) — discarded the staged clone and left
    // the shared state untouched; no interleaved command can
    // lose this commit.
    *state_guard = staged;

    // Deliberate deviation from the plan snippet (Task-4
    // SQL-review carryover, mirroring the SQL store's
    // post-commit site): the swap has ALREADY committed, so a
    // corrupt non-`human_` owner shape surfaces the pinned
    // integrity error here — never a write-command panic (a
    // post-commit panic would be an undefined-outcome hole).
    let Some(old_user) = human_user_id_of_actor(&owner) else {
        return Err(owner_integrity_error_message(format!(
            "owner edge subject '{owner}' is not a Human actor id"
        )));
    };
    Ok(OwnershipChange {
        bot_id: ctx.bot_id.clone(),
        owner_user_id: Some(target_user),
        previous_owner_user_id: Some(old_user),
        changed: true,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};
    use bcs_service_api::types::bot_authority::{
        AuthorityActor, AuthorityMutation, BotAccessRelation,
        ManagerListQuery, human_actor_id,
    };
    use bcs_service_api::BotCapabilities;

    /// Byte-parity pins for the frozen spellings (mirror of the
    /// SQL store's `frozen_spellings` test plus the shared
    /// harness's cross-store string set).
    #[test]
    fn frozen_spellings() {
        assert_eq!(ACTION_OWNERSHIP_SYNC, "ownership_sync");
        assert_eq!(REASON_OWNERSHIP_SYNC, "ownership_sync");
        assert_eq!(ACTION_TRANSFER_INVALIDATE, "transfer_invalidate");
        assert_eq!(CLOSING_SYSTEM_ACTOR, "system_transfer_close");
        assert_eq!(AuthorityActorKind::System.as_str(), "system");
        assert_eq!(
            ownership_audit_state("human_a", 3),
            "{\"owner\":\"human_a\",\"version\":3}"
        );
    }

    /// Service-actor command construction (same discipline as
    /// the SQL focused tests' `ownership_sync_command` in
    /// `authority_ownership_sync_edges.rs`).
    fn command(bot: &str, owner_user: &str, mutation_id: &str) -> OwnershipSync {
        OwnershipSync {
            bot_id: bot.to_string(),
            owner_user_id: human_actor_id(owner_user),
            mutation: AuthorityMutation {
                mutation_id: mutation_id.to_string(),
                actor: AuthorityActor::Service { app_id: 7 },
            },
        }
    }

    /// Seed helpers mirror the shared fixture
    /// (`tests/memory_authority_fixture.rs`): register the
    /// bot, then drive the SAME authority state production
    /// writes.
    async fn seed_bot(repo: &MemoryBotRepo, bot_id: &str, version: u64) {
        repo.register(bot_id.to_string(), BotCapabilities::default())
            .await
            .expect("register fixture bot");
        let env = resolve_env();
        repo.authority_state()
            .write()
            .await
            .ownership_versions
            .insert((env, bot_id.to_string()), version);
    }

    async fn seed_live_human(repo: &MemoryBotRepo, env: &str, user_id: &str) {
        repo.seed_human_actor_in(env, user_id).await;
    }

    async fn grant_edge(
        repo: &MemoryBotRepo,
        env: &str,
        bot_id: &str,
        user_id: &str,
        kind: MemoryAuthorityEdgeKind,
    ) {
        repo.authority_state()
            .write()
            .await
            .edges
            .insert(
                (env.to_string(), bot_id.to_string(), human_actor_id(user_id), kind),
                (MemoryAuthorityEdgeStatus::Approved, 0),
            );
    }

    /// Carryover leg (a) from Task 4's focused SQL tests
    /// (`old_owner_manager_edge_is_revoked_by_the_swap`): the
    /// old owner who ALSO holds an approved manager edge loses
    /// BOTH edges on the swap — full removal, never the
    /// accept-style manager demotion (设计 §2). The shared
    /// contract's S01 never seeds this state.
    #[tokio::test]
    async fn old_owner_manager_edge_is_revoked_by_the_swap() {
        let repo = MemoryBotRepo::new();
        let env = resolve_env();
        let bot = "bot_own_sync_oldmgr_mem";
        for user in ["own", "keep", "next"] {
            seed_live_human(&repo, &env, user).await;
        }
        seed_bot(&repo, bot, 1).await;
        grant_edge(&repo, &env, bot, "own", MemoryAuthorityEdgeKind::Owner).await;
        // The leg S01 never seeds: the current owner ALSO holds
        // an approved manager edge.
        grant_edge(&repo, &env, bot, "own", MemoryAuthorityEdgeKind::Manager).await;
        grant_edge(&repo, &env, bot, "keep", MemoryAuthorityEdgeKind::Manager).await;

        let change = repo
            .sync_ownership(command(bot, "next", "mutation-ownsync-oldmgr-mem"))
            .await
            .expect("ownership sync");
        assert!(change.changed);
        assert_eq!(change.owner_user_id.as_deref(), Some("next"));
        assert_eq!(change.previous_owner_user_id.as_deref(), Some("own"));
        assert_eq!(
            repo.authority_state()
                .read()
                .await
                .ownership_versions
                .get(&(env, bot.to_string()))
                .copied(),
            Some(2),
            "exactly one increment"
        );

        assert_eq!(
            repo.role(&human_actor_id("own"), bot)
                .await
                .expect("old owner role"),
            None,
            "old owner FULLY removed — the manager edge went with the owner edge (设计 §2)"
        );
        assert_eq!(
            repo.role(&human_actor_id("next"), bot)
                .await
                .expect("new owner role"),
            Some(BotAccessRelation::Owner)
        );
        let page = repo
            .managers(ManagerListQuery {
                bot_id: bot.to_string(),
                ..Default::default()
            })
            .await
            .expect("managers page");
        assert!(
            page.items.iter().all(|user| user != "own"),
            "the old owner is not demoted into the manager set: {:?}",
            page.items
        );
        assert!(
            page.items.contains(&"keep".to_string()),
            "bystander managers stand: {:?}",
            page.items
        );
    }

    /// Carryover leg (b) from Task 4's focused SQL tests
    /// (`bot_without_owner_edge_is_the_integrity_error`): a
    /// version-1 bot with NO approved owner edge is corrupt
    /// state — the pinned integrity error, never a silent
    /// repair.
    #[tokio::test]
    async fn bot_without_owner_edge_is_the_integrity_error() {
        let repo = MemoryBotRepo::new();
        let env = resolve_env();
        let bot = "bot_own_no_owner_edge_mem";
        seed_live_human(&repo, &env, "next").await;
        seed_bot(&repo, bot, 1).await;

        let err = repo
            .sync_ownership(command(bot, "next", "mutation-ownsync-noedge-mem"))
            .await
            .expect_err("a bot with no owner edge must not be swapped");
        assert!(
            err.to_string().contains("bot_owner_integrity"),
            "pinned integrity code, got: {err}"
        );
    }
}
