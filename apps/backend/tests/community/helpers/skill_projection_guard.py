"""Isolate mapping transport tests from the separately tested snapshot guard."""

class PassThroughSkillProjectionGuard:
    async def deliver(self, *, plan, send):
        return await send()
