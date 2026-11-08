//! One indexed, environment-scoped join per bounded authorization batch.
use super::*;
use bcs_service_api::port::repo::collaboration_actor::CollaborationActor;

impl DbProviderStore {
    pub(super) async fn collaboration_actor_rows(&self, ids: &[String]) -> ServiceResult<Vec<CollaborationActor>> {
        if ids.is_empty() { return Ok(Vec::new()); }
        if ids.len() > 256 { return Err(ServiceError::InvalidOperation { message: "collaboration actor batch exceeds 256".into(), request_id: None }); }
        let placeholders = vec!["?"; ids.len()].join(",");
        let sql = format!("SELECT b.bot_uuid, b.created_by, b.provider_id, p.config AS provider_config, p.disabled AS provider_disabled \
            FROM bcs_bots b LEFT JOIN bcs_providers p ON p.provider_id = b.provider_id AND p.env = b.env \
            WHERE b.env = ? AND COALESCE(b.is_deleted, 0) = 0 AND b.bot_uuid IN ({placeholders})");
        let mut params = vec![DbValue::from(resolve_env())];
        params.extend(ids.iter().map(|id| DbValue::from(id.as_str())));
        self.db.query(DbStatement::with_params(sql, params)).await
            .map_err(|_| ServiceError::InternalError("collaboration policy lookup failed".into()))?
            .iter().map(|row| {
                let read = || -> Result<CollaborationActor, DbError> {
                    Ok(CollaborationActor {
                        actor_id: row.get_string("bot_uuid")?.ok_or_else(|| DbError::Conversion("missing actor id".into()))?,
                        owner: row.get_string("created_by")?, provider_id: row.get_string("provider_id")?,
                        provider_config: row.get_string("provider_config")?,
                        provider_disabled: row.get_bool("provider_disabled")?.unwrap_or(false),
                    })
                };
                read().map_err(|_| ServiceError::InternalError("invalid collaboration actor projection".into()))
            }).collect()
    }
}
