//! Atomic invalidation uses two directed index ranges, not a whole-scope OR
//! scan. Outbound completes before inbound, so even a corrupt self-edge cannot
//! append the same invalidation audit twice. The caller owns the scope lock.
use super::*;
use bcs_db_api::{DbTransactionParam as Param, DbTransactionStep as Step};
impl DbAgentInvokeStore {
    pub(super) fn invalidation_steps(
        &self,
        scope_id: u64,
        f: &AgentInvokePublishedFact,
        registration: (u64, u64),
        revision: u64,
        start: usize,
    ) -> AgentInvokeResult<Vec<Step>> {
        let mut steps = Vec::new();
        for (side, column, index) in [
            ("from", "from_id", "idx_agent_invoke_edge_out"),
            ("to", "to_id", "idx_agent_invoke_edge_in"),
        ] {
            let from = if self.flavor == DbSqlFlavor::Mysql {
                format!(
                    "edge_grants e FORCE INDEX ({index}) STRAIGHT_JOIN agent_invoke_grants g ON e.id=g.edge_id"
                )
            } else {
                format!("edge_grants e INDEXED BY {index} CROSS JOIN agent_invoke_grants g")
            };
            let filter = format!(
                "e.id=g.edge_id AND g.scope_id=? AND e.env=? AND {}=? AND e.{column}=? AND {}=? AND e.grant_kind='agent_invoke' AND e.status='approved' AND (?=0 OR ?=0 OR ?=1 OR g.{side}_binding_generation<>? OR g.{side}_eligibility_generation<>? OR g.{side}_registration_id<>? OR g.{side}_registration_revision<>?)",
                self.exact("e.env"),
                self.exact(&format!("e.{column}"))
            );
            let params: Vec<DbValue> = vec![
                scope_id.into(),
                self.scope.env.as_str().into(),
                self.scope.env.as_str().into(),
                f.state.bot_id.as_str().into(),
                f.state.bot_id.as_str().into(),
                (f.state.eligible as i64).into(),
                (f.state.active as i64).into(),
                (f.state.hidden as i64).into(),
                f.state.binding_generation.get().into(),
                f.state.eligibility_generation.get().into(),
                registration.0.into(),
                registration.1.into(),
            ];
            let guard_index = start + steps.len();
            steps.push(Step::Query(DbStatement::with_params(format!("SELECT 1 AS ok WHERE NOT EXISTS (SELECT 1 FROM {from} WHERE {filter} AND g.version>=9223372036854775807)"),params.clone())));
            let action = if self.flavor == DbSqlFlavor::Mysql {
                format!("CONCAT('invalidate-{revision}-',e.id)")
            } else {
                format!("'invalidate-{revision}-' || e.id")
            };
            let before = "JSON_OBJECT('edge_id',e.id,'status','approved','version',g.version)";
            let after = "JSON_OBJECT('edge_id',e.id,'status','revoked','version',g.version+1)";
            let mut audit_params: Vec<Param> = params.iter().cloned().map(Param::Value).collect();
            audit_params.push(Param::query_result(guard_index, 0, "ok"));
            steps.push(Step::Execute(DbStatement::with_transaction_params(format!("INSERT INTO agent_invoke_audit (scope_id,action_id,action,principal_kind,principal_id,from_id,to_id,reason,before_json,after_json,created_at_ms) SELECT g.scope_id,{action},'qualification_invalidate','inventory','trusted-fact-publisher',e.from_id,e.to_id,'qualification or binding changed',{before},{after},{} FROM {from} WHERE {filter} AND ?=1",self.now_ms()),audit_params)));
            let (metadata, edges) = if self.flavor == DbSqlFlavor::Mysql {
                (
                    format!(
                        "UPDATE {from} SET g.version=g.version+1,g.updated_at_ms={} WHERE {filter}",
                        self.now_ms()
                    ),
                    format!(
                        "UPDATE {from} SET e.status='revoked',e.gmt_modified=CURRENT_TIMESTAMP WHERE {filter}"
                    ),
                )
            } else {
                (
                    format!(
                        "UPDATE agent_invoke_grants SET version=version+1,updated_at_ms={} WHERE edge_id IN (SELECT e.id FROM {from} WHERE {filter})",
                        self.now_ms()
                    ),
                    format!(
                        "UPDATE edge_grants SET status='revoked',gmt_modified=CURRENT_TIMESTAMP WHERE id IN (SELECT e.id FROM {from} WHERE {filter})"
                    ),
                )
            };
            steps.push(Step::Execute(DbStatement::with_params(
                metadata,
                params.clone(),
            )));
            steps.push(Step::Execute(DbStatement::with_params(edges, params)));
        }
        Ok(steps)
    }
}
