"""Enums used by query builders, value objects and dialects."""

from __future__ import annotations

from enum import StrEnum


class ChainEnum(StrEnum):
    """How a condition is chained to the condition before it."""

    AND = "AND"
    OR = "OR"


class ConditionEnum(StrEnum):
    """Condition kinds. The value is the SQL operator where one exists."""

    EQUALS = "="
    NOT_EQUALS = "<>"
    LESS_THAN = "<"
    LESS_THAN_OR_EQUALS = "<="
    GREATER_THAN = ">"
    GREATER_THAN_OR_EQUALS = ">="
    BETWEEN = "BETWEEN"
    NOT_BETWEEN = "NOT BETWEEN"
    LIKE = "LIKE"
    NOT_LIKE = "NOT LIKE"
    GLOB = "GLOB"
    NOT_GLOB = "NOT GLOB"
    IN = "IN"
    NOT_IN = "NOT IN"
    REGEX = "REGEX"
    NOT_REGEX = "NOT REGEX"
    EXISTS = "EXISTS"
    NOT_EXISTS = "NOT EXISTS"
    RAW = "RAW"


class JoinEnum(StrEnum):
    """Join kinds. The value is the SQL keyword sequence."""

    LEFT_JOIN = "LEFT JOIN"
    LEFT_JOIN_LATERAL = "LEFT JOIN LATERAL"
    INNER_JOIN = "INNER JOIN"
    INNER_JOIN_LATERAL = "INNER JOIN LATERAL"
    CROSS_JOIN = "CROSS JOIN"
    CROSS_JOIN_LATERAL = "CROSS JOIN LATERAL"

    @property
    def lateral(self) -> bool:
        """Whether this is a ``LATERAL`` join."""
        return self in (JoinEnum.LEFT_JOIN_LATERAL, JoinEnum.INNER_JOIN_LATERAL, JoinEnum.CROSS_JOIN_LATERAL)


class OrderByDirectionEnum(StrEnum):
    """Sort direction."""

    ASC = "ASC"
    DESC = "DESC"


class ReferentialActionEnum(StrEnum):
    """Foreign key ``ON UPDATE`` / ``ON DELETE`` actions."""

    NO_ACTION = "NO ACTION"
    RESTRICT = "RESTRICT"
    CASCADE = "CASCADE"
    SET_NULL = "SET NULL"
    SET_DEFAULT = "SET DEFAULT"


class TypeEnum(StrEnum):
    """Portable column types, mapped to SQL by :meth:`DialectABC.type`."""

    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    STRING = "string"
    DATETIME = "datetime"


class UnionEnum(StrEnum):
    """Set operation used to combine selects."""

    UNION = "UNION"
    UNION_ALL = "UNION ALL"


__all__ = [
    "ChainEnum",
    "ConditionEnum",
    "JoinEnum",
    "OrderByDirectionEnum",
    "ReferentialActionEnum",
    "TypeEnum",
    "UnionEnum",
]
