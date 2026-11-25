#![allow(clippy::unwrap_used, reason = "query policy and consumer assertions")]
use crate::agent_invoke::{AgentInvokeQueryApplicationService, AgentInvokeQueryPolicy};
use bcs_domain::edge_permission::EdgeStatus;
use bcs_service_api::{
    application::agent_invoke::AgentInvokeGovernanceQueryService,
    core::agent_invoke::AgentInvokeQueryPolicyCoreService,
    port::repo::agent_invoke::AgentInvokeQueryRepoPort,
    types::{agent_invoke::*, agent_invoke_query::*},
};
use std::{
    num::{NonZeroU16, NonZeroU64},
    sync::{
        Arc,
        atomic::{AtomicU8, AtomicUsize, Ordering},
    },
};
fn scope() -> AgentInvokeScope {
    AgentInvokeScope {
        namespace: "unit-query".into(),
        env: "dev".into(),
    }
}
fn input(filter: AgentInvokeQueryFilter) -> AgentInvokeQuery {
    let page = filter.is_page().then(AgentInvokeQueryPage::default);
    AgentInvokeQuery {
        context: AgentInvokeQueryContext {
            scope: scope(),
            principal: AgentInvokePrincipal::Human("human_owner".into()),
            target: AgentInvokeQueryTarget {
                bot_id: "bot-a".into(),
                peer_bot_id: None,
            },
            filter,
        },
        page,
    }
}
fn facts(q: &AgentInvokeQuery) -> AgentInvokeQueryFacts {
    AgentInvokeQueryFacts {
        context: q.context.clone(),
        operator_live: true,
        anchor_live: true,
        ownership_version: 1,
        owner_count: 1,
        canonical_owner_count: 1,
        anchor_role: Some(AgentInvokeRole::Owner),
        delegated_actions: vec![],
        reverse_delegated_actions: vec![],
    }
}
#[test]
fn query_validation_is_closed_for_scope_identity_and_resource_shapes() {
    let p = AgentInvokeQueryPolicy;
    let q = input(AgentInvokeQueryFilter::Degrees);
    let mut bad = q.clone();
    bad.context.scope.env = "other".into();
    assert_eq!(
        p.validate_query(&scope(), &bad),
        Err(AgentInvokeError::Forbidden)
    );
    for principal in [
        AgentInvokePrincipal::Human(" ".into()),
        AgentInvokePrincipal::Human("wrong".into()),
    ] {
        let mut bad = q.clone();
        bad.context.principal = principal;
        assert_eq!(
            p.validate_query(&scope(), &bad),
            Err(AgentInvokeError::Unauthenticated)
        );
    }
    let mut s = scope();
    s.namespace.clear();
    assert_eq!(
        p.validate_query(&s, &q),
        Err(AgentInvokeError::InvalidInput)
    );
    for id in [String::new(), "human_x".into(), "x".repeat(257)] {
        let mut bad = q.clone();
        bad.context.target.bot_id = id.clone();
        assert_eq!(
            p.validate_query(&scope(), &bad),
            Err(AgentInvokeError::InvalidInput)
        );
        let mut bad = q.clone();
        bad.context.target.peer_bot_id = Some(id);
        assert_eq!(
            p.validate_query(&scope(), &bad),
            Err(AgentInvokeError::InvalidInput)
        );
    }
    let mut bad = q.clone();
    bad.context.principal = AgentInvokePrincipal::Human(format!("human_{}", "x".repeat(251)));
    assert_eq!(
        p.validate_query(&scope(), &bad),
        Err(AgentInvokeError::InvalidInput)
    );
    let mut bad = q.clone();
    bad.context.target.peer_bot_id = Some("bot-a".into());
    assert_eq!(
        p.validate_query(&scope(), &bad),
        Err(AgentInvokeError::InvalidInput)
    );
    for filter in [
        AgentInvokeQueryFilter::Grant {
            direction: AgentInvokeDirection::Outbound,
            edge_id: NonZeroU64::new(u64::MAX).unwrap(),
        },
        AgentInvokeQueryFilter::Request {
            direction: AgentInvokeDirection::Inbound,
            request_id: "not-a-uuid".into(),
        },
    ] {
        assert_eq!(
            p.validate_query(&scope(), &input(filter)),
            Err(AgentInvokeError::InvalidInput)
        );
    }
    let mut bad = q;
    bad.page = Some(AgentInvokeQueryPage::default());
    assert_eq!(
        p.validate_query(&scope(), &bad),
        Err(AgentInvokeError::InvalidInput)
    );
}
#[test]
fn query_keysets_bind_every_filter_and_position_component() {
    let p = AgentInvokeQueryPolicy;
    let mut q = input(AgentInvokeQueryFilter::Grants {
        direction: AgentInvokeDirection::Outbound,
        status: EdgeStatus::Approved,
    });
    assert_eq!(q.page.as_ref().unwrap().limit.get(), 50);
    let cursor = AgentInvokeQueryCursor {
        context: q.context.clone(),
        position: AgentInvokeCursorPosition::RowId(NonZeroU64::MIN),
    };
    q.page.as_mut().unwrap().cursor = Some(cursor);
    assert_eq!(p.validate_query(&scope(), &q), Ok(()));
    for mode in 0..7 {
        let mut bad = q.clone();
        let c = &mut bad.page.as_mut().unwrap().cursor.as_mut().unwrap().context;
        match mode {
            0 => c.target.bot_id.push('x'),
            1 => c.target.peer_bot_id = Some("peer".into()),
            2 => c.scope.namespace.push('x'),
            3 => c.principal = AgentInvokePrincipal::Human("human_other".into()),
            4 => {
                c.filter = AgentInvokeQueryFilter::Grants {
                    direction: AgentInvokeDirection::Inbound,
                    status: EdgeStatus::Approved,
                }
            }
            5 => {
                c.filter = AgentInvokeQueryFilter::Grants {
                    direction: AgentInvokeDirection::Outbound,
                    status: EdgeStatus::Revoked,
                }
            }
            _ => c.filter = AgentInvokeQueryFilter::Degrees,
        };
        assert_eq!(
            p.validate_query(&scope(), &bad),
            Err(AgentInvokeError::InvalidInput)
        );
    }
    let mut bad = q.clone();
    bad.page = None;
    assert_eq!(
        p.validate_query(&scope(), &bad),
        Err(AgentInvokeError::InvalidInput)
    );
    let mut bad = q.clone();
    bad.page.as_mut().unwrap().limit = NonZeroU16::new(101).unwrap();
    assert_eq!(
        p.validate_query(&scope(), &bad),
        Err(AgentInvokeError::InvalidInput)
    );
    for position in [
        AgentInvokeCursorPosition::RowId(NonZeroU64::new(u64::MAX).unwrap()),
        AgentInvokeCursorPosition::AuditTime {
            created_at_ms: 1,
            id: NonZeroU64::MIN,
        },
    ] {
        let mut bad = q.clone();
        bad.page.as_mut().unwrap().cursor.as_mut().unwrap().position = position;
        assert_eq!(
            p.validate_query(&scope(), &bad),
            Err(AgentInvokeError::InvalidInput)
        );
    }
    let mut audit = input(AgentInvokeQueryFilter::Audit {
        direction: AgentInvokeDirection::Outbound,
        action: None,
    });
    assert_eq!(
        p.validate_query(&scope(), &audit),
        Err(AgentInvokeError::Forbidden)
    );
    audit.context.target.peer_bot_id = Some("bot-a".into());
    let pos = AgentInvokeCursorPosition::AuditTime {
        created_at_ms: 1,
        id: NonZeroU64::MIN,
    };
    audit.page.as_mut().unwrap().cursor = Some(AgentInvokeQueryCursor {
        context: audit.context.clone(),
        position: pos,
    });
    assert_eq!(
        p.validate_query(&scope(), &audit),
        Ok(()),
        "self fact publication audit is readable by a current controller"
    );
    for position in [
        AgentInvokeCursorPosition::AuditTime {
            created_at_ms: u64::MAX,
            id: NonZeroU64::MIN,
        },
        AgentInvokeCursorPosition::AuditTime {
            created_at_ms: 1,
            id: NonZeroU64::new(u64::MAX).unwrap(),
        },
        AgentInvokeCursorPosition::RowId(NonZeroU64::MIN),
    ] {
        audit
            .page
            .as_mut()
            .unwrap()
            .cursor
            .as_mut()
            .unwrap()
            .position = position;
        assert_eq!(
            p.validate_query(&scope(), &audit),
            Err(AgentInvokeError::InvalidInput)
        );
    }
}
#[test]
fn query_roles_are_current_and_do_not_depend_on_agent_qualification() {
    let p = AgentInvokeQueryPolicy;
    let q = input(AgentInvokeQueryFilter::Degrees);
    let good = facts(&q);
    for role in [AgentInvokeRole::Owner, AgentInvokeRole::Manager] {
        let mut f = good.clone();
        f.anchor_role = Some(role);
        assert_eq!(p.authorize_query(&scope(), &q, &f), Ok(()));
    }
    for mode in 0..7 {
        let mut f = good.clone();
        let expected = match mode {
            0 => {
                f.operator_live = false;
                AgentInvokeError::Forbidden
            }
            1 => {
                f.anchor_role = None;
                AgentInvokeError::Forbidden
            }
            2 => {
                f.anchor_live = false;
                AgentInvokeError::NotFound
            }
            3 => {
                f.ownership_version = 0;
                AgentInvokeError::InvalidState
            }
            4 => {
                f.owner_count = 2;
                AgentInvokeError::InvalidState
            }
            5 => {
                f.canonical_owner_count = 0;
                AgentInvokeError::InvalidState
            }
            _ => {
                f.context.target.bot_id.push('x');
                AgentInvokeError::InvalidState
            }
        };
        assert_eq!(p.authorize_query(&scope(), &q, &f), Err(expected));
    }
}
#[test]
fn governance_cannot_turn_a_pair_query_into_a_bot_graph_or_reverse_permission() {
    let p = AgentInvokeQueryPolicy;
    let mut q = input(AgentInvokeQueryFilter::Degrees);
    q.context.principal = AgentInvokePrincipal::Governance("governor".into());
    assert_eq!(
        p.validate_query(&scope(), &q),
        Err(AgentInvokeError::Forbidden)
    );
    q.context.target.peer_bot_id = Some("bot-b".into());
    let mut f = facts(&q);
    f.operator_live = false;
    f.anchor_live = false;
    f.anchor_role = None;
    assert_eq!(
        p.authorize_query(&scope(), &q, &f),
        Err(AgentInvokeError::Forbidden)
    );
    f.delegated_actions = vec![AgentInvokeAction::Query];
    assert_eq!(
        p.authorize_query(&scope(), &q, &f),
        Err(AgentInvokeError::Forbidden)
    );
    f.reverse_delegated_actions = vec![AgentInvokeAction::Query];
    assert_eq!(p.authorize_query(&scope(), &q, &f), Ok(()));
    q.context.filter = AgentInvokeQueryFilter::Grant {
        direction: AgentInvokeDirection::Outbound,
        edge_id: NonZeroU64::MIN,
    };
    f.context = q.context.clone();
    f.reverse_delegated_actions.clear();
    assert_eq!(p.authorize_query(&scope(), &q, &f), Ok(()));
    q.context.principal = AgentInvokePrincipal::Agent("bot-a".into());
    f.context = q.context.clone();
    assert_eq!(
        p.authorize_query(&scope(), &q, &f),
        Err(AgentInvokeError::Forbidden)
    );
}
struct Recording {
    calls: AtomicUsize,
    mode: AtomicU8,
}
#[async_trait::async_trait]
impl AgentInvokeQueryRepoPort for Recording {
    async fn read_query(
        &self,
        _: &AgentInvokeScope,
        q: AgentInvokeQuery,
    ) -> AgentInvokeResult<AgentInvokeQueryResult> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        let mode = self.mode.load(Ordering::SeqCst);
        if mode == 1 {
            return Err(AgentInvokeError::Unavailable);
        }
        let mut context = q.context;
        if mode == 2 {
            context.target.bot_id.push('x');
        }
        let data = if mode == 3 {
            AgentInvokeQueryData::Audit(AgentInvokeQueryRows {
                items: vec![],
                next_cursor: None,
            })
        } else {
            AgentInvokeQueryData::Degrees(AgentInvokeDegrees {
                out_degree: 0,
                in_degree: 0,
                pending_out: 0,
                pending_in: 0,
                count_basis: AgentInvokeCountBasis::StoredApproved,
            })
        };
        Ok(AgentInvokeQueryResult {
            context,
            snapshot_at_ms: 1,
            data,
        })
    }
}
#[tokio::test]
async fn application_calls_one_port_propagates_failures_and_rejects_foreign_results() {
    let repo = Arc::new(Recording {
        calls: AtomicUsize::new(0),
        mode: AtomicU8::new(0),
    });
    let service = AgentInvokeQueryApplicationService::new(
        scope(),
        Arc::new(AgentInvokeQueryPolicy),
        repo.clone(),
    )
    .unwrap();
    let q = input(AgentInvokeQueryFilter::Degrees);
    service.read(q.clone()).await.unwrap();
    assert_eq!(repo.calls.load(Ordering::SeqCst), 1);
    for (mode, error) in [
        (1, AgentInvokeError::Unavailable),
        (2, AgentInvokeError::InvalidState),
        (3, AgentInvokeError::InvalidState),
    ] {
        repo.mode.store(mode, Ordering::SeqCst);
        assert_eq!(service.read(q.clone()).await, Err(error));
        assert_eq!(
            repo.calls.load(Ordering::SeqCst),
            mode as usize + 1,
            "no hidden retries"
        );
    }
    let mut forged = q;
    forged.context.principal = AgentInvokePrincipal::Agent("bot-a".into());
    assert_eq!(service.read(forged).await, Err(AgentInvokeError::Forbidden));
    assert_eq!(repo.calls.load(Ordering::SeqCst), 4);
    let mut bad_scope = scope();
    bad_scope.env.clear();
    assert!(
        AgentInvokeQueryApplicationService::new(bad_scope, Arc::new(AgentInvokeQueryPolicy), repo)
            .is_err()
    );
}
