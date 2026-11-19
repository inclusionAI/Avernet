use super::*;
use harness::state::session_spec_admission_contract_tests;

fn spec_script() -> (Script, AdmitRequest) {
    let request = admission();
    let script = Script::default()
        .expect(
            "SessionStorePort::get_spec",
            (&request.input.sid,),
            ok(None::<SessionSpec>),
        )
        .expect(
            "InboxProducerPort::admit",
            (&request,),
            ok(AdmitOutcome::Accepted(receipt())),
        )
        .expect(
            "SessionStorePort::get_spec",
            (&request.input.sid,),
            ok(Some(spec())),
        )
        .expect(
            "InboxProducerPort::admit",
            (&request,),
            ok(AdmitOutcome::Duplicate(receipt())),
        )
        .expect(
            "SessionStorePort::get_spec",
            (&request.input.sid,),
            ok(Some(spec())),
        );
    (script, request)
}

#[tokio::test]
async fn admission_creates_spec_readable_through_session_store_without_checkpoint() -> ContractResult
{
    let (script, request) = spec_script();
    session_spec_admission_contract_tests(&script, &script, request).await?;
    script.done();
    Ok(())
}

#[tokio::test]
async fn spec_readback_rejects_missing_or_replaced_frozen_definition() {
    for index in [2, 4] {
        let (script, request) = spec_script();
        if index == 2 {
            script.replace(index, ok(None::<SessionSpec>));
        } else {
            let mut changed = spec();
            changed.workflow.revision = "newer-workflow".into();
            script.replace(index, ok(Some(changed)));
        }
        rejects(
            session_spec_admission_contract_tests(&script, &script, request),
            "assertion",
        )
        .await;
        script.remaining(4 - index);
    }
}

#[tokio::test]
async fn spec_read_failure_is_propagated_instead_of_becoming_absence() {
    let (script, request) = spec_script();
    let error = StoreError::NotApplied {
        code: "spec_unavailable".into(),
    };
    script.replace(2, Err::<Option<SessionSpec>, _>(error.clone()));
    let actual = session_spec_admission_contract_tests(&script, &script, request)
        .await
        .unwrap_err();
    assert_eq!(actual.downcast_ref::<StoreError>(), Some(&error));
    script.remaining(2);
}
