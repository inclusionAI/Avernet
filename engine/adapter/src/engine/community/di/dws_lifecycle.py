"""Optional hosting lifecycle: failures must not break interactive engines."""

import asyncio
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def hosting_lifecycle(app, active_engine: str):
    endpoint = None
    app.state.dws_management = None
    factory = getattr(app.state, "dws_management_factory", None)
    if factory is not None and active_engine == "openclaw":
        try:
            endpoint = await asyncio.to_thread(factory, active_engine)
            if endpoint is not None:
                await endpoint.service.restore()
                app.state.dws_management = endpoint
        except Exception:
            # COSEC: configuration and subprocess errors may contain credentials.
            logger.warning("DWS hosting unavailable; interactive engine remains active")
            if endpoint is not None:
                try:
                    await endpoint.service.close()
                except Exception:
                    logger.warning("DWS hosting cleanup failed")
                endpoint = None
    try:
        yield
    finally:
        app.state.dws_management = None
        if endpoint is not None:
            try:
                await endpoint.service.close()
            except Exception:
                logger.warning("DWS hosting cleanup failed")
