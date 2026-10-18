"""SQL text plus the parameters bound to its placeholders."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...exceptions import QueryWithParamsError

if TYPE_CHECKING:
    from ...dialects.dialect_abc import DialectABC
    from ..type_aliases import Scalar

REGEX_PATTERN = re.compile(
    r"""
      '(?:\\.|[^\\'])*'                       # single quoted string ('' is two adjacent strings)
    | "(?:\\.|[^\\"])*"                       # double quoted identifier / string
    | `(?:\\.|[^\\`])*`                       # backtick quoted identifier
    | --[^\r\n]*                              # line comment
    | /\*.*?\*/                               # block comment
    | (?P<question_mark>\?)                   # positional placeholder
    | (?<![:\w])(?P<named_param>:[A-Za-z_]\w*)  # named placeholder; not a ::cast and not inside a word
    """,
    re.VERBOSE | re.DOTALL,
)
"""Tokenizer that finds placeholders while skipping quoted strings, quoted identifiers and comments.

MySQL ``#`` comments and PostgreSQL dollar quoted strings are deliberately not recognised: ``#`` is an operator
prefix in PostgreSQL (``#>``, ``#-``) and placeholders never belong in function bodies.
"""


@dataclass(frozen=True, slots=True)
class QueryWithParams:
    """SQL text plus the parameters bound to its placeholders.

    Dialects always emit ``?`` placeholders with a positional ``params`` sequence. User supplied SQL
    (:meth:`DatabaseABC.prepared`, ``where(sql, values)``) may instead use ``:name`` placeholders with a mapping;
    the mapping keys may be given with or without the leading colon.
    """

    query: str
    params: Sequence[Scalar] | Mapping[str, Scalar] = ()

    def named_params_to_question_marks(self) -> QueryWithParams:
        """Rewrite ``:name`` placeholders to ``?`` with positional params, in placeholder order.

        Positional queries are returned unchanged. A named parameter may be referenced several times.

        :raises QueryWithParamsError: A placeholder has no parameter, or ``?`` and ``:name`` are mixed.
        """
        params = self.params

        if not isinstance(params, Mapping):
            return self

        positional: list[Scalar] = []

        def replace(match: re.Match[str]) -> str:
            if match["question_mark"] is not None:
                raise QueryWithParamsError("cannot mix ? placeholders with named params")

            named_param = match["named_param"]

            if named_param is None:
                return match[0]

            positional.append(_named_value(params, named_param))

            return "?"

        query = REGEX_PATTERN.sub(replace, self.query)

        return QueryWithParams(query, positional)

    def question_marks_to_percent_s(self) -> QueryWithParams:
        """Convert to ``format`` paramstyle (psycopg): ``?`` becomes ``%s`` and every literal ``%`` becomes ``%%``.

        Named params are converted to positional first.

        :raises QueryWithParamsError: The number of placeholders and params differ.
        """
        positional = self.named_params_to_question_marks()
        params = list(positional.params)
        count = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal count

            if match["question_mark"] is not None:
                count += 1

                return "%s"

            return match[0].replace("%", "%%")

        query = _sub_outside_tokens(REGEX_PATTERN, replace, positional.query, lambda text: text.replace("%", "%%"))

        if count != len(params):
            raise QueryWithParamsError("question mark and param count do not match")

        return QueryWithParams(query, params)

    def to_sql(self, dialect: DialectABC) -> str:
        """Interpolate every parameter as a literal using :meth:`DialectABC.cast_to_query`.

        Used for emulated prepares, debug output and :meth:`Query.to_sql`. Returns ``query`` unchanged when there
        are no params.

        :raises QueryWithParamsError: A placeholder has no parameter (or vice versa).
        """
        params = self.params

        if not params:
            return self.query

        if isinstance(params, Mapping):

            def replace_named(match: re.Match[str]) -> str:
                named_param = match["named_param"]

                if named_param is None:
                    if match["question_mark"] is not None:
                        raise QueryWithParamsError("cannot mix ? placeholders with named params")

                    return match[0]

                return dialect.cast_to_query(_named_value(params, named_param))

            return REGEX_PATTERN.sub(replace_named, self.query)

        values = iter(params)
        count = 0

        def replace_positional(match: re.Match[str]) -> str:
            nonlocal count

            if match["question_mark"] is None:
                return match[0]

            count += 1

            try:
                return dialect.cast_to_query(next(values))
            except StopIteration:
                raise QueryWithParamsError("question mark and param count do not match") from None

        query = REGEX_PATTERN.sub(replace_positional, self.query)

        if count != len(params):
            raise QueryWithParamsError("question mark and param count do not match")

        return query


def _named_value(params: Mapping[str, Scalar], named_param: str) -> Scalar:
    """Look up ``:name`` in ``params``, accepting keys with or without the colon."""
    for key in (named_param[1:], named_param):
        if key in params:
            return params[key]

    raise QueryWithParamsError(f"named param {named_param} does not exist")


def _sub_outside_tokens(
    pattern: re.Pattern[str],
    replace_token: Callable[[re.Match[str]], str],
    text: str,
    replace_between: Callable[[str], str],
) -> str:
    """``pattern.sub(replace_token, text)`` that also maps the text between matches through ``replace_between``."""
    parts: list[str] = []
    position = 0

    for match in pattern.finditer(text):
        parts.append(replace_between(text[position : match.start()]))
        parts.append(replace_token(match))
        position = match.end()

    parts.append(replace_between(text[position:]))

    return "".join(parts)


__all__ = ["REGEX_PATTERN", "QueryWithParams"]
