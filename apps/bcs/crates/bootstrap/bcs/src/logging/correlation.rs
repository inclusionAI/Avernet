//! Enrich explicitly selected events before asynchronous file writers see bytes.
use std::fmt;
use bcs_tracing_api::{LogCorrelation, current_log_correlation};
use serde::{Serialize, Serializer, ser::SerializeMap};
use tracing::{Event, Subscriber, field::{Field, Visit}};
use tracing_subscriber::{fmt::{FmtContext, FormatEvent, FormatFields, format::{self, Writer}, time::FormatTime}, registry::LookupSpan};

struct Selection(bool);
impl Visit for Selection {
    fn record_str(&mut self, field: &Field, value: &str) {
        if field.name() == "log_class" { self.0 = value == "business_key"; }
    }
    fn record_debug(&mut self, _field: &Field, _value: &dyn fmt::Debug) {}
}

fn correlation(event: &Event<'_>) -> Option<LogCorrelation> {
    if event.metadata().target() == "bcn_otel" { return None; }
    let mut selected = Selection(false);
    event.record(&mut selected);
    if !selected.0 { return None; }
    let correlation = current_log_correlation()?;
    // Treat collisions atomically. Never replace an existing event field or
    // serialize a duplicate JSON key, even when the event recorded it as None.
    if correlation.fields().iter().any(|(name, _)| event.fields().any(|f| f.name().trim_start_matches("r#") == *name)) {
        return None;
    }
    Some(correlation)
}

pub(super) struct Text<T>(format::Format<format::Full, T>);
pub(super) fn text<T: FormatTime>(timer: T) -> Text<T> { Text(format::format().with_timer(timer)) }

impl<S, N, T> FormatEvent<S, N> for Text<T>
where S: Subscriber + for<'a> LookupSpan<'a>, N: for<'a> FormatFields<'a> + 'static, T: FormatTime + Clone {
    fn format_event(&self, ctx: &FmtContext<'_, S, N>, mut writer: Writer<'_>, event: &Event<'_>) -> fmt::Result {
        let Some(ids) = correlation(event) else { return self.0.format_event(ctx, writer, event); };
        let mut line = String::new();
        self.0.clone().with_ansi(writer.has_ansi_escapes()).format_event(ctx, Writer::new(&mut line), event)?;
        writer.write_str(line.strip_suffix('\n').unwrap_or(&line))?;
        for (name, value) in ids.fields() { write!(writer, " {name}={value}")?; }
        writeln!(writer)
    }
}

pub(super) struct Json<T> {
    fallback: format::Format<format::Json, T>,
    timer: T,
}
pub(super) fn json<T: FormatTime + Clone>(timer: T) -> Json<T> {
    Json { fallback: format::format().json().flatten_event(true).with_current_span(false).with_span_list(false).with_timer(timer.clone()), timer }
}

struct CorrelatedEvent<'a, 'event> {
    event: &'a Event<'event>,
    timestamp: &'a str,
    correlation: &'a LogCorrelation,
}
impl Serialize for CorrelatedEvent<'_, '_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut map = serializer.serialize_map(None)?;
        map.serialize_entry("timestamp", self.timestamp)?;
        map.serialize_entry("level", self.event.metadata().level().as_str())?;
        // Use the same field visitor as tracing-subscriber's flattened JSON
        // formatter: numeric, error, debug and byte fields retain their types.
        let mut visitor = tracing_serde::SerdeMapVisitor::new(map);
        self.event.record(&mut visitor);
        let mut map = visitor.take_serializer()?;
        map.serialize_entry("target", self.event.metadata().target())?;
        for (name, value) in self.correlation.fields() { map.serialize_entry(name, value)?; }
        map.end()
    }
}

impl<S, N, T> FormatEvent<S, N> for Json<T>
where S: Subscriber + for<'a> LookupSpan<'a>, N: for<'a> FormatFields<'a> + 'static, T: FormatTime {
    fn format_event(&self, ctx: &FmtContext<'_, S, N>, mut writer: Writer<'_>, event: &Event<'_>) -> fmt::Result {
        let Some(ids) = correlation(event) else { return self.fallback.format_event(ctx, writer, event); };
        let mut timestamp = String::new();
        self.timer.format_time(&mut Writer::new(&mut timestamp))?;
        let row = CorrelatedEvent { event, timestamp: &timestamp, correlation: &ids };
        let row = serde_json::to_string(&row).map_err(|_| fmt::Error)?;
        writeln!(writer, "{row}")
    }
}

#[cfg(test)]
mod tests;
