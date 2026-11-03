use super::super::{
    Arc,
    Future,
    OnceLock,
    Write,
    io
};

#[derive(Clone, Default)]
pub(in super::super) struct SharedLogBuffer(pub(in super::super) Arc<std::sync::Mutex<Vec<u8>>>);

pub(in super::super) struct SharedLogWriter {
    buffer: Arc<std::sync::Mutex<Vec<u8>>>,
}

impl Write for SharedLogWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.buffer.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for SharedLogBuffer {
    type Writer = SharedLogWriter;

    fn make_writer(&'a self) -> Self::Writer {
        SharedLogWriter {
            buffer: self.0.clone(),
        }
    }
}

pub(in super::super) async fn capture_tracing_logs<Fut, T>(future: Fut) -> (T, String)
where
    Fut: Future<Output = T>,
{
    static BUFFER: OnceLock<SharedLogBuffer> = OnceLock::new();
    let buffer = BUFFER
        .get_or_init(|| {
            let buffer = SharedLogBuffer::default();
            let subscriber = tracing_subscriber::fmt()
                .with_ansi(false)
                .with_level(false)
                .with_target(true)
                .with_writer(buffer.clone())
                .finish();
            tracing::subscriber::set_global_default(subscriber)
                .expect("install tracing subscriber");
            buffer
        })
        .clone();
    buffer.0.lock().unwrap().clear();
    let output = future.await;
    let logs = String::from_utf8(buffer.0.lock().unwrap().clone()).unwrap();
    (output, logs)
}
