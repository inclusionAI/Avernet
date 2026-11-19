# Distributed encoding

Pure shared protocol encoding for public distributed services. It performs no
storage, policy, clock reads or random ID generation and depends only on model
declarations. Worker and store implementations use the same code without importing
one another. Composition does not select a backend for these fixed wire rules.

Field digests use SHA-256 with an unsigned 64-bit big-endian length before each
field and the `sha256:` plus lowercase-hex output. JSON digests sort object keys
recursively and retain array order. Blob digests hash the fields `blob:v1` and
exact stored bytes; readers independently validate parsed JSON size and shape.
Critical Agent input IDs hash `critical-agent:v1`, did and remote event ID and
prefix the digest hex with `ac-`. Holder, sequence and receive time are excluded;
changed event kind/content remains a canonical admission conflict.

These are existing distributed store encodings extracted for runtime reuse;
changing them requires an explicit version and compatibility contract. The old
store identity module remains a re-export for source compatibility.
