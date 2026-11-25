//! A2A delivery semantics, independent of SDK and network binding.
use bcs_protocol::BcsFrame;
use bcs_service_api::{BotAbortDeliveryCommand, BotDeliveryCommand, BotDeliveryPort, ServiceError};

/// Fixture supplies a trusted registered local Run and a remote Task that stays
/// active until canceled. Equal sends must not create another execution.
pub async fn bot_delivery_port_contract_tests(
    port: &dyn BotDeliveryPort, command: BotDeliveryCommand, abort: BotAbortDeliveryCommand,
) {
    assert!(port.is_available(&command.target).await);
    let mut unsupported = command.clone();
    let BcsFrame::Request(frame) = &mut unsupported.frame else { panic!("request fixture required"); };
    frame.method = "chat.inject".into();
    let rejected = port.deliver(unsupported).await.unwrap();
    assert!(!rejected.delivered);
    assert!(matches!(rejected.error,Some(ServiceError::BotMethodUnsupported { .. })));
    let mut foreign_run = command.clone(); foreign_run.run_id.push_str("-unregistered");
    assert!(port.deliver(foreign_run).await.is_err());
    let sent = port.deliver(command.clone()).await.unwrap();
    assert!(sent.delivered); assert!(sent.error.is_none());
    assert!(port.deliver(command.clone()).await.unwrap().delivered);
    let mut foreign_scope = abort.clone(); foreign_scope.session_id.push_str("-foreign");
    assert!(port.abort(foreign_scope).await.is_err());
    let canceled = port.abort(abort).await.unwrap();
    assert_eq!(canceled.target_bot_id,command.target.bot_id());
    assert_eq!(canceled.aborted_run_ids,vec![command.run_id]);
}
