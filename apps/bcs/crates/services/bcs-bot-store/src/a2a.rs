//! Bot-owned Card snapshot and separate catalog metadata; no stored routes/credentials.
use std::sync::Arc;
use serde_json::json;
use async_trait::async_trait;
use bcs_db_api::{DbPlugin, DbStatement, DbValue, DbSqlFlavor};
use bcs_service_api::{ServiceError, ServiceResult};
use bcs_service_api::port::repo::a2a_bot::{A2aBotAvailability, A2aBotRecord, A2aBotRepoPort};
use bcs_service_api::types::a2a_card_projection::project_agent_card_capabilities;

pub struct DbA2aBotStore { db: Arc<dyn DbPlugin>, env: String, flavor:DbSqlFlavor, registry:Option<std::sync::Weak<crate::PersistentBotRepo>> }

fn error(error: impl std::fmt::Display) -> ServiceError {
    ServiceError::InternalError(format!("A2A Bot persistence failed: {error}"))
}

fn parse_card(raw: &str) -> ServiceResult<serde_json::Value> {
    let card:serde_json::Value = serde_json::from_str(raw).map_err(error)?;
    if !card.is_object() { return Err(error("stored AgentCard must be an object")); }
    Ok(card)
}

impl DbA2aBotStore {
    pub fn new(db: Arc<dyn DbPlugin>, env: String, flavor:DbSqlFlavor) -> Self { Self { db, env, flavor, registry:None } }
    /// Share the live registry only for post-commit metadata invalidation.
    pub fn with_registry(mut self, registry:&Arc<crate::PersistentBotRepo>)->Self {
        self.registry=Some(Arc::downgrade(registry));self
    }
    pub fn sqlite(db: Arc<dyn DbPlugin>, env: String) -> Self { Self::new(db,env,DbSqlFlavor::Sqlite) }
}

#[async_trait]
impl A2aBotRepoPort for DbA2aBotStore {
    async fn discovery_cards(&self, bot_uuids: &[String], query: Option<&str>, skills: &[String])
        -> ServiceResult<std::collections::BTreeMap<String, Option<serde_json::Value>>> {
        if bot_uuids.is_empty() { return Ok(Default::default()); }
        if bot_uuids.len() > 256 { return Err(error("Card batch exceeds 256")); }
        let flavor = self.flavor;
        let mut predicates = Vec::new(); let mut params = Vec::new();
        if let Some(query) = query {
            let (sql, values) = crate::a2a_search::card_text_match(flavor, "agent_card", query);
            predicates.push(format!("(INSTR(LOWER(COALESCE(name, '')), ?) > 0 OR INSTR(LOWER(bot_uuid), ?) > 0 OR ({sql}))"));
            params.extend(vec![DbValue::from(query.to_lowercase());2]); params.extend(values);
        }
        for skill in skills {
            let (sql, values) = crate::a2a_search::card_skill_match(flavor, "agent_card", skill);
            predicates.push(sql); params.extend(values);
        }
        let predicate = if predicates.is_empty() { "1=1".to_string() } else { predicates.join(" AND ") };
        let placeholders = vec!["?";bot_uuids.len()].join(",");
        let sql = format!("SELECT bot_uuid, CASE WHEN ({predicate}) THEN agent_card ELSE NULL END AS matched_card \
            FROM bcs_bots WHERE env=? AND is_deleted=0 AND agent_card IS NOT NULL AND bot_uuid IN ({placeholders})");
        params.push(self.env.clone().into()); params.extend(bot_uuids.iter().cloned().map(DbValue::from));
        let rows = self.db.query(DbStatement::with_params(sql, params)).await.map_err(error)?;
        rows.iter().map(|row| {
            let id = row.get_string("bot_uuid").map_err(error)?.ok_or_else(||error("missing Bot id"))?;
            let card = row.get_string("matched_card").map_err(error)?.map(|raw|parse_card(&raw)).transpose()?;
            Ok((id,card))
        }).collect()
    }
    async fn cards_for_bots(&self, bot_uuids: &[String]) -> ServiceResult<std::collections::BTreeMap<String, serde_json::Value>> {
        if bot_uuids.is_empty() { return Ok(Default::default()); }
        if bot_uuids.len() > 256 { return Err(error("Card batch exceeds 256")); }
        let placeholders = vec!["?"; bot_uuids.len()].join(",");
        let sql = format!("SELECT bot_uuid, agent_card FROM bcs_bots WHERE env=? AND is_deleted=0 AND agent_card IS NOT NULL AND bot_uuid IN ({placeholders})");
        let mut params = vec![self.env.clone().into()];
        params.extend(bot_uuids.iter().cloned().map(DbValue::from));
        let rows = self.db.query(DbStatement::with_params(sql, params)).await.map_err(error)?;
        rows.iter().map(|row| {
            let id = row.get_string("bot_uuid").map_err(error)?.ok_or_else(||error("missing Bot id"))?;
            let raw = row.get_string("agent_card").map_err(error)?.ok_or_else(||error("missing Card"))?;
            Ok((id, parse_card(&raw)?))
        }).collect()
    }
    async fn availability_many(&self, bot_uuids: &[String]) -> ServiceResult<Vec<A2aBotAvailability>> {
        if bot_uuids.is_empty() {return Ok(vec![]);}
        if bot_uuids.len()>256 {return Err(error("availability batch exceeds 256"));}
        let placeholders=vec!["?";bot_uuids.len()].join(",");
        let mut params=vec![self.env.clone().into()];params.extend(bot_uuids.iter().cloned().map(DbValue::from));
        let projection="CASE WHEN JSON_VALID(agent_card) THEN JSON_EXTRACT(agent_card,'$.supportedInterfaces') ELSE NULL END";
        // MySQL CASE over JSON otherwise exposes a binary column to the driver.
        let projection=if self.flavor==DbSqlFlavor::Mysql {format!("CAST({projection} AS CHAR CHARACTER SET utf8mb4)")} else {projection.to_owned()};
        let authentication="CASE WHEN JSON_VALID(agent_card) THEN JSON_OBJECT('securitySchemes',JSON_EXTRACT(agent_card,'$.securitySchemes'),'securityRequirements',JSON_EXTRACT(agent_card,'$.securityRequirements')) ELSE NULL END";
        let authentication=if self.flavor==DbSqlFlavor::Mysql {format!("CAST({authentication} AS CHAR CHARACTER SET utf8mb4)")} else {authentication.to_owned()};
        let rows=self.db.query(DbStatement::with_params(format!("SELECT bot_uuid,provider_id,provider_bot_ref,{authentication} AS authentication,{projection} AS interfaces FROM bcs_bots WHERE env=? AND is_deleted=0 AND agent_card IS NOT NULL AND bot_uuid IN ({placeholders})"),params)).await.map_err(error)?;
        rows.iter().map(|row| {
            let required=|column|row.get_string(column).map_err(error)?.ok_or_else(||error("missing binding"));
            let interfaces=row.get_string("interfaces").map_err(error)?.and_then(|s|serde_json::from_str::<serde_json::Value>(&s).ok()).unwrap_or(serde_json::Value::Null);
            let mut authentication=row.get_string("authentication").map_err(error)?.and_then(|value|serde_json::from_str::<serde_json::Value>(&value).ok()).unwrap_or(serde_json::Value::Null);
            if let Some(object)=authentication.as_object_mut() {object.retain(|_,value|!value.is_null());}
            Ok(A2aBotAvailability {bot_uuid:required("bot_uuid")?,provider_id:required("provider_id")?,provider_bot_ref:required("provider_bot_ref")?,
                ready:interfaces.as_array().is_some_and(|v|!v.is_empty()),interfaces,authentication})
        }).collect()
    }
    async fn get(&self, bot_uuid:&str)->ServiceResult<Option<A2aBotRecord>> {
        self.db.query(DbStatement::with_params("SELECT bot_uuid,provider_id,provider_bot_ref,agent_card,agent_card_url,agent_card_revision,catalog_labels,visibility,status,user_visibility,agent_code FROM bcs_bots WHERE env=? AND bot_uuid=? AND is_deleted=0 AND agent_card IS NOT NULL LIMIT 1",
            vec![self.env.clone().into(),bot_uuid.into()])).await.map_err(error)?.first().map(decode).transpose()
    }
    async fn compare_and_set(&self,record:&A2aBotRecord,expected_revision:u64)->ServiceResult<bool> {
        if expected_revision.checked_add(1)!=Some(record.revision) {return Err(error("invalid revision"));}
        Ok(self.write_card(record,Some(expected_revision),None).await?.is_some())
    }
    async fn compare_and_set_with_visibility(&self,record:&A2aBotRecord,expected:u64,visibility:Option<bcs_service_api::types::a2a_bot::A2aRegistrationVisibility>)->ServiceResult<bool> {
        if expected.checked_add(1)!=Some(record.revision) {return Err(error("invalid revision"));}
        Ok(self.write_card(record,Some(expected),visibility).await?.is_some())
    }
    async fn replace(&self,record:&A2aBotRecord)->ServiceResult<A2aBotRecord> {
        for _ in 0..3 {
            if let Some(record)=self.write_card(record,None,None).await? {return Ok(record);}
        }
        Err(ServiceError::Conflict("A2A Bot binding unavailable or revision exhausted".into()))
    }
    async fn synchronize_card_projection(&self,record:&A2aBotRecord)->ServiceResult<()> {
        let name=bcs_service_api::types::a2a_names::card_bot_name(&record.agent_card).map_err(|message|ServiceError::InvalidOperation {message:message.into(),request_id:None})?;
        let (bot_info_assignment,bot_info_values)=crate::bot_info_json_set(card_bot_info_updates(&name,&record.agent_card)?)?;
        let mut params=vec![name.into()];
        params.extend(bot_info_values);
        params.push(self.env.clone().into());params.push(record.bot_uuid.clone().into());
        params.push(record.provider_id.clone().into());params.push(record.provider_bot_ref.clone().into());
        params.push(i64::try_from(record.revision).map_err(error)?.into());
        let result=self.db.execute(DbStatement::with_params(format!(
            "UPDATE bcs_bots SET name=?,{bot_info_assignment},gmt_modified=CURRENT_TIMESTAMP WHERE env=? AND bot_uuid=? AND provider_id=? AND provider_bot_ref=? AND is_deleted=0 AND connection_mode='gateway' AND agent_card_revision=?"),params)).await.map_err(error)?;
        // MySQL reports zero affected rows when the UPDATE matched the row but
        // every written value equalled the stored value (no CLIENT_FOUND_ROWS).
        // A matched-but-unchanged synchronize is a success, not a conflict:
        // re-check the fence before signalling a revision conflict.
        if result.affected_rows==0 && !card_projection_unchanged(self.db.as_ref(),self.env.as_str(),record).await? {
            return Err(ServiceError::Conflict("A2A Card configuration changed".into()));
        }
        if let Some(registry)=self.registry.as_ref().and_then(std::sync::Weak::upgrade) {
            registry.invalidate_registry_metadata(&self.env,&record.bot_uuid).await;
        }
        Ok(())
    }
    async fn get_by_name(&self,provider_id:&str,name:&str)->ServiceResult<Option<A2aBotRecord>> {
        let name_column=match self.flavor {DbSqlFlavor::Mysql=>"name COLLATE utf8mb4_bin",DbSqlFlavor::Sqlite=>"name COLLATE BINARY"};
        let rows=self.db.query(DbStatement::with_params(
            format!("SELECT bot_uuid,provider_id,provider_bot_ref,agent_card,agent_card_url,agent_card_revision,catalog_labels,visibility,status,user_visibility,agent_code FROM bcs_bots WHERE env=? AND provider_id=? AND {name_column}=? AND is_deleted=0 LIMIT 2"),
            vec![self.env.clone().into(),provider_id.into(),name.into()])).await.map_err(error)?;
        if rows.len()>1 { return Err(ServiceError::Conflict("Multiple active Bots use this name in the Provider".into())); }
        if rows.first().is_some_and(|row| matches!(row.get_string("agent_card"), Ok(None))) { return Ok(None); }
        Ok(rows.first().map(decode).transpose()?.filter(|record|record.agent_card.get("name").and_then(serde_json::Value::as_str)==Some(name)))
    }
    async fn get_by_provider_ref(&self, provider_id:&str, provider_bot_ref:&str)->ServiceResult<Option<A2aBotRecord>> {
        let ref_column=match self.flavor {DbSqlFlavor::Mysql=>"CAST(provider_bot_ref AS BINARY)=CAST(? AS BINARY)",DbSqlFlavor::Sqlite=>"provider_bot_ref=? COLLATE BINARY"};
        let rows=self.db.query(DbStatement::with_params(format!(
            "SELECT bot_uuid,provider_id,provider_bot_ref,agent_card,agent_card_url,agent_card_revision,catalog_labels,visibility,status,user_visibility,agent_code FROM bcs_bots WHERE env=? AND provider_id=? AND {ref_column} AND is_deleted=0 AND connection_mode='gateway' AND agent_card IS NOT NULL LIMIT 2"),
            vec![self.env.clone().into(),provider_id.into(),provider_bot_ref.into()])).await.map_err(error)?;
        if rows.len()>1 {return Err(error("multiple Bots use the same Provider reference"));}
        rows.first().map(decode).transpose()
    }
    async fn refreshable(&self, after:Option<&str>, limit:u32)->ServiceResult<Vec<A2aBotRecord>> {
        let rows=self.db.query(DbStatement::with_params("SELECT bot_uuid,provider_id,provider_bot_ref,agent_card,agent_card_url,agent_card_revision,catalog_labels,visibility,status,user_visibility,agent_code FROM bcs_bots WHERE env=? AND is_deleted=0 AND agent_card_url IS NOT NULL AND agent_card IS NOT NULL AND bot_uuid>? ORDER BY bot_uuid LIMIT ?",
            vec![self.env.clone().into(),after.unwrap_or("").into(),i64::from(limit.clamp(1,16)).into()])).await.map_err(error)?;
        rows.iter().map(decode).collect()
    }
}
fn visibility_from_storage(value:&str)->Option<bcs_service_api::types::a2a_bot::A2aRegistrationVisibility> {
    match value {
        "public" => Some(bcs_service_api::types::a2a_bot::A2aRegistrationVisibility::Public),
        "protected" => Some(bcs_service_api::types::a2a_bot::A2aRegistrationVisibility::Protected),
        "private" => Some(bcs_service_api::types::a2a_bot::A2aRegistrationVisibility::Private),
        _ => None,
    }
}

fn user_visibility_from_storage(value:&str)->Option<bcs_service_api::application::v1::UserVisibility> {
    match value {
        "public" => Some(bcs_service_api::application::v1::UserVisibility::Public),
        "protected" => Some(bcs_service_api::application::v1::UserVisibility::Protected),
        "private" => Some(bcs_service_api::application::v1::UserVisibility::Private),
        _ => None,
    }
}

fn user_visibility_to_storage(value:bcs_service_api::application::v1::UserVisibility)->&'static str {
    match value {
        bcs_service_api::application::v1::UserVisibility::Public => "public",
        bcs_service_api::application::v1::UserVisibility::Protected => "protected",
        bcs_service_api::application::v1::UserVisibility::Private => "private",
    }
}

fn decode(row:&bcs_db_api::DbRow)->ServiceResult<A2aBotRecord> {
    let required=|column|row.get_string(column).map_err(error)?.ok_or_else(||error("missing binding or Card"));
    // Native MySQL JSON may be UTF-8 bytes; retain strict conversion errors.
    let labels = bcs_db_api::db_get_column_opt::<String>(row, "catalog_labels").map_err(error)?;
    let visibility=bcs_db_api::db_get_column_opt::<String>(row, "visibility").map_err(error)?
        .and_then(|value|visibility_from_storage(&value))
        .unwrap_or_default();
    let status=bcs_db_api::db_get_column_opt::<String>(row, "status").map_err(error)?
        .as_deref().map(|value|match value {
            "hidden" => bcs_service_api::ActorStatus::Hidden,
            _ => bcs_service_api::ActorStatus::Online,
        }).unwrap_or(bcs_service_api::ActorStatus::Online);
    let user_visibility=bcs_db_api::db_get_column_opt::<String>(row, "user_visibility").map_err(error)?
        .and_then(|value|user_visibility_from_storage(&value))
        .unwrap_or_default();
    Ok(A2aBotRecord {catalog_labels:bcs_service_api::types::catalog_labels::CatalogLabels::from_storage(labels.as_deref())?,bot_uuid:required("bot_uuid")?,provider_id:required("provider_id")?,provider_bot_ref:required("provider_bot_ref")?,
        agent_card_url:row.get_string("agent_card_url").map_err(error)?,agent_card:parse_card(&required("agent_card")?)?,
        visibility,status,user_visibility,agent_id:row.get_string("agent_code").map_err(error)?,
        revision:u64::try_from(row.get_i64("agent_card_revision").map_err(error)?.ok_or_else(||error("missing revision"))?).map_err(error)?})
}
/// Whether the Card binding still satisfies the synchronize fence: the row is
/// present, not deleted, gateway-bound to this Provider Bot and still at the
/// expected revision. MySQL (without CLIENT_FOUND_ROWS) reports zero affected
/// rows for an UPDATE that matched a row whose values were all unchanged, so a
/// passing fence check distinguishes a no-op synchronize from a real conflict.
async fn card_projection_unchanged(db:&dyn DbPlugin,env:&str,record:&A2aBotRecord)->ServiceResult<bool> {
    let rows=db.query(DbStatement::with_params(
        "SELECT bot_uuid FROM bcs_bots WHERE env=? AND bot_uuid=? AND provider_id=? AND provider_bot_ref=? AND is_deleted=0 AND connection_mode='gateway' AND agent_card_revision=?",
        vec![env.into(),record.bot_uuid.clone().into(),record.provider_id.clone().into(),record.provider_bot_ref.clone().into(),i64::try_from(record.revision).map_err(error)?.into()])).await.map_err(error)?;
    Ok(!rows.is_empty())
}
pub(crate) fn validate_registration_card(bot:&bcs_service_api::bot_provider::BotProviderRecord, card:&A2aBotRecord)->ServiceResult<()> {
    if card.bot_uuid!=bot.bot_uuid || card.provider_id!=bot.provider_id || card.provider_bot_ref!=bot.provider_bot_ref || bot.is_deleted
        || bot.connection_mode!=bcs_service_api::bot_provider::BotConnectionMode::Gateway || card.revision!=1 || !card.agent_card.is_object() {
        return Err(error("invalid initial A2A Card"));
    }
    bcs_service_api::types::a2a_bot::a2a_management::source_agent(&card.agent_card)?;
    Ok(())
}
pub(crate) fn registration_card_steps(card:&A2aBotRecord,env:&str)->ServiceResult<Vec<bcs_db_api::DbTransactionStep>> {
    let name=bcs_service_api::types::a2a_names::card_bot_name(&card.agent_card).map_err(error)?;
    let (bot_info_assignment,bot_info_values)=crate::bot_info_json_set(card_bot_info_updates(&name,&card.agent_card)?)?;
    let mut params=vec![name.into()];
    params.extend(bot_info_values);
    params.push(serde_json::to_string(&card.agent_card).map_err(error)?.into());
    params.push(card.agent_card_url.clone().map(DbValue::from).unwrap_or(DbValue::Null));
    params.push(serde_json::to_string(&card.catalog_labels).map_err(error)?.into());
    params.push(card.bot_uuid.clone().into());params.push(env.into());
    params.push(card.provider_id.clone().into());params.push(card.provider_bot_ref.clone().into());
    Ok(vec![bcs_db_api::DbTransactionStep::ExecuteChecked {statement:DbStatement::with_params(format!(
        "UPDATE bcs_bots SET name=?,{bot_info_assignment},agent_card=?,agent_card_url=?,catalog_labels=?,agent_card_revision=1,gmt_modified=CURRENT_TIMESTAMP WHERE bot_uuid=? AND env=? AND provider_id=? AND provider_bot_ref=? AND connection_mode='gateway' AND is_deleted=0 AND agent_card IS NULL AND agent_card_revision=0"),
        params),expected_affected_rows:1}])
}

/// Rebuild the Card-owned `bot_info` keys from the committed AgentCard.
/// Every Card write (registration, replace/CAS, no-op synchronize) overwrites
/// the same five keys unconditionally — a Card that drops a field clears its
/// stored value instead of preserving a previous one. Keys owned by other
/// write paths (`agent_code`, `agent_token`, …) are left untouched.
pub(crate) fn card_bot_info_updates(name:&str,card:&serde_json::Value)->ServiceResult<Vec<(&'static str,serde_json::Value)>> {
    let projection=project_agent_card_capabilities(Some(card))?;
    Ok(vec![
        ("$.name",json!(name)),
        ("$.summary",json!(projection.summary.unwrap_or_default())),
        ("$.domains",json!(projection.domains.unwrap_or_default())),
        ("$.skills",json!(projection.skills.unwrap_or_default())),
        ("$.scopes",json!(projection.scopes.unwrap_or_default())),
    ])
}

impl DbA2aBotStore {
    async fn write_card(&self,record:&A2aBotRecord,expected:Option<u64>,visibility:Option<bcs_service_api::types::a2a_bot::A2aRegistrationVisibility>)->ServiceResult<Option<A2aBotRecord>> {
        use bcs_db_api::{DbTransactionStep,DbTransactionStepResult};
        let name=bcs_service_api::types::a2a_names::card_bot_name(&record.agent_card).map_err(|message|ServiceError::InvalidOperation {message:message.into(),request_id:None})?;
        let previous=self.get(&record.bot_uuid).await?;
        if expected.is_some_and(|revision|revision!=previous.as_ref().map_or(0,|value|value.revision)) {return Ok(None);}
        // Validate every incoming Card, including the first attachment to a legacy Bot.
        bcs_service_api::types::a2a_bot::a2a_management::source_agent(&record.agent_card)?;
        // Fence even an unconditional replacement against a concurrent Card write.
        // Caller retries the local CAS only.
        let expected=Some(expected.unwrap_or_else(||previous.as_ref().map_or(0,|v|v.revision)));
        // A2aBotRecord carries the complete management snapshot. Persist its
        // visibility even on compare_and_set: legacy `bcs_bots` rows default to
        // public, but the first Card attachment must commit the API's default
        // protected visibility. The override path keeps its explicit value.
        let visibility_assignment=format!("visibility='{}',",visibility.unwrap_or(record.visibility).as_str());
        let (bot_info_assignment,bot_info_values)=crate::bot_info_json_set(card_bot_info_updates(&name,&record.agent_card)?)?;
        let mut params=vec![name.into()];
        params.extend(bot_info_values);
        params.push(serde_json::to_string(&record.agent_card).map_err(error)?.into());
        params.push(record.agent_card_url.clone().map(DbValue::from).unwrap_or(DbValue::Null));
        params.push(serde_json::to_string(&record.catalog_labels).map_err(error)?.into());
        params.push(DbValue::from(match record.status { bcs_service_api::ActorStatus::Online=>"online", bcs_service_api::ActorStatus::Hidden=>"hidden" }));
        params.push(DbValue::from(user_visibility_to_storage(record.user_visibility)));
        params.push(record.agent_id.clone().map(DbValue::from).unwrap_or(DbValue::Null));
        params.push(self.env.clone().into());params.push(record.bot_uuid.clone().into());
        params.push(record.provider_id.clone().into());params.push(record.provider_bot_ref.clone().into());
        let predicate=if let Some(expected)=expected {params.push(i64::try_from(expected).map_err(error)?.into());" AND agent_card_revision=?"} else {""};
        let results=self.db.transaction(vec![
            DbTransactionStep::Execute(DbStatement::with_params(format!(
                "UPDATE bcs_bots SET {visibility_assignment}name=?,{bot_info_assignment},agent_card=?,agent_card_url=?,catalog_labels=?,status=?,user_visibility=?,agent_code=?,agent_card_revision=agent_card_revision+1,gmt_modified=CURRENT_TIMESTAMP WHERE env=? AND bot_uuid=? AND provider_id=? AND provider_bot_ref=? AND is_deleted=0 AND connection_mode='gateway' AND agent_card_revision<9223372036854775807{predicate}"),params).with_transaction_stop_on_no_rows()),
            DbTransactionStep::Query(DbStatement::with_params(
                "SELECT bot_uuid,provider_id,provider_bot_ref,agent_card,agent_card_url,agent_card_revision,catalog_labels,visibility,status,user_visibility,agent_code FROM bcs_bots WHERE env=? AND bot_uuid=?",vec![self.env.clone().into(),record.bot_uuid.clone().into()])),
        ]).await.map_err(|failure|if failure.is_duplicate_key() {ServiceError::Conflict("Bot name is already in use in this Provider".into())} else {error(failure)})?;
        match results.last() {
            Some(DbTransactionStepResult::Rows(rows)) if rows.len()==1=>{
                let committed=decode(&rows[0])?;
                if let Some(registry)=self.registry.as_ref().and_then(std::sync::Weak::upgrade) {
                    registry.invalidate_registry_metadata(&self.env,&record.bot_uuid).await;
                }
                Ok(Some(committed))
            },
            Some(DbTransactionStepResult::Executed(value)) if value.affected_rows==0=>{
                Ok(None)
            },
            _=>Err(error("invalid Card write result")),
        }
    }
}

/// Transitional preflight, not a cross-process uniqueness guarantee. No name cache:
/// reads current rows so deletion/rename releases names without a schema migration.
pub(crate) async fn ensure_provider_name_available(
    db:&dyn DbPlugin, flavor:DbSqlFlavor, env:&str, provider_id:&str,
    name:&str, exclude_bot:Option<&str>,
)->ServiceResult<()> {
    let column=match flavor {DbSqlFlavor::Mysql=>"name COLLATE utf8mb4_bin",DbSqlFlavor::Sqlite=>"name COLLATE BINARY"};
    let mut params=vec![env.into(),provider_id.into(),name.into()];
    let exclude=if let Some(id)=exclude_bot {params.push(id.into());" AND bot_uuid<>?"} else {""};
    let rows=db.query(DbStatement::with_params(format!(
        "SELECT bot_uuid FROM bcs_bots WHERE env=? AND provider_id=? AND is_deleted=0 AND {column}=?{exclude} LIMIT 1"),params)).await.map_err(error)?;
    if !rows.is_empty() {return Err(ServiceError::Conflict("Bot name is already in use in this Provider".into()));}
    Ok(())
}
