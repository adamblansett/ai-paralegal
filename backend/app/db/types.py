import json
from typing import Any

from sqlalchemy import JSON, Dialect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator, TypeEngine, UserDefinedType


class _FallbackPgVector(UserDefinedType[list[float]]):
    """Fallback PostgreSQL VECTOR(dim) SQL type when pgvector python bindings are absent."""

    cache_ok = True

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def get_col_spec(self, **kw: Any) -> str:
        return f"VECTOR({self.dim})"


def _get_pgvector_type(dim: int) -> TypeEngine[Any]:
    try:
        from pgvector.sqlalchemy import Vector

        return Vector(dim)
    except ImportError:  # pragma: no cover - fallback when pgvector package is absent
        return _FallbackPgVector(dim)


class EmbeddingVector(TypeDecorator[list[float]]):
    """Dialect-aware embedding vector column.

    Uses pgvector `VECTOR(dim)` on PostgreSQL and JSON float arrays on SQLite.
    """

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int = 384) -> None:
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_get_pgvector_type(self.dim))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: list[float] | None, dialect: Dialect) -> list[float] | None:
        if value is None:
            return None
        return [float(x) for x in value]

    def process_result_value(self, value: Any, dialect: Dialect) -> list[float] | None:
        if value is None:
            return None
        if isinstance(value, str):
            parsed = json.loads(value)
            return [float(x) for x in parsed]
        return [float(x) for x in value]


JSONBCompat = JSON().with_variant(JSONB(), "postgresql")
