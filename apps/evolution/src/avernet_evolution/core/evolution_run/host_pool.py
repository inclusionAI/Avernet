"""Serial, fair dispatch across immutable Worker images.

Each child ContainerHost checks only the strategy refs baked into its image.
Workers still claim over the existing Job protocol; no jobs are claimed inside
this selector. Old and new versions can coexist without redefining old images.
"""

from dataclasses import dataclass, field

from .container_host import ContainerHost


@dataclass
class HostPool:
    hosts: tuple[ContainerHost, ...]
    _next: int = field(default=0, init=False)

    async def run_once(self, worker_id: str) -> bool:
        for offset in range(len(self.hosts)):
            index = (self._next + offset) % len(self.hosts)
            if await self.hosts[index].run_once(worker_id):
                self._next = (index + 1) % len(self.hosts)
                return True
        return False
