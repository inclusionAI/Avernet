use axum::response::{IntoResponse, Response};
use bcs_domain::GroupContextEntry;
use bcs_service_api::SessionLaunchError;
use serde::Deserialize;

use crate::routes::group_context::GroupContextHttpError;
use crate::state::HttpAppState;

use super::session_launch_error_to_legacy;

#[derive(Debug, Deserialize)]
pub struct ContextInheritance {
    context_from_session_id: Option<String>,
    context_names: Option<Vec<String>>,
}

impl ContextInheritance {
    pub(super) async fn resolve(
        self,
        state: &HttpAppState,
        group_id: &str,
        actor_id: &str,
        reactivating: bool,
    ) -> Result<Vec<GroupContextEntry>, Response> {
        match (self.context_from_session_id, self.context_names) {
            (None, None) => Ok(Vec::new()),
            (Some(source), names) if !reactivating => state
                .services
                .group_context
                .inheritance_sources(group_id, &source, actor_id, names.as_deref())
                .await
                .map_err(|error| GroupContextHttpError::from(error).into_response()),
            _ => Err(session_launch_error_to_legacy(SessionLaunchError::InvalidRequest(
                "context inheritance requires a new session, a source session id".into(),
            ))),
        }
    }
}
