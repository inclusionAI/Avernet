// Shared by logging regression tests. Run in a child because tracing permanently
// disables its legacy fallback after any subscriber has been installed. This
// keeps the tests independent of libtest ordering as well as nextest isolation.
use std::sync::Mutex;
use tracing::log::{Level, LevelFilter, Log, Metadata, Record};

#[derive(Debug, Clone)]
pub struct Row {
    pub level: Level,
    pub target: String,
    pub message: String,
}

impl Row {
    pub fn assert_field(&self, name: &str, value: &str) {
        assert!(self.message.contains(&format!("{name}={value}"))
            || self.message.contains(&format!("{name}={value:?}")), "missing {name}={value}: {self:?}");
    }
}

struct Capture(Mutex<Vec<Row>>);
static CAPTURE: Capture = Capture(Mutex::new(Vec::new()));

impl Log for Capture {
    fn enabled(&self, _: &Metadata<'_>) -> bool { true }
    fn log(&self, record: &Record<'_>) {
        self.0.lock().unwrap().push(Row {
            level: record.level(), target: record.target().into(),
            message: record.args().to_string(),
        });
    }
    fn flush(&self) {}
}

pub fn rows(target: &str) -> Vec<Row> {
    CAPTURE.0.lock().unwrap().iter().filter(|row| row.target == target).cloned().collect()
}

pub fn isolated(action: impl FnOnce()) {
    let thread = std::thread::current();
    let name = thread.name().expect("libtest test name");
    const CHILD: &str = "BCS_LEGACY_LOG_TEST_CHILD";
    if std::env::var(CHILD).as_deref() != Ok(name) {
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", name, "--nocapture"])
            .env(CHILD, name).output().unwrap();
        assert!(output.status.success(), "isolated test failed:\n{}\n{}",
            String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        return;
    }
    tracing::log::set_logger(&CAPTURE).unwrap();
    tracing::log::set_max_level(LevelFilter::Trace);
    action();
}
