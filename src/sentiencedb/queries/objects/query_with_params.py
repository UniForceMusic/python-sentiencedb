"""SQL text plus the parameters bound to its placeholders, and the per-engine tokenizer that finds placeholders."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...driver import Driver
from ...exceptions import QueryWithParamsError

if TYPE_CHECKING:
    from ...dialects.dialect_abc import DialectABC
    from ..type_aliases import Scalar

_PLACEHOLDERS = r"""
    | (?P<question_mark>\?)                     # positional placeholder
    | (?<![:\w])(?P<named_param>:[A-Za-z_]\w*)  # named placeholder; not a ::cast and not inside a word
"""

_SQLITE = re.compile(
    r"""
      '[^']*'                                   # string ('' is two adjacent strings)
    | "[^"]*" | `[^`]*` | \[[^\]]*\]            # quoted identifiers
    | --[^\r\n]*                                # line comment
    | (?P<block_comment>/\*)                    # block comment, not nested
    """
    + _PLACEHOLDERS,
    re.VERBOSE,
)

_PGSQL = re.compile(
    r"""
      (?<![\w$])[Ee]'(?:\\.|[^\\'])*'           # escape string E'..', with backslash escapes
    | '[^']*'                                   # standard conforming string
    | "[^"]*"                                   # quoted identifier
    | (?<![\w$])\$(?P<tag>(?:[^\W\d]\w*)?)\$.*?\$(?P=tag)\$  # dollar quoted string $tag$..$tag$
    | --[^\r\n]*                                # line comment
    | (?P<block_comment>/\*)                    # block comment, nested
    """
    + _PLACEHOLDERS,
    re.VERBOSE | re.DOTALL,
)

_MYSQL = re.compile(
    r"""
      '(?:\\.|[^\\'])*' | "(?:\\.|[^\\"])*"     # strings, with backslash escapes
    | `[^`]*`                                   # quoted identifier
    | \#[^\r\n]* | --(?=\s|$)[^\r\n]*           # line comments; -- needs a space after it
    | (?P<block_comment>/\*)                    # block comment, not nested
    """
    + _PLACEHOLDERS,
    re.VERBOSE | re.DOTALL,
)

_TOKENIZERS: dict[Driver, tuple[re.Pattern[str], bool]] = {
    Driver.SQLITE: (_SQLITE, False),
    Driver.PGSQL: (_PGSQL, True),
    Driver.MYSQL: (_MYSQL, False),
    Driver.MARIADB: (_MYSQL, False),
}
"""Per engine: the token pattern and whether block comments nest."""

_COMMENT_DELIMITER = re.compile(r"/\*|\*/")


@dataclass(frozen=True, slots=True)
class QueryWithParams:
    """SQL text plus the parameters bound to its placeholders.

    Dialects always emit ``?`` placeholders with a positional ``params`` sequence. User supplied SQL
    (:meth:`DatabaseABC.prepared`, ``where(sql, values)``, :func:`expression`) may instead use ``:name``
    placeholders with a mapping; the mapping keys may be given with or without the leading colon.

    Placeholders are found by a tokenizer that skips the strings, quoted identifiers and comments of the dialect's
    engine, plus ``--`` and ``/* */`` comments everywhere:

    * SQLite: ``'..'``; identifiers in double quotes, backticks or ``[..]``.
    * PostgreSQL: ``'..'``, ``E'..'`` (backslash escapes), ``$tag$..$tag$``; ``".."`` identifiers; nested block
      comments.
    * MySQL / MariaDB: ``'..'`` and ``".."`` with backslash escapes; backtick identifiers; ``#`` comments (``--``
      needs whitespace after it).

    Everywhere else a ``?`` is a placeholder, including PostgreSQL's ``?``, ``?|`` and ``?&`` operators; see the
    README for the workarounds.
    """

    query: str
    params: Sequence[Scalar] | Mapping[str, Scalar] = ()

    def named_params_to_question_marks(self, dialect: DialectABC) -> QueryWithParams:
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

            positional.append(_named_value(params, match["named_param"]))

            return "?"

        return QueryWithParams(_substitute(self.query, dialect, replace), positional)

    def count_question_marks(self, dialect: DialectABC) -> int:
        """The number of ``?`` placeholders in :attr:`query`."""
        return sum(match["question_mark"] is not None for match in _placeholders(self.query, dialect))

    def question_marks_to_percent_s(self, dialect: DialectABC) -> QueryWithParams:
        """Convert to ``format`` paramstyle (psycopg): ``?`` becomes ``%s`` and every other ``%`` -- in strings and
        comments too, as psycopg does not parse SQL -- becomes ``%%``.

        Named params are converted to positional first.

        :raises QueryWithParamsError: The number of placeholders and params differ.
        """
        positional = self.named_params_to_question_marks(dialect)
        params = list(positional.params)
        count = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal count

            if match["question_mark"] is None:
                return match[0]

            count += 1

            return "%s"

        query = _substitute(positional.query, dialect, replace, lambda text: text.replace("%", "%%"))

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
                if match["question_mark"] is not None:
                    raise QueryWithParamsError("cannot mix ? placeholders with named params")

                return dialect.cast_to_query(_named_value(params, match["named_param"]))

            return _substitute(self.query, dialect, replace_named)

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

        query = _substitute(self.query, dialect, replace_positional)

        if count != len(params):
            raise QueryWithParamsError("question mark and param count do not match")

        return query


def _named_value(params: Mapping[str, Scalar], named_param: str) -> Scalar:
    """Look up ``:name`` in ``params``, accepting keys with or without the colon."""
    for key in (named_param[1:], named_param):
        if key in params:
            return params[key]

    raise QueryWithParamsError(f"named param {named_param} does not exist")


def _placeholders(query: str, dialect: DialectABC) -> Iterator[re.Match[str]]:
    """The ``?`` and ``:name`` placeholders of ``query``, outside the strings, quoted identifiers and comments of
    the dialect's engine."""
    pattern, nested_comments = _TOKENIZERS[dialect.driver]
    position = 0

    while (match := pattern.search(query, position)) is not None:
        position = match.end()

        if match["block_comment"] is not None:
            position = _block_comment_end(query, position, nested_comments)
        elif match["question_mark"] is not None or match["named_param"] is not None:
            yield match


def _block_comment_end(query: str, position: int, nested: bool) -> int:
    """The end of the block comment whose ``/*`` ends at ``position``; the end of ``query`` when unterminated."""
    depth = 1

    for delimiter in _COMMENT_DELIMITER.finditer(query, position):
        if delimiter[0] == "*/":
            depth -= 1
        elif nested:
            depth += 1

        if depth == 0:
            return delimiter.end()

    return len(query)


def _substitute(
    query: str,
    dialect: DialectABC,
    replace: Callable[[re.Match[str]], str],
    replace_text: Callable[[str], str] = str,
) -> str:
    """``query`` with every placeholder replaced by ``replace(match)`` and the text around them by
    ``replace_text(text)``."""
    parts: list[str] = []
    position = 0

    for match in _placeholders(query, dialect):
        parts += (replace_text(query[position : match.start()]), replace(match))
        position = match.end()

    parts.append(replace_text(query[position:]))

    return "".join(parts)


__all__ = ["QueryWithParams"]
