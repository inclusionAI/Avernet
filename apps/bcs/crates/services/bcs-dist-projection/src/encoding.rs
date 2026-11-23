use serde::Serialize;
use std::io::{self, Write};

/// Count exact public JSON encoding without allocating another copy of the full
/// value. Stop serialization as soon as the selected byte budget is exceeded.
pub(super) fn encoded_bytes<T: Serialize + ?Sized>(value: &T, limit: u64) -> Option<u64> {
    let mut writer = Counter { bytes: 0, limit };
    serde_json::to_writer(&mut writer, value).ok()?;
    Some(writer.bytes)
}

struct Counter {
    bytes: u64,
    limit: u64,
}
impl Write for Counter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.bytes = self
            .bytes
            .checked_add(bytes.len() as u64)
            .filter(|bytes| *bytes <= self.limit)
            .ok_or_else(|| io::Error::other("encoded byte limit"))?;
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
