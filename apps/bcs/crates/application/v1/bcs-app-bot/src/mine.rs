//! `list_mine` — the controllable-Bot union projection use case (plan
//! Task 9, spec §7).
//!
//! The use case answers "which Bot rows does the current Human control,
//! and by which relation" for THIS authenticated caller only:
//!
//! 1. authentication/validation first, then the idempotent Human
//!    materialization (it never binds physical Bot ownership);
//! 2. ONE `list_controllable` union read through the control-plane Core:
//!    physical owner ∪ manager edges (deduplicated per Bot, owner label
//!    wins) plus the caller's own Human self row as an explicit
//!    `Owner`-labeled compatibility projection (spec §4.1/§7.1);
//! 3. virtual reachability is computed over the FULL candidate set and
//!    filtered BEFORE `total` (spec §7.2: no premature truncation), the
//!    contract sort (`created_at DESC, bot_id ASC`) is re-asserted, and
//!    only then offset/limit paginate — never two separately paged lists
//!    stitched together;
//! 4. every item carries the REQUIRED `access_relation` label computed
//!    from the CURRENT authority edges — `created_by` never decides it.
//!
//! The label is a per-read permission snapshot and does not replace the
//! server-side authorization of any subsequent request (spec §7.1).

use bcs_service_api::application::v1::{
    ApplicationError, Bot, BotKind, ListMyBots, MyBot, Page, require_authenticated_user,
};
use bcs_service_api::{ActorKind, BotControllableQuery};

use crate::{BotServiceImpl, domain_status, map_service_error, normalize_optional_name};

impl BotServiceImpl {
    pub(super) async fn list_mine_impl(
        &self,
        command: ListMyBots,
    ) -> Result<Page<MyBot>, ApplicationError> {
        let human = require_authenticated_user(&command.caller)?;
        Self::validate_pagination(command.offset, command.limit)?;
        let staff_no = human.id.clone();
        let display_name = Self::human_display_name(human);
        // Idempotent self materialization; it never grants ownership.
        self.registry
            .ensure_human_actor(&staff_no, &display_name)
            .await
            .map_err(map_service_error)?;

        // The single union read: one Core call, one hydrated batch; the
        // relation labels arrive from the same read (no per-Bot queries).
        let views = self
            .control_plane
            .list_controllable(BotControllableQuery {
                user_id: staff_no,
                env: self.config.env.clone(),
                kind: command.kind.map(|kind| match kind {
                    BotKind::Bot => ActorKind::Bot,
                    BotKind::Human => ActorKind::Human,
                }),
                name: normalize_optional_name(command.name),
                status: command.status.map(domain_status),
            })
            .await
            .map_err(map_service_error)?;
        let relations = views
            .iter()
            .map(|view| view.access_relation)
            .collect::<Vec<_>>();
        let bots = self
            .project_records(views.into_iter().map(|view| view.bot).collect())
            .await?;

        // Unified reachability filter over the full candidate set:
        // physical Bots only — `kind=human` with a reachability filter
        // stays an empty page (spec §7.2).
        let mut items = Vec::with_capacity(bots.len());
        for (bot, access_relation) in bots.into_iter().zip(relations) {
            if let Some(reachability) = command.reachability
                && !matches!(
                    &bot,
                    Bot::Physical(physical) if physical.reachability == reachability
                )
            {
                continue;
            }
            items.push(MyBot {
                bot,
                access_relation,
            });
        }

        // Contract order (`created_at DESC, bot_id ASC`) re-asserted after
        // the in-memory filtering, then total, then ONE pagination pass.
        let created_at = |bot: &Bot| match bot {
            Bot::Physical(physical) => physical.created_at,
            Bot::Human(human) => human.created_at,
        };
        items.sort_by(|left, right| {
            created_at(&right.bot)
                .cmp(&created_at(&left.bot))
                .then_with(|| left.bot.bot_id().cmp(right.bot.bot_id()))
        });
        let total = items.len() as u64;
        let offset = usize::try_from(command.offset).unwrap_or(usize::MAX);
        let limit = usize::try_from(command.limit).unwrap_or(usize::MAX);
        let items = items.into_iter().skip(offset).take(limit).collect();
        Ok(Page {
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
    }
}