"""Bounded, cancellable hosting subprocesses shared by DWS and notification CLI."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import aclosing, asynccontextmanager
from pathlib import Path

from engine.community.kernel.dws_connection import DwsError
from engine.community.plugins.dws_hosting.database import HostingDatabase


# uvloop rejects subprocess's umask keyword. Set it in a fresh interpreter,
# then exec the CLI so cancellation still targets the same process.
_PRIVATE_EXEC = "import os,sys; os.umask(0o077); os.execv(sys.argv[1], sys.argv[1:])"


class HostingCommandProcess:
    def __init__(
        self,
        command: tuple[str, ...],
        work_dir: Path,
        environment: Mapping[str, str],
        *,
        timeout: float = 60,
        line_limit: int = 262_144,
        output_limit: int = 8_388_608,
        shutdown_seconds: float = 5,
    ) -> None:
        if not command or not Path(command[0]).is_absolute():
            raise ValueError("hosting executable must be an absolute trusted path")
        if (
            timeout <= 0
            or line_limit <= 0
            or output_limit <= 0
            or shutdown_seconds <= 0
        ):
            raise ValueError("invalid DWS process bounds")
        work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        HostingDatabase._check_private(work_dir, directory=True)
        self.work_dir = work_dir
        self.command = command
        allowed = {
            "PATH",
            "HOME",
            "TMPDIR",
            "LANG",
            "LC_ALL",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "NO_PROXY",
            "SSL_CERT_FILE",
        }
        self.environment = {k: v for k, v in environment.items() if k in allowed}
        self.environment.update(NO_COLOR="1")
        self.timeout, self.line_limit, self.output_limit = (
            timeout,
            line_limit,
            output_limit,
        )
        self.shutdown_seconds = shutdown_seconds

    @asynccontextmanager
    async def open(self, args: tuple[str, ...]):
        process = None
        # COSEC: fixed bootstrap code; all CLI arguments stay argv, never shell/code.
        # The parent's umask is unchanged, so concurrent chat/file operations are isolated.
        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-S",
                "-c",
                _PRIVATE_EXEC,
                *self.command,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.environment,
                cwd=self.work_dir,
                limit=self.line_limit,
                start_new_session=True,
            )
        )
        try:
            process = await asyncio.shield(spawn)
            yield process
        except (OSError, ValueError):
            raise DwsError("process_failed") from None
        finally:

            async def close():
                target = process
                if target is None:
                    try:
                        target = await spawn
                    except (OSError, ValueError):
                        return
                if target.stdin is not None:
                    target.stdin.close()
                if target.returncode is None:
                    try:
                        target.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(target.wait(), self.shutdown_seconds)
                    except TimeoutError:
                        try:
                            target.kill()
                        except ProcessLookupError:
                            pass
                        await target.wait()

            closing = asyncio.create_task(close())
            cancelled = False
            while not closing.done():
                try:
                    await asyncio.shield(closing)
                except asyncio.CancelledError:
                    cancelled = True
            closing.result()
            if cancelled:
                raise asyncio.CancelledError

    async def lines(self, process) -> AsyncIterator[tuple[str, str]]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=128)

        async def pump(name, stream):
            try:
                while raw := await stream.readline():
                    await queue.put(
                        (name, raw.decode("utf-8", errors="strict").rstrip("\r\n"))
                    )
            except (ValueError, UnicodeError):
                await queue.put(DwsError("invalid_output"))
            finally:
                # Cancelled pumps need not signal EOF to an already closing consumer.
                if not asyncio.current_task().cancelling():
                    await queue.put(None)

        tasks = [
            asyncio.create_task(pump("stdout", process.stdout)),
            asyncio.create_task(pump("stderr", process.stderr)),
        ]
        try:
            ended = 0
            while ended < 2:
                item = await queue.get()
                if item is None:
                    ended += 1
                elif isinstance(item, Exception):
                    raise item
                else:
                    yield item
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def capture(self, args: tuple[str, ...]) -> str:
        try:
            async with (
                asyncio.timeout(self.timeout),
                self.open(args) as process,
                aclosing(self.lines(process)) as lines,
            ):
                chunks, size = [], 0
                async for channel, line in lines:
                    size += len(line.encode("utf-8"))
                    if size > self.output_limit:
                        raise DwsError("output_limit")
                    if channel == "stdout":
                        chunks.append(line)
                if await process.wait() != 0:
                    raise DwsError("command_failed")
                return "\n".join(chunks)
        except TimeoutError:
            raise DwsError("command_timeout") from None
