"""Authn SPI — port package retained for layout symmetry.

With the bot-token registry composition moved into ``BotTokenStrategy``
(mirrors BCS ``SessionTokenPlugin`` + ``BotRegistryLookup`` in one place), the
gateway's authn SPI no longer defines a bot-token validator protocol here. This
module is kept as the conventional home for any future flavor-swapped authn
ports.
"""

from __future__ import annotations
