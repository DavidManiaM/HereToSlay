"""``$ref`` resolution and the tiny expression language.

Card data addresses the game with strings: ``$self``, ``$event.player``,
``$rules.turn.action_points_per_turn``, ``$victim``. This module is the half of
that which does not need to know what a game *is* — splitting a reference,
walking a dotted path, and evaluating ``{expr: "$rules.turn.hand_limit - 1"}``.
:class:`~here_to_slay.core.context.EffectContext` supplies the roots.

Why an evaluator at all, rather than ``eval``? ``repeat.times`` is documented as
"may be an expression" (``docs/card_schemas.md §3.1``), and a variant will want
``"$rules.turn.action_points_per_turn + 1"`` without a Python change. ``eval``
on content is both a security hole and non-deterministic (it can reach anything
importable); forty lines of recursive descent over ``+ - * / %`` and parentheses
is exactly as expressive as card text ever needs and cannot do anything else.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from here_to_slay.core.errors import EffectError

#: ``$name`` or ``$name.path.to.thing``
REF_PATTERN = re.compile(r"^\$[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)*$")
REF_TOKEN = re.compile(r"\$[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)*")

Resolver = Callable[[str], Any]


def is_ref(value: Any) -> bool:
    """Whether a raw parameter value is a ``$reference``."""
    return isinstance(value, str) and value.startswith("$")


def split_ref(ref: str) -> tuple[str, tuple[str, ...]]:
    """``"$event.player"`` -> ``("event", ("player",))``."""
    if not is_ref(ref):
        raise EffectError(f"not a reference: {ref!r}")
    if not REF_PATTERN.match(ref):
        raise EffectError(f"malformed reference {ref!r} — expected $name or $name.path")
    root, _, rest = ref.removeprefix("$").partition(".")
    return root, tuple(part for part in rest.split(".") if part)


def member(value: Any, name: str, *, where: str = "") -> Any:
    """One step of a dotted path: mapping key, sequence index, or attribute.

    Deliberately permissive about *shape* and strict about *failure*: content
    should be able to say ``$event.player`` without caring whether the payload
    is a dict or a dataclass, but a typo must name the path it died on.
    """
    if isinstance(value, Mapping):
        if name in value:
            return value[name]
    elif isinstance(value, Sequence) and not isinstance(value, str) and name.isdigit():
        index = int(name)
        if index < len(value):
            return value[index]
        raise EffectError(f"{where or 'value'} has no element {index} (length {len(value)})")
    if hasattr(value, name):
        return getattr(value, name)
    raise EffectError(f"{where or type(value).__name__} has no '{name}'")


def follow_path(
    value: Any, path: Sequence[str], *, where: str = "", deref: Resolver | None = None
) -> Any:
    """Walk a dotted path, optionally dereferencing ids on the way.

    ``deref`` is how ``$card.attached_to`` works: ``$card`` resolves to a
    ``CardId`` (a string), and the context hands in a callback that turns it
    back into the ``CardInstance`` whose attributes the path wants.
    """
    current = value
    for index, name in enumerate(path):
        if deref is not None:
            current = deref(current)
        current = member(current, name, where=f"{where}.{'.'.join(path[:index])}".rstrip("."))
    return current


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""
    \s*(?:
        (?P<ref>\$[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)*)
      | (?P<number>\d+)
      | (?P<op>[-+*/%()])
    )
    """,
    re.VERBOSE,
)


def _tokenize(expression: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(expression):
        if expression[position].isspace():
            position += 1
            continue
        match = _TOKEN.match(expression, position)
        if match is None:
            raise EffectError(
                f"cannot parse expression {expression!r} at character {position} "
                f"({expression[position]!r}); expressions allow $refs, integers and + - * / % ( )"
            )
        kind = match.lastgroup or ""
        tokens.append((kind, match.group(kind)))
        position = match.end()
    return tokens


class _Parser:
    """Recursive descent over ``expr := term (('+'|'-') term)*``."""

    def __init__(self, tokens: Sequence[tuple[str, str]], resolve: Resolver, source: str) -> None:
        self.tokens = tokens
        self.resolve = resolve
        self.source = source
        self.position = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise EffectError(f"expression {self.source!r} ends unexpectedly")
        self.position += 1
        return token

    def parse(self) -> int | float:
        value = self.sum()
        if self.peek() is not None:
            raise EffectError(f"unexpected {self.peek()[1]!r} in expression {self.source!r}")  # type: ignore[index]
        return value

    def sum(self) -> int | float:
        value = self.product()
        while (token := self.peek()) and token[1] in {"+", "-"}:
            self.take()
            right = self.product()
            value = value + right if token[1] == "+" else value - right
        return value

    def product(self) -> int | float:
        value = self.unary()
        while (token := self.peek()) and token[1] in {"*", "/", "%"}:
            self.take()
            right = self.unary()
            if token[1] in {"/", "%"} and right == 0:
                raise EffectError(f"division by zero in expression {self.source!r}")
            if token[1] == "*":
                value = value * right
            elif token[1] == "/":
                # Integer division: card counts are whole cards.
                value = value // right
            else:
                value = value % right
        return value

    def unary(self) -> int | float:
        token = self.peek()
        if token and token[1] == "-":
            self.take()
            return -self.unary()
        if token and token[1] == "+":
            self.take()
            return self.unary()
        return self.atom()

    def atom(self) -> int | float:
        kind, text = self.take()
        if kind == "number":
            return int(text)
        if kind == "ref":
            return _as_number(self.resolve(text), text, self.source)
        if text == "(":
            value = self.sum()
            closing = self.take()
            if closing[1] != ")":
                raise EffectError(f"expected ')' in expression {self.source!r}")
            return value
        raise EffectError(f"unexpected {text!r} in expression {self.source!r}")


def _as_number(value: Any, ref: str, source: str) -> int | float:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return value
    if isinstance(value, Sequence) and not isinstance(value, str):
        return len(value)  # "$my_heroes * 2" reads naturally as a count
    raise EffectError(
        f"reference '{ref}' in expression {source!r} is {type(value).__name__}, not a number"
    )


def evaluate_expression(expression: str, resolve: Resolver) -> Any:
    """Evaluate an ``expr`` string.

    A lone reference is returned *unchanged* — ``{expr: "$self"}`` should hand
    back a ``PlayerId``, not try to make a number of one. Anything longer is
    arithmetic and must produce a number.
    """
    stripped = expression.strip()
    if REF_PATTERN.match(stripped):
        return resolve(stripped)
    tokens = _tokenize(stripped)
    if not tokens:
        raise EffectError("empty expression")
    return _Parser(tokens, resolve, stripped).parse()


def refs_in(expression: str) -> tuple[str, ...]:
    """Every ``$ref`` an expression mentions — used by the validator's twin."""
    return tuple(match.group(0) for match in REF_TOKEN.finditer(expression))


__all__ = [
    "evaluate_expression",
    "follow_path",
    "is_ref",
    "member",
    "refs_in",
    "split_ref",
]
