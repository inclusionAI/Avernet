//! Three Bot-owned Card columns. Routes/credentials are never persisted here.
use std::sync::Arc;
use async_trait::async_trait;
use bcs_db_api::{DbPlugin, DbStatement, DbValue, DbSqlFlavor};
use bcs_service_api::{ServiceError, ServiceResult};
use bcs_service_api::port::repo::a2a_bot::{A2aBotAvailability, A2aBotRecord, A2aBotRepoPort};

pub struct DbA2aBotStore { db: Arc<dyn DbPlugin>, env: String, flavor:DbSqlFlavor }

fn error(error: impl std::fmt::Display) -> ServiceError {
    ServiceError::InternalError(format!("A2A Bot persistence failed: {error}"))
}

fn parse_card(raw: &str) -> ServiceResult<serde_json::Value> {
    let card:serde_json::Value = serde_json::from_str(raw).map_err(error)?;
    if !card.is_object() { return Err(error("stored AgentCard must be an object")); }
    Ok(card)
}

impl DbA2aBotStore {
    pub fn new(db: Arc<dyn DbPlugin>, env: String, flavor:DbSqlFlavor) -> Self { Self { db, env, flavor } }
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
            predicates.push(format!("(INSTR(LOWER(bot_uuid), ?) > 0 OR ({sql}))"));
            params.push(DbValue::from(query.to_lowercase())); params.extend(values);
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
        let rows=self.db.query(DbStatement::with_params(format!("SELECT bot_uuid,provider_id,provider_bot_ref,{projection} AS interfaces FROM bcs_bots WHERE env=? AND is_deleted=0 AND agent_card IS NOT NULL AND bot_uuid IN ({placeholders})"),params)).await.map_err(error)?;
        rows.iter().map(|row| {
            let required=|column|row.get_string(column).map_err(error)?.ok_or_else(||error("missing binding"));
            let interfaces=row.get_string("interfaces").map_err(error)?.and_then(|s|serde_json::from_str::<serde_json::Value>(&s).ok()).unwrap_or(serde_json::Value::Null);
            Ok(A2aBotAvailability {bot_uuid:required("bot_uuid")?,provider_id:required("provider_id")?,provider_bot_ref:required("provider_bot_ref")?,
                ready:interfaces.as_array().is_some_and(|v|!v.is_empty()),interfaces})
        }).collect()
    }
    async fn get(&self, bot_uuid:&str)->ServiceResult<Option<A2aBotRecord>> {
        self.db.query(DbStatement::with_params("SELECT bot_uuid,provider_id,provider_bot_ref,agent_card,agent_card_url,agent_card_revision FROM bcs_bots WHERE env=? AND bot_uuid=? AND is_deleted=0 AND agent_card IS NOT NULL LIMIT 1",
            vec![self.env.clone().into(),bot_uuid.into()])).await.map_err(error)?.first().map(decode).transpose()
    }
    async fn compare_and_set(&self, record:&A2aBotRecord, expected_revision:u64)->ServiceResult<bool> {
        if expected_revision.checked_add(1)!=Some(record.revision) || !record.agent_card.is_object() {return Err(error("invalid revision or Card"));}
        let result=self.db.execute(DbStatement::with_params(
            "UPDATE bcs_bots SET agent_card=?,agent_card_url=?,agent_card_revision=?,gmt_modified=CURRENT_TIMESTAMP WHERE env=? AND bot_uuid=? AND provider_id=? AND provider_bot_ref=? AND is_deleted=0 AND connection_mode='gateway' AND agent_card_revision=?",
            vec![serde_json::to_string(&record.agent_card).map_err(error)?.into(),record.agent_card_url.clone().map(DbValue::from).unwrap_or(DbValue::Null),
                i64::try_from(record.revision).map_err(error)?.into(),self.env.clone().into(),record.bot_uuid.clone().into(),record.provider_id.clone().into(),
                record.provider_bot_ref.clone().into(),i64::try_from(expected_revision).map_err(error)?.into()])).await.map_err(error)?;
        Ok(result.affected_rows==1)
    }
    async fn refreshable(&self, after:Option<&str>, limit:u32)->ServiceResult<Vec<A2aBotRecord>> {
        let rows=self.db.query(DbStatement::with_params("SELECT bot_uuid,provider_id,provider_bot_ref,agent_card,agent_card_url,agent_card_revision FROM bcs_bots WHERE env=? AND is_deleted=0 AND agent_card_url IS NOT NULL AND agent_card IS NOT NULL AND bot_uuid>? ORDER BY bot_uuid LIMIT ?",
            vec![self.env.clone().into(),after.unwrap_or("").into(),i64::from(limit.clamp(1,16)).into()])).await.map_err(error)?;
        rows.iter().map(decode).collect()
    }
}
fn decode(row:&bcs_db_api::DbRow)->ServiceResult<A2aBotRecord> {
    let required=|column|row.get_string(column).map_err(error)?.ok_or_else(||error("missing binding or Card"));
    Ok(A2aBotRecord {bot_uuid:required("bot_uuid")?,provider_id:required("provider_id")?,provider_bot_ref:required("provider_bot_ref")?,
        agent_card_url:row.get_string("agent_card_url").map_err(error)?,agent_card:parse_card(&required("agent_card")?)?,
        revision:u64::try_from(row.get_i64("agent_card_revision").map_err(error)?.ok_or_else(||error("missing revision"))?).map_err(error)?})
}
pub(crate) fn validate_registration_card(bot:&bcs_service_api::bot_provider::BotProviderRecord, card:&A2aBotRecord)->ServiceResult<()> {
    if card.bot_uuid!=bot.bot_uuid || card.provider_id!=bot.provider_id || card.provider_bot_ref!=bot.provider_bot_ref || bot.is_deleted
        || bot.connection_mode!=bcs_service_api::bot_provider::BotConnectionMode::Gateway || card.revision!=1 || !card.agent_card.is_object() {
        return Err(error("invalid initial A2A Card"));
    } Ok(())
}
pub(crate) fn registration_card_step(card:&A2aBotRecord, env:&str)->ServiceResult<bcs_db_api::DbTransactionStep> {
    Ok(bcs_db_api::DbTransactionStep::ExecuteChecked {statement:DbStatement::with_params(
        "UPDATE bcs_bots SET agent_card=?,agent_card_url=?,agent_card_revision=1,gmt_modified=CURRENT_TIMESTAMP WHERE bot_uuid=? AND env=? AND provider_id=? AND provider_bot_ref=? AND connection_mode='gateway' AND is_deleted=0 AND agent_card IS NULL AND agent_card_revision=0",
        vec![serde_json::to_string(&card.agent_card).map_err(error)?.into(),card.agent_card_url.clone().map(DbValue::from).unwrap_or(DbValue::Null),
            card.bot_uuid.clone().into(),env.into(),card.provider_id.clone().into(),card.provider_bot_ref.clone().into()]),expected_affected_rows:1})
}
