//! Same query semantics for each real/synthetic consumer driver.
#![allow(clippy::unwrap_used, reason = "shared contract assertions")]
use super::agent_invoke::AgentInvokeContractDriver;
use bcs_domain::edge_permission::{EdgeStatus, RequestStatus};
use bcs_service_api::{
    application::agent_invoke::AgentInvokeGovernanceQueryService,
    core::agent_invoke::AgentInvokeQueryPolicyCoreService,
    port::repo::agent_invoke::AgentInvokeQueryRepoPort,
    types::{agent_invoke::*, agent_invoke_query::*},
};
use std::num::NonZeroU16;
#[async_trait::async_trait]
pub trait AgentInvokeQueryContractDriver: AgentInvokeContractDriver {
    fn queries(&self) -> &dyn AgentInvokeGovernanceQueryService;
    fn query_repo(&self) -> &dyn AgentInvokeQueryRepoPort;
    async fn query_delegation(&self, pair: AgentInvokePair);
    async fn legacy_query_fixture(&self);
}
pub fn query(scope: AgentInvokeScope, filter: AgentInvokeQueryFilter) -> AgentInvokeQuery {
    let page = filter.is_page().then(|| AgentInvokeQueryPage {
        limit: NonZeroU16::MIN,
        cursor: None,
    });
    AgentInvokeQuery {
        context: AgentInvokeQueryContext {
            scope,
            principal: AgentInvokePrincipal::Human("human_query_manager".into()),
            target: AgentInvokeQueryTarget {
                bot_id: "query-a".into(),
                peer_bot_id: None,
            },
            filter,
        },
        page,
    }
}
fn command<D: AgentInvokeQueryContractDriver>(
    d: &D,
    principal: &str,
    from: &str,
    to: &str,
    key: &str,
    change: AgentInvokeChange,
) -> AgentInvokeCommand {
    AgentInvokeCommand {
        principal: AgentInvokePrincipal::Human(principal.into()),
        authenticated_scope: d.scope(),
        pair: AgentInvokePair {
            from_bot_id: from.into(),
            to_bot_id: to.into(),
        },
        change,
        idempotency_key: key.into(),
        reason: "query conformance".into(),
    }
}
pub async fn setup<D: AgentInvokeQueryContractDriver>(d: &D) {
    for bot in ["query-a", "query-b", "query-c", "query-d"] {
        d.register(bot).await;
    }
    d.role(
        "human_query_source",
        "query-a",
        Some(AgentInvokeRole::Owner),
    )
    .await;
    d.role(
        "human_query_manager",
        "query-a",
        Some(AgentInvokeRole::Manager),
    )
    .await;
    for bot in ["query-b", "query-c", "query-d"] {
        d.role("human_query_target", bot, Some(AgentInvokeRole::Owner))
            .await;
    }
}
pub async fn agent_invoke_governance_query_service_contract_tests<
    D: AgentInvokeQueryContractDriver,
>(
    d: &D,
) {
    setup(d).await;
    let first = d
        .management()
        .change(command(
            d,
            "human_query_target",
            "query-a",
            "query-b",
            "q-grant-b",
            AgentInvokeChange::Grant {
                expected_version: 0,
            },
        ))
        .await
        .unwrap()
        .current_grant
        .unwrap();
    d.management()
        .change(command(
            d,
            "human_query_target",
            "query-a",
            "query-c",
            "q-grant-c",
            AgentInvokeChange::Grant {
                expected_version: 0,
            },
        ))
        .await
        .unwrap();
    d.management()
        .change(command(
            d,
            "human_query_source",
            "query-b",
            "query-a",
            "q-grant-reverse",
            AgentInvokeChange::Grant {
                expected_version: 0,
            },
        ))
        .await
        .unwrap();
    let pending = d
        .management()
        .change(command(
            d,
            "human_query_source",
            "query-a",
            "query-d",
            "q-request-out",
            AgentInvokeChange::Request,
        ))
        .await
        .unwrap()
        .current_request
        .unwrap();
    d.management()
        .change(command(
            d,
            "human_query_target",
            "query-b",
            "query-a",
            "q-request-in",
            AgentInvokeChange::Request,
        ))
        .await
        .unwrap();
    d.legacy_query_fixture().await;
    let degree = query(d.scope(), AgentInvokeQueryFilter::Degrees);
    assert_eq!(
        d.queries().read(degree.clone()).await.unwrap().data,
        AgentInvokeQueryData::Degrees(AgentInvokeDegrees {
            out_degree: 2,
            in_degree: 1,
            pending_out: 1,
            pending_in: 1,
            count_basis: AgentInvokeCountBasis::StoredApproved
        })
    );
    let mut page = query(
        d.scope(),
        AgentInvokeQueryFilter::Grants {
            direction: AgentInvokeDirection::Outbound,
            status: EdgeStatus::Approved,
        },
    );
    let one = d.queries().read(page.clone()).await.unwrap();
    let AgentInvokeQueryData::Grants(one) = one.data else {
        panic!("grant page")
    };
    assert_eq!(one.items.len(), 1);
    assert_eq!(one.items[0].grant.edge_id, first.edge_id);
    assert_eq!(one.items[0].from.bot_id, "query-a");
    assert!(one.items[0].from.name.is_some());
    page.page.as_mut().unwrap().cursor = one.next_cursor;
    assert!(page.page.as_ref().unwrap().cursor.is_some());
    let AgentInvokeQueryData::Grants(two) = d.queries().read(page.clone()).await.unwrap().data
    else {
        panic!("grant page")
    };
    assert_eq!(two.items.len(), 1);
    assert_eq!(two.items[0].to.bot_id, "query-c");
    assert!(two.next_cursor.is_none());
    let mut wrong = page.clone();
    wrong.context.target.bot_id = "query-b".into();
    assert_eq!(
        d.queries().read(wrong).await,
        Err(AgentInvokeError::InvalidInput)
    );
    let mut wrong = page.clone();
    wrong.context.principal = AgentInvokePrincipal::Human("human_query_source".into());
    assert_eq!(
        d.queries().read(wrong).await,
        Err(AgentInvokeError::InvalidInput)
    );
    let exact = query(
        d.scope(),
        AgentInvokeQueryFilter::Grant {
            direction: AgentInvokeDirection::Outbound,
            edge_id: first.edge_id,
        },
    );
    let AgentInvokeQueryData::Grant(record) = d.queries().read(exact.clone()).await.unwrap().data
    else {
        panic!("exact grant")
    };
    assert_eq!(record.grant, first);
    let mut reverse = exact;
    reverse.context.filter = AgentInvokeQueryFilter::Grant {
        direction: AgentInvokeDirection::Inbound,
        edge_id: first.edge_id,
    };
    assert_eq!(
        d.queries().read(reverse).await,
        Err(AgentInvokeError::NotFound)
    );
    let exact = query(
        d.scope(),
        AgentInvokeQueryFilter::Request {
            direction: AgentInvokeDirection::Outbound,
            request_id: pending.request_id.clone(),
        },
    );
    let AgentInvokeQueryData::Request(record) = d.queries().read(exact).await.unwrap().data else {
        panic!("request")
    };
    assert_eq!(record.request, pending);
    let list = query(
        d.scope(),
        AgentInvokeQueryFilter::Requests {
            direction: AgentInvokeDirection::Inbound,
            status: RequestStatus::Pending,
        },
    );
    let AgentInvokeQueryData::Requests(records) = d.queries().read(list).await.unwrap().data else {
        panic!("request page")
    };
    assert_eq!(records.items.len(), 1);
    let revoked = d
        .management()
        .change(command(
            d,
            "human_query_source",
            "query-a",
            "query-b",
            "q-revoke",
            AgentInvokeChange::Revoke {
                edge_id: first.edge_id,
                expected_version: first.version,
            },
        ))
        .await
        .unwrap()
        .current_grant
        .unwrap();
    d.management()
        .change(command(
            d,
            "human_query_target",
            "query-a",
            "query-b",
            "q-regrant",
            AgentInvokeChange::Grant {
                expected_version: revoked.version.get(),
            },
        ))
        .await
        .unwrap();
    let mut audit = query(
        d.scope(),
        AgentInvokeQueryFilter::Audit {
            direction: AgentInvokeDirection::Outbound,
            action: Some(AgentInvokeAuditAction::Grant),
        },
    );
    audit.context.target.peer_bot_id = Some("query-b".into());
    let AgentInvokeQueryData::Audit(first_audit) =
        d.queries().read(audit.clone()).await.unwrap().data
    else {
        panic!("audit")
    };
    assert_eq!(first_audit.items.len(), 1);
    assert_eq!(first_audit.items[0].action, AgentInvokeAuditAction::Grant);
    audit.page.as_mut().unwrap().cursor = first_audit.next_cursor;
    assert!(audit.page.as_ref().unwrap().cursor.is_some());
    let AgentInvokeQueryData::Audit(second) = d.queries().read(audit).await.unwrap().data else {
        panic!("audit")
    };
    assert_eq!(second.items.len(), 1);
    assert!(second.next_cursor.is_none());
    let mut all_audit = query(
        d.scope(),
        AgentInvokeQueryFilter::Audit {
            direction: AgentInvokeDirection::Outbound,
            action: None,
        },
    );
    all_audit.context.target.peer_bot_id = Some("query-b".into());
    let AgentInvokeQueryData::Audit(all) = d.queries().read(all_audit).await.unwrap().data else {
        panic!("all actions audit")
    };
    assert_eq!(all.items.len(), 1);
    assert!(all.next_cursor.is_some());
    let mut governor = query(
        d.scope(),
        AgentInvokeQueryFilter::Grants {
            direction: AgentInvokeDirection::Outbound,
            status: EdgeStatus::Approved,
        },
    );
    governor.context.principal = AgentInvokePrincipal::Governance("query-governor".into());
    assert_eq!(
        d.queries().read(governor.clone()).await,
        Err(AgentInvokeError::Forbidden)
    );
    governor.context.target.peer_bot_id = Some("query-b".into());
    assert_eq!(
        d.queries().read(governor.clone()).await,
        Err(AgentInvokeError::Forbidden)
    );
    d.query_delegation(AgentInvokePair {
        from_bot_id: "query-a".into(),
        to_bot_id: "query-b".into(),
    })
    .await;
    let AgentInvokeQueryData::Grants(scoped) =
        d.queries().read(governor.clone()).await.unwrap().data
    else {
        panic!("governance pair")
    };
    assert_eq!(scoped.items.len(), 1);
    assert!(scoped.next_cursor.is_none());
    governor.context.filter = AgentInvokeQueryFilter::Degrees;
    governor.page = None;
    assert_eq!(
        d.queries().read(governor.clone()).await,
        Err(AgentInvokeError::Forbidden)
    );
    d.query_delegation(AgentInvokePair {
        from_bot_id: "query-b".into(),
        to_bot_id: "query-a".into(),
    })
    .await;
    assert_eq!(
        d.queries().read(governor).await.unwrap().data,
        AgentInvokeQueryData::Degrees(AgentInvokeDegrees {
            out_degree: 1,
            in_degree: 1,
            pending_out: 0,
            pending_in: 1,
            count_basis: AgentInvokeCountBasis::StoredApproved
        })
    );
    d.role("human_query_manager", "query-a", None).await;
    assert_eq!(
        d.queries().read(page).await,
        Err(AgentInvokeError::Forbidden)
    );
    let mut ordinary = degree;
    ordinary.context.principal = AgentInvokePrincipal::Agent("query-a".into());
    assert_eq!(
        d.queries().read(ordinary).await,
        Err(AgentInvokeError::Forbidden)
    );
}
pub async fn agent_invoke_query_repo_port_contract_tests<D: AgentInvokeQueryContractDriver>(d: &D) {
    setup(d).await;
    let q = query(d.scope(), AgentInvokeQueryFilter::Degrees);
    let result = d
        .query_repo()
        .read_query(&d.scope(), q.clone())
        .await
        .unwrap();
    assert_eq!(result.context, q.context);
    assert!(result.snapshot_at_ms > 0);
    assert_eq!(
        result.data,
        AgentInvokeQueryData::Degrees(AgentInvokeDegrees {
            out_degree: 0,
            in_degree: 0,
            pending_out: 0,
            pending_in: 0,
            count_basis: AgentInvokeCountBasis::StoredApproved
        })
    );
    let mut forged = q;
    forged.context.principal = AgentInvokePrincipal::Agent("query-a".into());
    assert_eq!(
        d.query_repo().read_query(&d.scope(), forged).await,
        Err(AgentInvokeError::Forbidden)
    );
}
pub fn agent_invoke_query_policy_core_service_contract_tests(
    policy: &dyn AgentInvokeQueryPolicyCoreService,
) {
    let scope = AgentInvokeScope {
        namespace: "query-policy".into(),
        env: "dev".into(),
    };
    let q = query(scope.clone(), AgentInvokeQueryFilter::Degrees);
    let mut f = AgentInvokeQueryFacts {
        context: q.context.clone(),
        operator_live: true,
        anchor_live: true,
        ownership_version: 1,
        owner_count: 1,
        canonical_owner_count: 1,
        anchor_role: Some(AgentInvokeRole::Manager),
        delegated_actions: vec![],
        reverse_delegated_actions: vec![],
    };
    assert_eq!(policy.authorize_query(&scope, &q, &f), Ok(()));
    f.owner_count = 0;
    assert_eq!(
        policy.authorize_query(&scope, &q, &f),
        Err(AgentInvokeError::InvalidState)
    );
    f.owner_count = 1;
    f.operator_live = false;
    assert_eq!(
        policy.authorize_query(&scope, &q, &f),
        Err(AgentInvokeError::Forbidden)
    );
    let mut q = q;
    q.context.scope.namespace.push_str("-wrong");
    assert_eq!(
        policy.validate_query(&scope, &q),
        Err(AgentInvokeError::Forbidden)
    );
}
