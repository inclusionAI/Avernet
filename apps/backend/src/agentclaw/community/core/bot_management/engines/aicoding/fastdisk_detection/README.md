# Embedded fastdisk detector

Only read-only detection code is shipped here. The mount gate is copied from the
container scripts repository's `bootstrapping/lib/persist_mount_gate.py`; retain
its live-mount and pending-cutover semantics. `runtime_layout.py` reads the same
runtime path configuration as the container. Backend compresses this detector
into a short command and executes it without installed script dependencies.

There is deliberately no upload/restore implementation or fallback bundle here.
Transfers require the compatible script already installed in the container.
Detection errors block the operation instead of falling back to possibly stale
NAS data. Non-fastdisk results retain the legacy Backend path.
