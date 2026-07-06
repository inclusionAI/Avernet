//! Human actor use-case implementation.

use std::sync::Arc;

use async_trait::async_trait;

use bcs_service_api::{
    BotRegistryCoreService, CurrentHumanActorCommand, EnsureCurrentHumanActorError,
    EnsureCurrentHumanActorResult, HumanActorService, RelationCoreService,
    RepairHumanActorInfoResult,
};

/// Human actor application service backed by registry and relation services.
pub struct HumanActor {
    registry: Arc<dyn BotRegistryCoreService>,
    relation: Arc<dyn RelationCoreService>,
}

impl HumanActor {
    pub fn new(
        registry: Arc<dyn BotRegistryCoreService>,
        relation: Arc<dyn RelationCoreService>,
    ) -> Self {
        Self { registry, relation }
    }
}

#[async_trait]
impl HumanActorService for HumanActor {
    async fn repair_human_actor_info(
        &self,
        command: CurrentHumanActorCommand,
    ) -> RepairHumanActorInfoResult {
        let staff_no = command.staff_no;
        let nick_name = normalize_nick_name(command.nick_name);
        let mut human_id: Option<String> = None;
        let mut current_name: Option<String> = None;
        let mut name_repaired = false;
        let mut skipped_reason: Option<&'static str> = None;
        let mut repair_error: Option<String> = None;

        if let Some(staff) = staff_no.as_deref() {
            let bot_uuid = format!("human_{}", staff);
            human_id = Some(bot_uuid.clone());

            let stored = self.registry.get(&bot_uuid).await;
            current_name = stored
                .as_ref()
                .and_then(|bot| bot.capabilities.name.clone());

            match (stored.as_ref(), nick_name.as_deref()) {
                (None, _) => {
                    skipped_reason = Some("human_row_not_found");
                }
                (_, None) => {
                    skipped_reason = Some("nick_name_unavailable_in_cookie");
                }
                (Some(_), Some(nick)) if nick == staff => {
                    skipped_reason = Some("nick_name_equals_staff_no");
                }
                (Some(_), Some(nick)) => {
                    let needs_repair = match current_name.as_deref() {
                        Some(name) if name == staff => true,
                        Some(_) => false,
                        None => true,
                    };
                    if !needs_repair {
                        skipped_reason = Some("name_already_non_fallback");
                    } else {
                        match self.registry.update_human_name(staff, nick).await {
                            Ok(()) => {
                                name_repaired = true;
                            }
                            Err(error) => {
                                repair_error = Some(error.to_string());
                            }
                        }
                    }
                }
            }
        } else {
            skipped_reason = Some("no_staff_no_in_cookie");
        }

        let current_name_after_repair = if name_repaired {
            nick_name.clone()
        } else {
            current_name.clone()
        };

        RepairHumanActorInfoResult {
            ok: repair_error.is_none(),
            user_id: staff_no.clone(),
            staff_no,
            nick_name,
            human_id,
            previous_name: current_name,
            current_name: current_name_after_repair,
            name_repaired,
            skipped_reason,
            error: repair_error,
        }
    }

    async fn ensure_current_human_actor(
        &self,
        command: CurrentHumanActorCommand,
    ) -> Result<EnsureCurrentHumanActorResult, EnsureCurrentHumanActorError> {
        let staff_no = command
            .staff_no
            .filter(|staff_no| !staff_no.is_empty())
            .ok_or(EnsureCurrentHumanActorError::LoginRequired)?;

        validate_staff_no(&staff_no).map_err(|_| EnsureCurrentHumanActorError::InvalidStaffNo)?;

        let nick_name = resolve_human_nick_name(&staff_no, command.nick_name.as_deref());
        let human_id = format!("human_{}", staff_no);
        let env = bcs_config::resolve_env_str();

        let human_result = self
            .registry
            .ensure_human_actor(&staff_no, &nick_name)
            .await
            .map_err(|error| {
                EnsureCurrentHumanActorError::EnsureHumanActorFailed(error.to_string())
            })?;

        let matched_bots = self
            .registry
            .list_legacy_bots_for_owner(&staff_no, &env)
            .await
            .map_err(|error| {
                EnsureCurrentHumanActorError::ListLegacyBotsForOwnerFailed(error.to_string())
            })?;

        let mut total_created: u32 = 0;
        let mut total_upgraded: u32 = 0;
        let mut failed_bots: Vec<String> = Vec::new();
        let mut errors: Vec<String> = Vec::new();
        let matched_bot_ids: Vec<String> = matched_bots
            .iter()
            .map(|bot| bot.bot_uuid.clone())
            .collect();

        for bot in &matched_bots {
            match self
                .relation
                .ensure_owner_edges_counted(&human_id, &bot.bot_uuid, &env)
                .await
            {
                Ok(result) => {
                    total_created += result.created;
                    total_upgraded += result.upgraded;
                }
                Err(error) => {
                    failed_bots.push(bot.bot_uuid.clone());
                    errors.push(format!("{}: {}", bot.bot_uuid, error));
                }
            }
        }

        if !matched_bot_ids.is_empty() && failed_bots.len() == matched_bot_ids.len() {
            return Err(EnsureCurrentHumanActorError::AllMatchedBotsFailed {
                failed_bots,
                errors,
            });
        }

        Ok(EnsureCurrentHumanActorResult {
            human_id,
            human_created: human_result.created,
            matched_bots: matched_bot_ids,
            edges_created: total_created,
            edges_upgraded: total_upgraded,
            failed_bots,
            errors,
        })
    }
}

fn normalize_nick_name(nick_name: Option<String>) -> Option<String> {
    nick_name
        .as_deref()
        .map(str::trim)
        .filter(|nick| !nick.is_empty())
        .map(str::to_string)
}

fn resolve_human_nick_name(staff_no: &str, nick_name: Option<&str>) -> String {
    nick_name
        .map(str::trim)
        .filter(|nick| !nick.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| staff_no.to_string())
}

fn validate_staff_no(staff_no: &str) -> Result<(), String> {
    if staff_no.is_empty() || !staff_no.bytes().all(|byte| byte.is_ascii_alphanumeric()) {
        return Err(format!(
            "invalid staff_no: must be non-empty ASCII alphanumeric, got {:?}",
            staff_no
        ));
    }
    Ok(())
}
