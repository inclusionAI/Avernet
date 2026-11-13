use std::{io::Write, sync::{Arc, Mutex}};
use serde_json::Value;

#[path = "../../../../test-support/legacy_log_capture.rs"]
pub(crate) mod legacy;

#[derive(Clone, Default)]
struct Buffer(Arc<Mutex<Vec<u8>>>);
impl Write for Buffer {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> { self.0.lock().unwrap().write(bytes) }
    fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
}

pub(crate) fn capture(action: impl FnOnce()) -> Vec<Value> {
    let buffer = Buffer::default();
    let output = buffer.clone();
    let subscriber = tracing_subscriber::fmt().json().with_ansi(false)
        .with_max_level(tracing::Level::TRACE).with_writer(move || output.clone()).finish();
    tracing::subscriber::with_default(subscriber, action);
    String::from_utf8(buffer.0.lock().unwrap().clone()).unwrap().lines()
        .map(|line| serde_json::from_str(line).unwrap()).collect()
}

pub(crate) fn decision() -> bcs_service_api::RoutingDecision {
    bcs_service_api::RoutingDecision {
        targets: [("send", bcs_service_api::DeliveryType::Send), ("inject", bcs_service_api::DeliveryType::Inject)]
            .into_iter().map(|(bot, delivery_type)| bcs_service_api::RoutingTarget {
                bot_uuid: bot.into(), url: "ws://fixture".into(), is_driver: false, delivery_type,
            }).collect(),
        mentions: vec!["send".into()], cleaned_message: "stripped text".into(), hidden_mentions: Vec::new(),
    }
}
