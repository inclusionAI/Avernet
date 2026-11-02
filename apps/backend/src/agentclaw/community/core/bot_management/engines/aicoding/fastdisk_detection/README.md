# Embedded fastdisk detector

Only read-only detection code is shipped here. The mount gate is copied from the
container scripts repository's `bootstrapping/lib/persist_mount_gate.py`; retain
its live-mount and pending-cutover semantics. `runtime_layout.py` reads the same
runtime path configuration as the container. Backend compresses this detector
into a short command and executes it without installed script dependencies.

There is deliberately no upload/restore implementation or fallback bundle here.
Transfers require the compatible script already installed in the container.
`../fastdisk_probe.py` owns command packaging and result parsing; each caller owns
its failure policy. Publication and restore reject detection errors rather than
falling back to possibly stale NAS data. Coding restart treats an initial
unknown/failed probe as non-fastdisk, retaining its best-effort behavior. Once
restart has confirmed fastdisk, backup failure or an unconfirmed receipt vetoes
replacement. Non-coding engines never run this probe. No backup implementation is
embedded; the installed restart helper's start/status protocol remains unchanged.
