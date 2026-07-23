use crate::config::TelemetryConfig;
use opentelemetry::{global, trace::TracerProvider as _};
use opentelemetry_otlp::{SpanExporter, WithExportConfig, WithHttpConfig};
use opentelemetry_sdk::{
    Resource,
    propagation::TraceContextPropagator,
    trace::{Sampler, SdkTracer, SdkTracerProvider},
};
use std::collections::HashMap;

const DEFAULT_SERVICE_NAME: &str = "bcn";

#[derive(Debug, Clone, PartialEq, Eq)]
enum EndpointSource {
    Environment,
    Config(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ResolvedTelemetryConfig {
    service_name: String,
    endpoint: Option<EndpointSource>,
    extra_headers: HashMap<String, String>,
    export_enabled: bool,
}

impl ResolvedTelemetryConfig {
    fn from_values(
        file: &TelemetryConfig,
        service_name: Option<&str>,
        traces_endpoint: Option<&str>,
        general_endpoint: Option<&str>,
        sdk_disabled: Option<&str>,
    ) -> Self {
        let service_name = service_name
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .or_else(|| {
                let value = file.service_name.trim();
                (!value.is_empty()).then_some(value)
            })
            .unwrap_or(DEFAULT_SERVICE_NAME)
            .to_string();
        let environment_endpoint = traces_endpoint
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .or_else(|| {
                general_endpoint
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
            })
            .map(|_| EndpointSource::Environment);
        let endpoint = environment_endpoint.or_else(|| {
            file.otlp_traces_endpoint
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(|value| EndpointSource::Config(value.to_string()))
        });
        let sdk_disabled = sdk_disabled.is_some_and(|value| value.eq_ignore_ascii_case("true"));
        let export_enabled = file.enabled && !sdk_disabled && endpoint.is_some();

        Self {
            service_name,
            endpoint,
            extra_headers: file
                .extra_headers
                .iter()
                .map(|(name, value)| (name.clone(), value.clone()))
                .collect(),
            export_enabled,
        }
    }

    fn from_env(file: &TelemetryConfig) -> Self {
        let service_name = std::env::var("OTEL_SERVICE_NAME").ok();
        let traces_endpoint = std::env::var("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT").ok();
        let general_endpoint = std::env::var("OTEL_EXPORTER_OTLP_ENDPOINT").ok();
        let sdk_disabled = std::env::var("OTEL_SDK_DISABLED").ok();
        Self::from_values(
            file,
            service_name.as_deref(),
            traces_endpoint.as_deref(),
            general_endpoint.as_deref(),
            sdk_disabled.as_deref(),
        )
    }
}

fn build_span_exporter(
    config: &ResolvedTelemetryConfig,
) -> Result<SpanExporter, opentelemetry_otlp::ExporterBuildError> {
    let builder = SpanExporter::builder()
        .with_http()
        .with_headers(config.extra_headers.clone());
    match config.endpoint.as_ref() {
        Some(EndpointSource::Config(endpoint)) => {
            builder.with_endpoint(endpoint.clone()).build()
        }
        Some(EndpointSource::Environment) | None => builder.build(),
    }
}

pub struct Telemetry {
    provider: SdkTracerProvider,
    tracer: SdkTracer,
}

impl Telemetry {
    pub fn init(file_config: &TelemetryConfig) -> Self {
        global::set_text_map_propagator(TraceContextPropagator::new());
        let config = ResolvedTelemetryConfig::from_env(file_config);
        let resource = Resource::builder_empty()
            .with_service_name(config.service_name.clone())
            .build();
        let builder = SdkTracerProvider::builder()
            .with_sampler(Sampler::ParentBased(Box::new(Sampler::AlwaysOn)))
            .with_resource(resource);

        let provider = if config.export_enabled {
            match build_span_exporter(&config) {
                Ok(exporter) => builder.with_batch_exporter(exporter).build(),
                Err(error) => {
                    eprintln!(
                        "[telemetry] WARNING: failed to initialize OTLP trace exporter: {error}. Trace export disabled."
                    );
                    builder.build()
                }
            }
        } else {
            builder.build()
        };
        let tracer = provider.tracer("bcn");
        global::set_tracer_provider(provider.clone());

        Self { provider, tracer }
    }

    pub fn tracer(&self) -> SdkTracer {
        self.tracer.clone()
    }
}

impl Drop for Telemetry {
    fn drop(&mut self) {
        if let Err(error) = self.provider.shutdown() {
            eprintln!("[telemetry] WARNING: failed to shut down trace provider: {error}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use opentelemetry::trace::{Span as _, Tracer as _};
    use std::collections::BTreeMap;
    use std::io::{Read, Write};
    use std::sync::mpsc;
    use std::time::Duration;

    #[test]
    fn environment_values_override_file_telemetry_config() {
        let file = crate::config::TelemetryConfig {
            service_name: "bcn-file".to_string(),
            otlp_traces_endpoint: Some("http://file-collector/v1/traces".to_string()),
            ..crate::config::TelemetryConfig::default()
        };
        let config = ResolvedTelemetryConfig::from_values(
            &file,
            Some("bcn-test"),
            Some("http://collector/traces"),
            Some("http://collector"),
            None,
        );

        assert_eq!(config.service_name, "bcn-test");
        assert_eq!(config.endpoint, Some(EndpointSource::Environment));
        assert!(config.export_enabled);
    }

    #[test]
    fn file_telemetry_values_are_used_without_environment_overrides() {
        let file = crate::config::TelemetryConfig {
            service_name: "bcn-file".to_string(),
            otlp_traces_endpoint: Some("http://file-collector/v1/traces".to_string()),
            extra_headers: BTreeMap::from([(
                "x-collector-route".to_string(),
                "collector-local".to_string(),
            )]),
            ..crate::config::TelemetryConfig::default()
        };
        let config = ResolvedTelemetryConfig::from_values(&file, None, None, None, None);

        assert_eq!(config.service_name, "bcn-file");
        assert_eq!(
            config.endpoint,
            Some(EndpointSource::Config(
                "http://file-collector/v1/traces".to_string()
            ))
        );
        assert_eq!(
            config.extra_headers.get("x-collector-route"),
            Some(&"collector-local".to_string())
        );
        assert!(config.export_enabled);
    }

    #[test]
    fn blank_traces_endpoint_falls_back_to_general_environment_endpoint() {
        let file = crate::config::TelemetryConfig::default();
        let config = ResolvedTelemetryConfig::from_values(
            &file,
            None,
            Some("  "),
            Some("http://general-collector:4318"),
            None,
        );

        assert_eq!(config.endpoint, Some(EndpointSource::Environment));
        assert!(config.export_enabled);
    }

    #[test]
    fn file_or_sdk_disabled_prevents_export() {
        let file_disabled = crate::config::TelemetryConfig {
            enabled: false,
            otlp_traces_endpoint: Some("http://file-collector/v1/traces".to_string()),
            ..crate::config::TelemetryConfig::default()
        };
        let config = ResolvedTelemetryConfig::from_values(
            &file_disabled,
            None,
            Some("http://env-collector/v1/traces"),
            None,
            None,
        );
        assert!(!config.export_enabled);

        let file_enabled = crate::config::TelemetryConfig {
            otlp_traces_endpoint: Some("http://file-collector/v1/traces".to_string()),
            ..crate::config::TelemetryConfig::default()
        };
        let config = ResolvedTelemetryConfig::from_values(
            &file_enabled,
            None,
            None,
            None,
            Some("TRUE"),
        );
        assert!(!config.export_enabled);
    }

    #[test]
    fn configured_extra_headers_are_sent_to_otlp_endpoint() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let (request_tx, request_rx) = mpsc::channel();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut request = Vec::new();
            let mut buffer = [0_u8; 2048];
            while !request.windows(4).any(|window| window == b"\r\n\r\n") {
                let size = stream.read(&mut buffer).unwrap();
                if size == 0 {
                    break;
                }
                request.extend_from_slice(&buffer[..size]);
            }
            request_tx
                .send(String::from_utf8_lossy(&request).into_owned())
                .unwrap();
            stream
                .write_all(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\nconnection: close\r\n\r\n")
                .unwrap();
        });

        let file = crate::config::TelemetryConfig {
            otlp_traces_endpoint: Some(format!("http://{address}/v1/traces")),
            extra_headers: BTreeMap::from([(
                "x-bcn-config-header-test".to_string(),
                "configured".to_string(),
            )]),
            ..crate::config::TelemetryConfig::default()
        };
        let config = ResolvedTelemetryConfig::from_values(&file, None, None, None, None);
        let exporter = build_span_exporter(&config).unwrap();
        let provider = SdkTracerProvider::builder()
            .with_simple_exporter(exporter)
            .build();
        let tracer = provider.tracer("bcn-header-test");
        let mut span = tracer.start("header-test");
        span.end();
        provider.force_flush().unwrap();

        let request = request_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        assert!(
            request
                .to_ascii_lowercase()
                .contains("x-bcn-config-header-test: configured\r\n")
        );
        provider.shutdown().unwrap();
        server.join().unwrap();
    }
}
