use super::*;
pub(super) struct GrantPlan {
    pub id: Option<u64>,
    pub version: NonZeroU64,
    pub status: EdgeStatus,
}
pub(super) struct RequestPlan {
    pub id: String,
    pub version: NonZeroU64,
    pub status: RequestStatus,
    pub new: bool,
}
pub(super) struct Plan {
    pub action_id: String,
    pub grant: Option<GrantPlan>,
    pub request: Option<RequestPlan>,
    pub before_grant: Option<AgentInvokeGrantView>,
    pub before_request: Option<AgentInvokeRequestView>,
}
impl Plan {
    pub fn prepare(
        s: &DbAgentInvokeStore,
        c: &AgentInvokeCommand,
        m: &ManagementRead,
        counts: &DbRow,
    ) -> AgentInvokeResult<Self> {
        let old = m.pair.approval.as_ref();
        let version = old.map_or(0, |a| a.version.get());
        let before_grant = old.map(grant_view).transpose()?;
        let before_request = m.request.first().map(request_view).transpose()?;
        let mut p = Self {
            action_id: uuid::Uuid::new_v4().to_string(),
            grant: None,
            request: None,
            before_grant,
            before_request,
        };
        let fresh = matches!(
            c.change,
            AgentInvokeChange::Grant { .. }
                | AgentInvokeChange::Request
                | AgentInvokeChange::Approve { .. }
        );
        if fresh
            && [&m.snapshot.source, &m.snapshot.target]
                .iter()
                .any(|b| !b.eligible || !b.active || b.hidden)
        {
            return Err(AgentInvokeError::Forbidden);
        }
        let approve = || -> AgentInvokeResult<GrantPlan> {
            if !old.is_some_and(|a| a.edge.status == EdgeStatus::Approved)
                && (number(counts, "out_active")? >= s.limits.max_out_degree.get() as u64
                    || number(counts, "in_active")? >= s.limits.max_in_degree.get() as u64)
            {
                return Err(AgentInvokeError::LimitExceeded);
            }
            Ok(GrantPlan {
                id: old.map(|a| a.edge.edge_id),
                version: advance(version)?,
                status: EdgeStatus::Approved,
            })
        };
        match &c.change {
            AgentInvokeChange::Grant { expected_version } => {
                if *expected_version != version {
                    return Err(AgentInvokeError::Conflict);
                }
                p.grant = Some(approve()?);
            }
            AgentInvokeChange::Request => {
                if number(counts, "pair_pending")? > 0 {
                    return Err(AgentInvokeError::Conflict);
                }
                if number(counts, "out_pending")? >= s.limits.max_pending_out.get() as u64
                    || number(counts, "in_pending")? >= s.limits.max_pending_in.get() as u64
                {
                    return Err(AgentInvokeError::LimitExceeded);
                }
                p.request = Some(RequestPlan {
                    id: uuid::Uuid::new_v4().to_string(),
                    version: NonZeroU64::MIN,
                    status: RequestStatus::Pending,
                    new: true,
                });
            }
            AgentInvokeChange::Approve {
                request_id,
                expected_version,
            }
            | AgentInvokeChange::Reject {
                request_id,
                expected_version,
            }
            | AgentInvokeChange::Cancel {
                request_id,
                expected_version,
            } => {
                let r = p
                    .before_request
                    .as_ref()
                    .ok_or(AgentInvokeError::NotFound)?;
                if &r.request_id != request_id {
                    return Err(AgentInvokeError::NotFound);
                }
                if r.version != *expected_version || r.status != RequestStatus::Pending {
                    return Err(AgentInvokeError::Conflict);
                }
                let status = match &c.change {
                    AgentInvokeChange::Approve { .. } => {
                        let metadata = m.request.first().ok_or(AgentInvokeError::NotFound)?;
                        if !m.snapshot.requester_source_authorized
                            || number(metadata, "edge_version")? != version
                        {
                            return Err(AgentInvokeError::Conflict);
                        }
                        for (field, value) in [
                            (
                                "from_binding_generation",
                                m.snapshot.source.binding_generation.get(),
                            ),
                            (
                                "to_binding_generation",
                                m.snapshot.target.binding_generation.get(),
                            ),
                            (
                                "from_eligibility_generation",
                                m.snapshot.source.eligibility_generation.get(),
                            ),
                            (
                                "to_eligibility_generation",
                                m.snapshot.target.eligibility_generation.get(),
                            ),
                            (
                                "source_ownership_version",
                                number(&m.pair.row, "source_registration_ownership_version")?,
                            ),
                            (
                                "source_authority_sequence",
                                number(&m.authority, "source_epoch")?,
                            ),
                        ] {
                            if number(metadata, field)? != value {
                                return Err(AgentInvokeError::Conflict);
                            }
                        }
                        for (side, prefix) in [("from", "source"), ("to", "target")] {
                            for (column, registered) in [
                                ("registration_id", "id"),
                                ("registration_revision", "agent_invoke_identity_revision"),
                            ] {
                                if positive(metadata, &format!("{side}_{column}"))?.get()
                                    != positive(
                                        &m.pair.row,
                                        &format!("{prefix}_registration_{registered}"),
                                    )?
                                    .get()
                                {
                                    return Err(AgentInvokeError::Conflict);
                                }
                            }
                        }
                        match m
                            .snapshot
                            .request_creator
                            .as_ref()
                            .ok_or(AgentInvokeError::InvalidState)?
                        {
                            AgentInvokePrincipal::Human(_) => {
                                for field in [
                                    "requester_registration_id",
                                    "requester_registration_revision",
                                ] {
                                    if positive(metadata, field)? != positive(&m.authority, field)?
                                    {
                                        return Err(AgentInvokeError::Conflict);
                                    }
                                }
                            }
                            AgentInvokePrincipal::Governance(id) => {
                                let revision = delegation_revision(m, id)?;
                                if positive(metadata, "requester_delegation_revision")?.get()
                                    != revision
                                {
                                    return Err(AgentInvokeError::Conflict);
                                }
                            }
                            _ => return Err(AgentInvokeError::Conflict),
                        }
                        p.grant = Some(approve()?);
                        RequestStatus::Approved
                    }
                    AgentInvokeChange::Reject { .. } => RequestStatus::Rejected,
                    AgentInvokeChange::Cancel { .. } => RequestStatus::Cancelled,
                    _ => return Err(AgentInvokeError::InvalidState),
                };
                p.request = Some(RequestPlan {
                    id: r.request_id.clone(),
                    version: advance(r.version.get())?,
                    status,
                    new: false,
                });
            }
            AgentInvokeChange::Revoke {
                edge_id,
                expected_version,
            } => {
                let old = old.ok_or(AgentInvokeError::NotFound)?;
                if old.edge.edge_id != edge_id.get() {
                    return Err(AgentInvokeError::NotFound);
                }
                if old.version != *expected_version {
                    return Err(AgentInvokeError::Conflict);
                }
                p.grant = Some(GrantPlan {
                    id: Some(old.edge.edge_id),
                    version: if old.edge.status == EdgeStatus::Approved {
                        advance(version)?
                    } else {
                        old.version
                    },
                    status: EdgeStatus::Revoked,
                });
            }
        }
        Ok(p)
    }
}
pub(super) fn delegation_revision(m: &ManagementRead, id: &str) -> AgentInvokeResult<u64> {
    for (_, rows) in &m.groups {
        for row in rows {
            if row.get("actions_json").is_some() && string(row, "principal_id")? == id {
                return Ok(positive(row, "revision")?.get());
            }
        }
    }
    Err(AgentInvokeError::Conflict)
}
