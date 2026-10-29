use super::*;
pub(super) fn validate_snapshot_envelope(
    definition: &CollaborationDefinition,
    execution_plan: Option<&StateMachineExecutionPlanSnapshot>,
) -> ServiceResult<()> {
    let v2 = matches!(&definition.runtime,
        bcs_domain::CollaborationRuntimeDefinition::StateMachine(machine) if machine.version == 2);
    if v2 != execution_plan.is_some() {
        return Err(ServiceError::InternalError(
            "v2 snapshot requires an execution plan; v1 snapshot must omit it".into(),
        ));
    }
    if let Some(snapshot) = execution_plan {
        if snapshot.compiler_version.is_empty()
            || snapshot.compiler_version != snapshot.plan.compiler_version
            || snapshot.content_hash.len() != 64
            || !snapshot.content_hash.bytes().all(|byte| byte.is_ascii_hexdigit())
        {
            return Err(ServiceError::InternalError("invalid execution plan snapshot envelope".into()));
        }
    }
    Ok(())
}

pub(super) fn definition_json(definition: &CollaborationDefinition) -> ServiceResult<String> {
    serde_json::to_string(definition)
        .map_err(|error| ServiceError::InternalError(format!("definition serialize: {error}")))
}

pub(super) fn sha256_hex(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

pub(super) fn definition_conflict_error(
    definition_id: &str,
    version: i32,
    existing_hash: &str,
    incoming_hash: &str,
) -> ServiceError {
    ServiceError::Conflict(format!(
        "CollaborationDefinition '{}@{}' already exists with different content \
         (existing_hash={}, incoming_hash={})",
        definition_id,
        version,
        short_hash(existing_hash),
        short_hash(incoming_hash)
    ))
}

fn short_hash(value: &str) -> &str {
    value.get(..16).unwrap_or(value)
}
