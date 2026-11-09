"""Own one loopback Qdrant process for one application instance."""

import asyncio
import socket
from collections.abc import Mapping
from pathlib import Path
from urllib.request import ProxyHandler, build_opener


class QdrantProcessProvider:
    def __init__(
        self,
        *,
        binary: str,
        storage_path: str,
        log_path: str,
        environment: Mapping[str, str],
        http_port: int = 6333,
        grpc_port: int = 6334,
        startup_timeout: float = 30,
    ):
        if not 1 <= http_port <= 65535 or not 1 <= grpc_port <= 65535:
            raise ValueError("invalid Qdrant port")
        if http_port == grpc_port or startup_timeout <= 0:
            raise ValueError("distinct ports and positive startup timeout required")
        self.binary = binary
        self.environment = dict(environment)
        self.storage_path = Path(storage_path).resolve()
        self.log_path = Path(log_path)
        self.http_port = http_port
        self.grpc_port = grpc_port
        self.startup_timeout = startup_timeout
        self._process = None

    def _probe(self):
        # Loopback health never uses company or workstation HTTP proxies.
        opener = build_opener(ProxyHandler({}))
        with opener.open(
            f"http://127.0.0.1:{self.http_port}/readyz", timeout=1
        ) as response:
            return response.status == 200

    async def initialize(self):
        if self._process is not None:
            if self._process.returncode is None:
                return
            raise RuntimeError("owned Qdrant process has exited")
        # Refuse to attach accidentally to an unrelated service occupying the ports.
        for port in (self.http_port, self.grpc_port):
            with socket.socket() as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(self.environment)
        env.update(
            {
                "QDRANT__SERVICE__HOST": "127.0.0.1",
                "QDRANT__SERVICE__HTTP_PORT": str(self.http_port),
                "QDRANT__SERVICE__GRPC_PORT": str(self.grpc_port),
                "QDRANT__STORAGE__STORAGE_PATH": str(self.storage_path / "storage"),
                "QDRANT__STORAGE__SNAPSHOTS_PATH": str(self.storage_path / "snapshots"),
                "QDRANT__LOG_LEVEL": "WARN",
                "QDRANT__CLUSTER__ENABLED": "false",
            }
        )
        try:
            with self.log_path.open("ab") as log:
                self._process = await asyncio.create_subprocess_exec(
                    self.binary,
                    "--disable-telemetry",
                    env=env,
                    cwd=self.storage_path,
                    stdout=log,
                    stderr=log,
                )
            deadline = asyncio.get_running_loop().time() + self.startup_timeout
            while asyncio.get_running_loop().time() < deadline:
                if self._process.returncode is not None:
                    raise RuntimeError(
                        "Qdrant exited before readiness; inspect its log"
                    )
                try:
                    ready = await asyncio.to_thread(self._probe)
                except OSError:
                    ready = False
                if ready and self._process.returncode is None:
                    return
                await asyncio.sleep(0.1)
            raise TimeoutError("Qdrant startup readiness timed out")
        except BaseException:
            await self.shutdown()
            raise

    async def shutdown(self):
        process = self._process
        if process is None:
            return
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
        self._process = None
