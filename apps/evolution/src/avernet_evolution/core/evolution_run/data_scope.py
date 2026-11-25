"""Task-local propagation of a verified operator, never a credential source.

Control preflight binds the receipt's subject. A background operation instead
binds the subject from its persisted Run, never from a worker or the Bot owner.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from avernet_genome.models import BotRef

from .records import Run


@dataclass(frozen=True)
class DataScope:
    bot: BotRef
    user_id: str
    run_id: str | None = None
    parent: str | None = None
    strategy: str | None = None
    genes: tuple[str, ...] = ()


current_data_scope: ContextVar[DataScope | None] = ContextVar("evolution_data_scope", default=None)


@contextmanager
def data_scope(scope: DataScope | None) -> Iterator[None]:
    token = current_data_scope.set(scope)
    try:
        yield
    finally:
        current_data_scope.reset(token)


def run_data_scope(run: Run) -> DataScope | None:
    if run.trigger.operator_user_id is None:
        return None
    return DataScope(
        bot=run.bot,
        user_id=run.trigger.operator_user_id,
        run_id=run.id,
        parent=run.parent_revision,
        strategy=run.strategy_ref,
        genes=tuple(run.allowed_genes),
    )
