//! SQL-only transaction plan. Snapshot guards consume actual same-transaction
//! query results; an optimistic pre-read never grants permission to commit.
use super::*;

fn checked(statement: DbStatement) -> Step {
    Step::ExecuteChecked {
        statement,
        expected_affected_rows: 1,
    }
}
fn lit(v: impl Into<DbValue>) -> P {
    P::value(v)
}
fn aggregate(statement: &DbStatement, rows: &[DbRow]) -> AgentInvokeResult<DbStatement> {
    let mut fields = vec!["COUNT(*) AS guard_n".to_owned()];
    if let Some(row) = rows.first() {
        for key in row.columns().keys() {
            if key == "db_now_ms" {
                continue;
            }
            if !key.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_') {
                return Err(AgentInvokeError::InvalidState);
            }
            fields.push(format!("MAX(x.`{key}`) AS `{key}`"));
        }
    }
    Ok(DbStatement::with_params(
        format!("SELECT {} FROM ({}) x", fields.join(","), statement.sql()),
        statement.params().to_vec(),
    ))
}
fn edge_lookup(s: &DbAgentInvokeStore, c: &AgentInvokeCommand, lock: bool) -> DbStatement {
    let lock = if lock && s.flavor == DbSqlFlavor::Mysql {
        " FOR UPDATE"
    } else {
        ""
    };
    DbStatement::with_params(
        format!(
            "SELECT id FROM edge_grants WHERE env=? AND {}=? AND from_id=? AND {}=? AND to_id=? AND {}=? AND grant_kind='agent_invoke' AND grant_ref_id=0{lock}",
            s.exact("env"),
            s.exact("from_id"),
            s.exact("to_id")
        ),
        vec![
            s.scope.env.as_str().into(),
            s.scope.env.as_str().into(),
            c.pair.from_bot_id.as_str().into(),
            c.pair.from_bot_id.as_str().into(),
            c.pair.to_bot_id.as_str().into(),
            c.pair.to_bot_id.as_str().into(),
        ],
    )
}
fn guard(
    s: &DbAgentInvokeStore,
    c: &AgentInvokeCommand,
    m: &ManagementRead,
    steps: &mut Vec<Step>,
) -> AgentInvokeResult<()> {
    let mut predicates = Vec::new();
    let mut params = vec![
        lit(advance(m.pair.domain.revision.get())?.get()),
        lit(m.pair.domain.id),
        lit(m.pair.domain.revision.get()),
    ];
    for (statement, rows) in &m.groups {
        let index = steps.len();
        steps.push(Step::Query(aggregate(statement, rows)?));
        let mut expected = vec![("guard_n".to_owned(), DbValue::from(rows.len() as u64))];
        if let Some(row) = rows.first() {
            for (k, v) in row.columns() {
                if k != "db_now_ms" {
                    expected.push((k.clone(), v.clone()));
                }
            }
        }
        for (column, value) in expected {
            predicates.push(if s.flavor == DbSqlFlavor::Mysql {
                "CAST(? AS BINARY) <=> CAST(? AS BINARY)"
            } else {
                "? IS ? COLLATE BINARY"
            });
            params.extend([P::query_result(index, 0, column), P::Value(value)]);
        }
    }
    if matches!(
        c.change,
        AgentInvokeChange::Grant { .. }
            | AgentInvokeChange::Request
            | AgentInvokeChange::Approve { .. }
    ) {
        for key in ["source_valid_until_ms", "target_valid_until_ms"] {
            predicates.push("? > CURRENT_TIME_PLACEHOLDER");
            params.push(lit(positive(&m.pair.row, key)?.get()));
        }
    }
    let predicate = predicates
        .join(" AND ")
        .replace("CURRENT_TIME_PLACEHOLDER", s.now_ms());
    steps.push(checked(DbStatement::with_transaction_params(format!("UPDATE agent_invoke_scopes SET revision=? WHERE id=? AND revision=? AND writer_enabled=1 AND runtime_enabled=1 AND {predicate}"),params)));
    Ok(())
}

pub(super) fn build(
    s: &DbAgentInvokeStore,
    c: &AgentInvokeCommand,
    m: &ManagementRead,
    p: &Plan,
) -> AgentInvokeResult<Vec<Step>> {
    let mut steps = vec![Step::Query(s.scope_statement(true))];
    let mut bots = [c.pair.from_bot_id.as_str(), c.pair.to_bot_id.as_str()];
    bots.sort();
    for id in bots {
        steps.push(Step::Query(s.bot_lock_statement(id)));
    }
    // Human lifecycle changes must not race role/registration revalidation.
    let mut humans = Vec::new();
    for principal in [Some(&c.principal), m.snapshot.request_creator.as_ref()]
        .into_iter()
        .flatten()
    {
        if let AgentInvokePrincipal::Human(id) = principal {
            humans.push(id.as_str());
        }
    }
    humans.sort();
    humans.dedup();
    for id in humans {
        steps.push(Step::Query(s.bot_lock_statement(id)));
    }
    steps.push(Step::Query(edge_lookup(s, c, true)));
    if let Some(id) = management_reads::request_id(c) {
        let lock = if s.flavor == DbSqlFlavor::Mysql {
            " FOR UPDATE"
        } else {
            ""
        };
        steps.push(Step::Query(DbStatement::with_params(
            format!(
                "SELECT request_id FROM permission_requests WHERE request_id=? AND {}=?{lock}",
                s.exact("request_id")
            ),
            vec![id.into(), id.into()],
        )));
    }
    guard(s, c, m, &mut steps)?;
    let edge_id = if let Some(g) = &p.grant {
        if let Some(id) = g.id {
            P::value(id)
        } else {
            steps.push(checked(DbStatement::with_params("INSERT INTO edge_grants (env,from_id,to_id,grant_kind,grant_ref_id,rules,status,originator_policy_type,originator_policy_data) VALUES (?,?,?,'agent_invoke',0,NULL,'approved','same_as_from',NULL)",vec![s.scope.env.as_str().into(),c.pair.from_bot_id.as_str().into(),c.pair.to_bot_id.as_str().into()])));
            let index = steps.len();
            steps.push(Step::Query(edge_lookup(s, c, false)));
            P::query_result(index, 0, "id")
        }
    } else {
        m.pair
            .approval
            .as_ref()
            .map(|a| P::value(a.edge.edge_id))
            .unwrap_or(P::value(DbValue::Null))
    };
    if let Some(g) = &p.grant {
        let old = m.pair.approval.as_ref();
        if let Some(old) = old {
            if old.edge.status != g.status {
                steps.push(checked(DbStatement::with_params("UPDATE edge_grants SET status=?,gmt_modified=CURRENT_TIMESTAMP WHERE id=? AND status=? AND grant_kind='agent_invoke' AND grant_ref_id=0",vec![(if g.status==EdgeStatus::Approved{"approved"}else{"revoked"}).into(),old.edge.edge_id.into(),(if old.edge.status==EdgeStatus::Approved{"approved"}else{"revoked"}).into()])));
            }
            if old.version != g.version && g.status == EdgeStatus::Revoked {
                steps.push(checked(DbStatement::with_transaction_params(format!("UPDATE agent_invoke_grants SET version=?,updated_at_ms={} WHERE edge_id=? AND scope_id=? AND version=?",s.now_ms()),vec![lit(g.version.get()),edge_id.clone(),lit(m.pair.domain.id),lit(old.version.get())])));
            }
            if old.version != g.version && g.status == EdgeStatus::Approved {
                let mut values = vec![lit(g.version.get())];
                values.extend(registration_values(m)?);
                values.extend(generation_values(m));
                values.extend([
                    edge_id.clone(),
                    lit(m.pair.domain.id),
                    lit(old.version.get()),
                ]);
                steps.push(checked(DbStatement::with_transaction_params(format!("UPDATE agent_invoke_grants SET version=?,from_registration_id=?,to_registration_id=?,from_registration_revision=?,to_registration_revision=?,from_binding_generation=?,to_binding_generation=?,from_eligibility_generation=?,to_eligibility_generation=?,updated_at_ms={} WHERE edge_id=? AND scope_id=? AND version=?",s.now_ms()),values)));
            }
        } else {
            let mut values = vec![edge_id.clone(), lit(m.pair.domain.id), lit(g.version.get())];
            values.extend(registration_values(m)?);
            values.extend(generation_values(m));
            steps.push(checked(DbStatement::with_transaction_params(format!("INSERT INTO agent_invoke_grants (edge_id,scope_id,version,from_registration_id,to_registration_id,from_registration_revision,to_registration_revision,from_binding_generation,to_binding_generation,from_eligibility_generation,to_eligibility_generation,created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,{},{})",s.now_ms(),s.now_ms()),values)));
        }
    }
    if let Some(r) = &p.request {
        if r.new {
            steps.push(checked(DbStatement::with_params("INSERT INTO permission_requests (request_id,env,from_id,to_id,request_kind,requested_ref_id,requested_rules,message,status,created_by) VALUES (?,?,?,?,'agent_invoke',0,NULL,?,'pending',?)",vec![r.id.as_str().into(),s.scope.env.as_str().into(),c.pair.from_bot_id.as_str().into(),c.pair.to_bot_id.as_str().into(),c.reason.as_str().into(),c.principal.id().into()])));
            let mut values = vec![
                lit(r.id.as_str()),
                lit(m.pair.domain.id),
                lit(r.version.get()),
                lit(principal_kind(&c.principal)),
                lit(number(
                    &m.pair.row,
                    "source_registration_ownership_version",
                )?),
                lit(number(&m.authority, "source_epoch")?),
                lit(m.pair.approval.as_ref().map_or(0, |a| a.version.get())),
            ];
            values.extend(registration_values(m)?);
            values.extend(generation_values(m));
            let (actor_id, actor_revision, delegation) = match &c.principal {
                AgentInvokePrincipal::Human(_) => (
                    positive(&m.authority, "operator_registration_id")?.get(),
                    positive(&m.authority, "operator_registration_revision")?.get(),
                    0,
                ),
                AgentInvokePrincipal::Governance(id) => (0, 0, delegation_revision(m, id)?),
                _ => return Err(AgentInvokeError::Forbidden),
            };
            values.extend([lit(actor_id), lit(actor_revision), lit(delegation)]);
            steps.push(checked(DbStatement::with_transaction_params("INSERT INTO agent_invoke_requests (request_id,scope_id,version,creator_kind,source_ownership_version,source_authority_sequence,edge_version,from_registration_id,to_registration_id,from_registration_revision,to_registration_revision,from_binding_generation,to_binding_generation,from_eligibility_generation,to_eligibility_generation,requester_registration_id,requester_registration_revision,requester_delegation_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",values)));
            steps.push(checked(DbStatement::with_params("INSERT INTO agent_invoke_pending_pairs (scope_id,from_id,to_id,request_id) VALUES (?,?,?,?)",vec![m.pair.domain.id.into(),c.pair.from_bot_id.as_str().into(),c.pair.to_bot_id.as_str().into(),r.id.as_str().into()])));
        } else {
            let ref_id = if r.status == RequestStatus::Approved {
                edge_id.clone()
            } else {
                P::value(DbValue::Null)
            };
            steps.push(checked(DbStatement::with_transaction_params("UPDATE permission_requests SET status=?,edge_id=?,decided_by=?,decided_at=CURRENT_TIMESTAMP,gmt_modified=CURRENT_TIMESTAMP WHERE request_id=? AND request_kind='agent_invoke' AND status='pending'",vec![lit(request_status(r.status)),ref_id,lit(c.principal.id()),lit(r.id.as_str())])));
            let old = p
                .before_request
                .as_ref()
                .ok_or(AgentInvokeError::InvalidState)?;
            steps.push(checked(DbStatement::with_params("UPDATE agent_invoke_requests SET version=? WHERE request_id=? AND scope_id=? AND version=?",vec![r.version.get().into(),r.id.as_str().into(),m.pair.domain.id.into(),old.version.get().into()])));
            steps.push(checked(DbStatement::with_params("DELETE FROM agent_invoke_pending_pairs WHERE scope_id=? AND request_id=? AND from_id=? AND to_id=?",vec![m.pair.domain.id.into(),r.id.as_str().into(),c.pair.from_bot_id.as_str().into(),c.pair.to_bot_id.as_str().into()])));
        }
    }
    append_records(s, c, m, p, edge_id, &mut steps)?;
    Ok(steps)
}
fn registration_values(m: &ManagementRead) -> AgentInvokeResult<Vec<P>> {
    Ok([
        "source_registration_id",
        "target_registration_id",
        "source_registration_agent_invoke_identity_revision",
        "target_registration_agent_invoke_identity_revision",
    ]
    .iter()
    .map(|key| positive(&m.pair.row, key).map(|v| lit(v.get())))
    .collect::<AgentInvokeResult<Vec<_>>>()?)
}
fn generation_values(m: &ManagementRead) -> Vec<P> {
    [
        m.snapshot.source.binding_generation,
        m.snapshot.target.binding_generation,
        m.snapshot.source.eligibility_generation,
        m.snapshot.target.eligibility_generation,
    ]
    .into_iter()
    .map(|v| lit(v.get()))
    .collect()
}

struct Json {
    params: Vec<P>,
}
impl Json {
    fn value(&mut self, p: P) -> String {
        self.params.push(p);
        "?".into()
    }
    fn grant(&mut self, grant: Option<(&GrantPlan, P)>) -> String {
        match grant {
            None => "NULL".into(),
            Some((g, id)) => {
                let id = self.value(id);
                let status = self.value(lit(if g.status == EdgeStatus::Approved {
                    "approved"
                } else {
                    "revoked"
                }));
                let version = self.value(lit(g.version.get()));
                format!("JSON_OBJECT('edge_id',{id},'status',{status},'version',{version})")
            }
        }
    }
    fn request(&mut self, r: Option<&RequestPlan>, edge: P) -> String {
        match r {
            None => "NULL".into(),
            Some(r) => {
                let id = self.value(lit(r.id.as_str()));
                let status = self.value(lit(request_status(r.status)));
                let version = self.value(lit(r.version.get()));
                let edge = self.value(if r.status == RequestStatus::Approved {
                    edge
                } else {
                    lit(DbValue::Null)
                });
                format!(
                    "JSON_OBJECT('request_id',{id},'status',{status},'version',{version},'edge_id',{edge})"
                )
            }
        }
    }
}
fn append_records(
    s: &DbAgentInvokeStore,
    c: &AgentInvokeCommand,
    m: &ManagementRead,
    p: &Plan,
    edge: P,
    steps: &mut Vec<Step>,
) -> AgentInvokeResult<()> {
    let old = m.pair.approval.as_ref().map(|a| GrantPlan {
        id: Some(a.edge.edge_id),
        version: a.version,
        status: a.edge.status,
    });
    let original = p.grant.as_ref().map(|g| (g, edge.clone()));
    let current = p
        .grant
        .as_ref()
        .map(|g| (g, edge.clone()))
        .or_else(|| old.as_ref().map(|g| (g, edge.clone())));
    let mut audit = Json {
        params: vec![
            lit(m.pair.domain.id),
            lit(p.action_id.as_str()),
            lit(action_name(c.change.action())),
            lit(principal_kind(&c.principal)),
            lit(c.principal.id()),
            lit(c.pair.from_bot_id.as_str()),
            lit(c.pair.to_bot_id.as_str()),
            lit(c.reason.as_str()),
            lit(audit_before(m, p)?),
        ],
    };
    let grant = audit.grant(current.clone());
    let request = audit.request(p.request.as_ref(), edge.clone());
    let mut binding_fields = Vec::new();
    if p.grant.is_some() {
        for (side, prefix, state) in [
            ("from", "source", &m.snapshot.source),
            ("to", "target", &m.snapshot.target),
        ] {
            let approved = p
                .grant
                .as_ref()
                .is_some_and(|g| g.status == EdgeStatus::Approved);
            for (column, value) in [
                (
                    "binding_generation",
                    if approved {
                        state.binding_generation.get()
                    } else {
                        number(&m.pair.row, &format!("approval_{side}_binding_generation"))?
                    },
                ),
                (
                    "eligibility_generation",
                    if approved {
                        state.eligibility_generation.get()
                    } else {
                        number(
                            &m.pair.row,
                            &format!("approval_{side}_eligibility_generation"),
                        )?
                    },
                ),
                (
                    "registration_id",
                    if approved {
                        positive(&m.pair.row, &format!("{prefix}_registration_id"))?.get()
                    } else {
                        number(&m.pair.row, &format!("approval_{side}_registration_id"))?
                    },
                ),
                (
                    "registration_revision",
                    if approved {
                        positive(
                            &m.pair.row,
                            &format!("{prefix}_registration_agent_invoke_identity_revision"),
                        )?
                        .get()
                    } else {
                        number(
                            &m.pair.row,
                            &format!("approval_{side}_registration_revision"),
                        )?
                    },
                ),
            ] {
                binding_fields.push(format!("'{side}_{column}',{}", audit.value(lit(value))));
            }
        }
    }
    let binding = if binding_fields.is_empty() {
        "NULL".to_owned()
    } else {
        format!("JSON_OBJECT({})", binding_fields.join(","))
    };
    let scope_revision = audit.value(lit(advance(m.pair.domain.revision.get())?.get()));
    let decision_fence = audit.value(lit(m.snapshot.fence.as_str()));

    steps.push(checked(DbStatement::with_transaction_params(format!("INSERT INTO agent_invoke_audit (scope_id,action_id,action,principal_kind,principal_id,from_id,to_id,reason,before_json,after_json,created_at_ms) VALUES (?,?,?,?,?,?,?,?,?,JSON_OBJECT('grant',{grant},'request',{request},'approval_binding',{binding},'scope_revision',{scope_revision},'decision_fence',{decision_fence}),{})",s.now_ms()),audit.params)));
    let hash = principal_hash(&c.principal);
    let key = DbValue::Bytes(c.idempotency_key.as_bytes().to_vec());
    let mut json = Json {
        params: vec![
            lit(m.pair.domain.id),
            lit(hash.as_str()),
            lit(key.clone()),
            lit(principal_kind(&c.principal)),
            lit(c.principal.id()),
            lit(command_json(c)),
        ],
    };
    let action = json.value(lit(p.action_id.as_str()));
    let og = json.grant(original);
    let or = json.request(p.request.as_ref(), edge.clone());
    let cg = json.grant(current);
    let cr = json.request(p.request.as_ref(), edge);
    let action_column = json.value(lit(p.action_id.as_str()));
    steps.push(checked(DbStatement::with_transaction_params(format!("INSERT INTO agent_invoke_receipts (scope_id,principal_hash,idempotency_key,principal_kind,principal_id,command_json,receipt_json,action_id) VALUES (?,?,?,?,?,?,JSON_OBJECT('action_id',{action},'original_grant',{og},'original_request',{or},'current_grant',{cg},'current_request',{cr}),{action_column})"),json.params)));
    steps.push(Step::Query(DbStatement::with_params("SELECT receipt_json FROM agent_invoke_receipts WHERE scope_id=? AND principal_hash=? AND idempotency_key=?",vec![m.pair.domain.id.into(),hash.into(),key])));
    Ok(())
}

fn audit_before(m: &ManagementRead, p: &Plan) -> AgentInvokeResult<String> {
    let mut value: serde_json::Value = serde_json::from_str(&view_json(
        p.before_grant.as_ref(),
        p.before_request.as_ref(),
    ))
    .map_err(|_| AgentInvokeError::InvalidState)?;
    if m.pair.approval.is_some() {
        let mut binding = serde_json::Map::new();
        for side in ["from", "to"] {
            for column in [
                "binding_generation",
                "eligibility_generation",
                "registration_id",
                "registration_revision",
            ] {
                binding.insert(
                    format!("{side}_{column}"),
                    number(&m.pair.row, &format!("approval_{side}_{column}"))?.into(),
                );
            }
        }
        value["approval_binding"] = serde_json::Value::Object(binding);
    } else {
        value["approval_binding"] = serde_json::Value::Null;
    }
    Ok(value.to_string())
}
