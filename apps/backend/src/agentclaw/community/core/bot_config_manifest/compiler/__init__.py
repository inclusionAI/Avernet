"""The Manifest compiler: a stored document → the apply engine's ``DesiredState``.

Everything that reads a document's grammar, resolves a source, fetches, and
files bytes in the content store lives under this package. What comes out
names bytes already in the store; the apply engine never sees a document,
a source or a fetch. ``compile.ManifestCompiler`` is the
``DesiredStateCompiler`` for ``MANIFEST_STATE`` (kind ``manifest``); ``entries/`` holds one
module per construct.
"""

from agentclaw.community.core.bot_config_manifest.compiler.compile import ManifestCompiler
from agentclaw.community.core.bot_config_manifest.compiler.declared import declared_entries

__all__ = ["ManifestCompiler", "declared_entries"]
