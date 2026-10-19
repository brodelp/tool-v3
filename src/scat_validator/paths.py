"""Bounded JSON Pointer construction from source references (spec §6.5, §7).

A :class:`PathNode` links to its parent instead of holding a concatenated string, so a huge
property name is measured once and never copied into every descendant path. Rendering
falls back to the deepest whole-token ancestor that is representable and within the limit.
"""

from dataclasses import dataclass
from typing import Final

from scat_validator.config import MAX_PATH_BYTES
from scat_validator.wire import contains_forbidden_code_point

_ROOT_TOKEN: Final = ""


def escape_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


class PathNode:
    __slots__ = ("_bytes", "_forbidden", "_token_bytes", "_token_forbidden", "parent", "token")

    def __init__(self, parent: PathNode | None, token: str) -> None:
        self.parent = parent
        self.token = token
        self._token_forbidden: bool | None = None
        self._token_bytes: int | None = None
        self._forbidden: bool | None = None
        self._bytes: int | None = None

    @classmethod
    def root(cls) -> PathNode:
        return cls(None, _ROOT_TOKEN)

    def child(self, token: str) -> PathNode:
        return PathNode(self, token)

    @property
    def token_forbidden(self) -> bool:
        if self._token_forbidden is None:
            self._token_forbidden = self.parent is not None and contains_forbidden_code_point(
                self.token
            )
        return self._token_forbidden

    @property
    def forbidden(self) -> bool:
        """True if this node or any ancestor has a name forbidden by the Unicode policy."""
        if self._forbidden is None:
            parent_forbidden = self.parent is not None and self.parent.forbidden
            self._forbidden = parent_forbidden or self.token_forbidden
        return self._forbidden

    @property
    def token_bytes(self) -> int:
        """UTF-8 bytes of ``"/" + escaped token``; only meaningful for non-forbidden tokens."""
        if self._token_bytes is None:
            if self.parent is None:
                self._token_bytes = 0
            else:
                token = self.token
                self._token_bytes = (
                    1 + len(token.encode("utf-8")) + token.count("~") + token.count("/")
                )
        return self._token_bytes

    @property
    def byte_length(self) -> int:
        """Escaped pointer length in UTF-8 bytes. Requires ``not self.forbidden``."""
        if self._bytes is None:
            parent_bytes = 0 if self.parent is None else self.parent.byte_length
            self._bytes = parent_bytes + self.token_bytes
        return self._bytes

    @property
    def is_exactly_representable(self) -> bool:
        return not self.forbidden and self.byte_length <= MAX_PATH_BYTES


@dataclass(frozen=True, slots=True)
class RenderedPath:
    pointer: str
    # Exact path replaced by an ancestor because it exceeds the byte limit (``path_truncated``).
    length_fallback: bool
    # Exact path replaced by an ancestor because it contains an unsupported property name.
    name_fallback: bool


def render(node: PathNode) -> RenderedPath:
    chain: list[PathNode] = []
    current: PathNode | None = node
    while current is not None and current.parent is not None:
        chain.append(current)
        current = current.parent
    chain.reverse()

    representable = 0
    while representable < len(chain) and not chain[representable].token_forbidden:
        representable += 1
    name_fallback = representable < len(chain)

    kept = 0
    total = 0
    while kept < representable:
        size = chain[kept].token_bytes
        if total + size > MAX_PATH_BYTES:
            break
        total += size
        kept += 1
    length_fallback = kept < representable

    pointer = "".join("/" + escape_token(n.token) for n in chain[:kept])
    return RenderedPath(pointer, length_fallback=length_fallback, name_fallback=name_fallback)
