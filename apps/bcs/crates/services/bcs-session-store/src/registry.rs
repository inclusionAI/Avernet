//! Shared in-memory registry transaction state, also used by message admission.
use std::collections::HashMap;
use bcs_service_api::port::repo::session_registry::{SessionRegistration, SessionType};
use bcs_service_api::{ServiceError, ServiceResult};

#[derive(Debug, Default)]
pub struct MemorySessionRegistry {
    pub entries: tokio::sync::Mutex<HashMap<String, SessionRegistration>>,
}

pub fn check_type(row: &SessionRegistration, kind: SessionType) -> ServiceResult<()> {
    if row.session_type != kind {
        return Err(ServiceError::Conflict("session_type_conflict".into()));
    }
    if (kind == SessionType::Group && row.current_msg_seq.is_some())
        || (kind == SessionType::DirectA2a && row.current_msg_seq.is_none_or(|n| n < 0)) {
        return Err(ServiceError::InternalError("session_registry_invalid".into()));
    }
    Ok(())
}
