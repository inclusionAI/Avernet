# evolution_verify — compatibility imports only

Canonical implementation, domain contracts and tests are owned by
[`avernet_evolution.core.evolution_verify`](../../../../../../evolution/src/avernet_evolution/core/evolution_verify/README.md).
These files re-export the same module/classes; do not add new domain logic here.
New consumers must import the canonical package. Compatibility aliases remain
for existing Backend/platform consumers and can be removed once those consumers
migrate; they are not a second runtime or independent service.

See [Evolution ownership and limitations](../../../../../../evolution/README.md).
