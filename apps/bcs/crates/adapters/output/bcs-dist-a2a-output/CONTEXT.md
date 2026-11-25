# Context boundary

- Role: pure output adapter implementing `DistOutputAdapterPort`.
- Owns: A2A artifact parts, stable artifact identity and append formatting.
- Consumes: neutral distributed model, output port and public A2A DTO library.
- Does not own: persistence, payload reads, projection, admission, task lifecycle,
  protocol transport envelopes or runtime assembly.
- Callers: `WorkerOutputReader` through the injected output port.
- [Encoding, source limitations and verification](README.md).
