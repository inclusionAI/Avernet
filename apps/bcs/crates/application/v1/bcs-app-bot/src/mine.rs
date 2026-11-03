//! The `/bots/mine` flow of `BotServiceImpl`: the listing
//! attempt, its projections, and the on-demand legacy ownership
//! migration sweep. The `BotService::list_mine` trait method on
//! the facade delegates to [`BotServiceImpl::list_mine_impl`]
//! here; the split keeps every source file under the
//! 1,000-line root rule.

use super::*;

impl BotServiceImpl {
    pub(crate) async fn list_mine_impl(&self, command: ListMyBots) -> Result<Page<Bot>, ApplicationError> {
        let human = require_authenticated_user(&command.caller)?;
        Self::validate_pagination(command.offset, command.limit)?;
        let staff_no = human.id.clone();
        let display_name = Self::human_display_name(human);
        self.registry
            .ensure_human_actor(&staff_no, &display_name)
            .await
            .map_err(map_service_error)?;
        // On-demand migration window: this first mine view backfills
        // the caller's legacy owned Bots (best-effort, never fails
        // the listing; the listing itself then reads the NEW owner
        // edges through the controllable projection).
        self.migrate_legacy_owned_bots(&staff_no).await;

        // Bot-authority spec §8.0 dynamic mine: persistent
        // candidates first, then ONE runtime observation,
        // then reachable/unreachable over ALL candidates,
        // total AFTER the filter, offset/limit, and the
        // batch page backfill (page fields, same sampling).
        let first = self
            .mine_attempt(&staff_no, &command)
            .await?;
        if !first.drift {
            return Ok(Page {
                items: first.items,
                total: first.total,
                offset: command.offset,
                limit: command.limit,
            });
        }

        // Page drift (a page target deleted or its role
        // changed since the sampling): retry the WHOLE
        // sampling once; a page that still does not
        // reproduce is `bot_list_changed` — never a
        // silently shifted page pretending to be exact
        // (spec §8.0 point 4).
        let second = self
            .mine_attempt(&staff_no, &command)
            .await?;
        if !second.drift && second.composition == first.composition {
            return Ok(Page {
                items: second.items,
                total: second.total,
                offset: command.offset,
                limit: command.limit,
            });
        }
        Err(ApplicationError::conflict(
            ERROR_BOT_LIST_CHANGED,
            "the controllable Bot list changed while the page \
             was sampled; re-request the page",
        ))
    }
    async fn mine_attempt(
        &self,
        staff_no: &str,
        command: &ListMyBots,
    ) -> Result<MineAttempt, ApplicationError> {
        let candidates = self
            .control_plane
            .list_controllable(self.controllable_query(staff_no, command))
            .await
            .map_err(map_service_error)?;

        let candidate_ids = candidates
            .iter()
            .map(|candidate| candidate.id.clone())
            .collect::<Vec<_>>();
        let snapshot = self
            .registry
            .try_runtime_activity_for(&candidate_ids)
            .await
            .map_err(map_service_error)?;

        let filtered: Vec<ControlledBotCandidate> = candidates
            .into_iter()
            .filter(|candidate| match command.reachability {
                None => true,
                Some(requested) => {
                    // Any reachability filter keeps the
                    // Human self entry excluded (existing
                    // behavior); physical bots are compared
                    // against the ONE observation.
                    candidate.kind == ActorKind::Bot
                        && (requested == BotReachability::Reachable)
                            == candidate_is_reachable(candidate, &snapshot)
                }
            })
            .collect();
        // total BEFORE pagination, AFTER the reachability
        // filter — never the truncated-head assumption
        // (spec §8.0).
        let total = filtered.len() as u64;
        let offset = usize::try_from(command.offset).unwrap_or(usize::MAX);
        let limit = usize::try_from(command.limit).unwrap_or(usize::MAX);
        let page = filtered
            .iter()
            .skip(offset)
            .take(limit)
            .cloned()
            .collect::<Vec<_>>();
        let composition = page
            .iter()
            .map(|candidate: &ControlledBotCandidate| {
                (candidate.id.clone(), candidate.access_relation)
            })
            .collect::<Vec<_>>();
        // The mine page exposes the sampled relation per
        // item (spec §8 mine row: 逐项
        // `access_relation=owner|manager` — Human 自身
        // 条目保持 self 语义). The label comes from the
        // SAME candidates as the page; it is a display
        // fact, never an authorization result.
        let relations: HashMap<String, String> = page
            .iter()
            .map(|candidate| {
                (
                    candidate.id.clone(),
                    mine_relation_label(candidate),
                )
            })
            .collect();

        let page_ids = page
            .iter()
            .map(|candidate| candidate.id.clone())
            .collect::<Vec<_>>();
        let mut drift = false;
        let items = if page_ids.is_empty() {
            Vec::new()
        } else {
            // Batch page backfill; the provider
            // hydration inside `get_by_ids` stays
            // batched (no per-bot provider lookup).
            let views = self
                .control_plane
                .get_by_ids(&page_ids, &self.config.env)
                .await
                .map_err(map_service_error)?;
            let mut views_by_id = views
                .into_iter()
                .map(|view| (view.record.bot_id.clone(), view))
                .collect::<HashMap<_, _>>();
            // Store-side role re-derivation for the
            // page ids (spec §8.0 point 4: 补页发现
            // 目标删除或角色变化). ONE fresh candidate
            // snapshot — no new WS observation, no new
            // provider batch loop.
            let fresh = self
                .control_plane
                .list_controllable(self.controllable_query(staff_no, command))
                .await
                .map_err(map_service_error)?;
            let fresh_by_id = fresh
                .iter()
                .map(|candidate| (candidate.id.clone(), candidate.access_relation))
                .collect::<HashMap<_, _>>();
            drift = page
                .iter()
                .any(|candidate| {
                    !views_by_id.contains_key(&candidate.id)
                        || fresh_by_id
                            .get(&candidate.id)
                            .is_none_or(|fresh_relation| {
                                *fresh_relation != candidate.access_relation
                            })
                });
            let ordered = page_ids
                .iter()
                .filter_map(|bot_id| views_by_id.remove(bot_id))
                .collect::<Vec<_>>();
            self.project_records_with_activity(ordered, &snapshot.active_bot_ids)
                .await?
        };
        let items = items
            .into_iter()
            .map(|bot| {
                let relation = relations.get(bot.bot_id()).cloned();
                bot.with_mine_access_relation(relation)
            })
            .collect::<Vec<_>>();
        Ok(MineAttempt {
            items,
            total,
            drift,
            composition,
        })
    }

    /// ONE sampled mine attempt (bot-authority spec §8.0):
    /// 1. ONE persistent candidate snapshot (`list_controllable` —
    ///    the owner|manager union + the Human self entry, persistent
    ///    filtering only, no pagination);
    /// 2. ONE `try_runtime_activity_for` observation over ALL
    ///    candidates (failed reads propagate — never a fake
    ///    unreachable);
    /// 3. the reachability filter over ALL candidates (existing
    ///    semantics: `status = Online && (WS active || HTTP
    ///    Provider routable)`; a specified reachability keeps
    ///    excluding the Human self entry, per the pre-existing
    ///    behavior);
    /// 4. `total` AFTER the filter, then offset/limit;
    /// 5. the batch page backfill through the same Core, sharing
    ///    the observation from step 2 for the reachability
    ///    projection;
    /// 6. the page roles RE-DERIVED store-side (one fresh
    ///    `list_controllable`, no new WS observation and no
    ///    new provider batch): a page target that got deleted
    ///    OR whose owner/manager relation changed since the
    ///    sampling is page drift (spec §8.0 point 4 literal
    ///    删除或角色变化).
    /// On-demand ownership migration (migration window): the
    /// first `/bots/mine` view after upgrading backfills ownership
    /// for the caller's legacy Bots — every live version-0 Bot
    /// whose legacy `created_by` names this Human gets the
    /// self-service `initialize_ownership` composite (idempotent,
    /// audited with the caller themself as the initiator), which is
    /// exactly the bcs-admin apply classification's backfillable
    /// carve. Best-effort per Bot: a failed migration is logged and
    /// the Bot is skipped — its reads still fall back to the legacy
    /// shim — instead of failing the whole listing.
    async fn migrate_legacy_owned_bots(&self, staff_no: &str) {
        // The cap bounds initialization ATTEMPTS per request, never
        // the classification sweep: already-initialized rows, Human
        // actor rows and conflicted records are skipped BEFORE the
        // cap is consumed, so repeated mine calls always progress to
        // the remaining backlog instead of re-scanning one prefix.
        const SWEEP_CAP: usize = 100;
        // Store-side prefilter: the candidate list itself is the
        // narrowing (live physical Bots, byte-exact creator,
        // `ownership_version = 0`), so a fully-migrated owner costs
        // this one lookup instead of a per-Bot governance rescan of
        // the whole creator backlist (review finding: steady-state
        // N+1). A read failure is best-effort like any single Bot's
        // failure: the listing proceeds and the next page retries.
        let candidates = match self
            .registry
            .list_uninitialized_bots_by_creator(staff_no, SWEEP_CAP)
            .await
        {
            Ok(candidates) => candidates,
            Err(error) => {
                tracing::warn!(
                    error = %error,
                    "legacy ownership migration: candidate query failed; skipped this request"
                );
                return;
            }
        };
        let owner_actor = human_actor_id(staff_no);
        let mut attempts = 0usize;
        for bot in &candidates {
            if attempts >= SWEEP_CAP {
                tracing::warn!(
                    remaining = candidates.len().saturating_sub(attempts),
                    "legacy ownership migration attempt cap hit; the remaining Bots migrate on the next /bots/mine"
                );
                break;
            }
            let governance = match self
                .registry
                .ownership_governance_state(&bot.bot_uuid)
                .await
            {
                Ok(governance) => governance,
                Err(error) => {
                    tracing::warn!(
                        bot_uuid = %bot.bot_uuid,
                        error = %error,
                        "legacy ownership migration: governance read failed; skipped"
                    );
                    continue;
                }
            };
            let Some(governance) = governance else {
                continue; // unknown in this env — nothing to migrate
            };
            if !governance.live || governance.ownership_version > 0 {
                continue;
            }
            // Human ACTOR rows (this caller's own `human_<staff_no>`
            // is created moments earlier) are never ownership targets
            // — bcs-admin classify() rejects them too.
            if governance.actor_kind != ActorKind::Bot {
                continue;
            }
            // The initialized owner must be the stored creator fact,
            // compared byte-for-byte: a case-insensitive candidate
            // query is never authority for minting ownership.
            if governance.created_by.as_deref() != Some(staff_no) {
                tracing::warn!(
                    bot_uuid = %bot.bot_uuid,
                    created_by = ?governance.created_by,
                    staff_no = %staff_no,
                    "legacy ownership migration: creator mismatch; skipped"
                );
                continue;
            }
            // bcs-admin classify() parity: migrate only zero-conflict
            // records (no approved owner edge, or the single agreeing one).
            match governance.owner_edges.as_slice() {
                [] => {}
                [edge] if edge == &owner_actor => {}
                _ => {
                    tracing::warn!(
                        bot_uuid = %bot.bot_uuid,
                        owner_edges = ?governance.owner_edges,
                        "legacy ownership migration: contradicting owner edges; manual governance"
                    );
                    continue;
                }
            }
            attempts += 1;
            let mutation = AuthorityMutation {
                mutation_id: legacy_migration_mutation_id(&bot.bot_uuid),
                actor: AuthorityActor::Human {
                    user_id: staff_no.to_string(),
                },
            };
            match self
                .registry
                .initialize_ownership(&bot.bot_uuid, staff_no, mutation)
                .await
            {
                Ok(_) => {
                    tracing::info!(
                        bot_uuid = %bot.bot_uuid,
                        "legacy ownership migration initialized via /bots/mine"
                    );
                }
                Err(error) => {
                    tracing::warn!(
                        bot_uuid = %bot.bot_uuid,
                        error = %error,
                        "legacy ownership migration failed for this Bot; the legacy fallback still serves its reads"
                    );
                }
            }
        }
    }
}

/// Deterministic per-Bot mutation id for the on-demand ownership
/// migration: server-generated, bounded by the audit column width
/// (VARCHAR(64)), collision-scoped per Bot.
fn legacy_migration_mutation_id(bot_id: &str) -> String {
    use std::hash::Hasher;
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    hasher.write(bot_id.as_bytes());
    format!("mine-init-{:016x}", hasher.finish())
}
/// Existing reachability semantics of a candidate against the
/// ONE §8.0 observation: `status = Online && (WS active ||
/// HTTP Provider routable)`. The Human kind has no
/// reachability semantics and stays excluded under any
/// specified filter; the page total is computed from the
/// same sampling, never from a truncated head.
fn candidate_is_reachable(
    candidate: &ControlledBotCandidate,
    snapshot: &RuntimeActivitySnapshot,
) -> bool {
    candidate.kind == ActorKind::Bot
        && candidate.status == ActorStatus::Online
        && snapshot.active_bot_ids.contains(&candidate.id)
}
/// The public mine item label for one sampled candidate
/// (spec §8 mine row): physical Bots carry their edge
/// relation (`owner` | `manager`), and the Human self
/// entry keeps the `self` semantics — the edge-less
/// candidate's internal Owner DISPLAY tag never becomes a
/// public `owner` claim.
fn mine_relation_label(
    candidate: &ControlledBotCandidate,
) -> String {
    match candidate.kind {
        ActorKind::Human => "self".to_string(),
        ActorKind::Bot => match candidate.access_relation {
            BotAccessRelation::Owner => "owner".to_string(),
            BotAccessRelation::Manager => "manager".to_string(),
        },
    }
}
/// One sampled mine attempt (spec §8.0): candidates → ONE
/// runtime observation → reachability over ALL candidates →
/// total → offset/limit → batch page backfill.
///
/// `drift` reports whether the page backfill discovered a
/// page target missing from the same store (deleted or
/// otherwise unresolvable since the candidate sampling).
struct MineAttempt {
    items: Vec<Bot>,
    total: u64,
    drift: bool,
    composition: Vec<(String, BotAccessRelation)>,
}
