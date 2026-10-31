# Java SDK HTTP fixtures

java-task-not-found.json was captured from a listening Java SDK 1.4.0.Final
Harness endpoint using a synthetic missing Task ID. It contains no credential.

java-structured-exec-input.json comes from the native Harness ASK engine plus
Java SDK HTTP test HarnessA2aInteractionTest. Only message/Task/context/request
IDs and expiry are normalized. The Java golden test checks the same payload.
The Rust wire test checks SDK parsing/projection, full schema retention and
UTF-8 text. It does not claim production BCS Structured Input support.

Struct numeric values such as version/minProperties are 1.0 on this wire.
