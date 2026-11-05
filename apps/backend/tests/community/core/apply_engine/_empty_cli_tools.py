"""An empty CLI tool service for apply lifecycle fixtures."""


class EmptyCliTools:
    def list(self, _ctx):
        return ()

    async def replace_all(self, _ctx, tools, *, installed_by):
        assert not tools
        return ()
