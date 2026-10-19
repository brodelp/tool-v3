"""Wire decoding: bounded body acquisition, strict UTF-8/JSON decoding, and lossless numbers.

The decoded source tree uses only ``dict``, ``list``, ``str``, ``bool``, ``None`` and
:class:`JsonNumber`. ``JsonNumber`` keeps the exact original token, so no number is ever
rounded through binary floating point (spec §4).
"""

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from itertools import accumulate, count
from operator import sub
from typing import Final, Literal

from scat_validator.config import MAX_JSON_DEPTH, MAX_SAFE_NUMBER

# U+0000 and unpaired surrogates (a valid escaped pair decodes to one scalar, never to these).
_FORBIDDEN_CODE_POINT: Final = re.compile("[\x00\ud800-\udfff]")

_NUMBER_TOKEN: Final = re.compile(r"(-?)(0|[1-9][0-9]*)(?:\.([0-9]+))?(?:[eE]([-+]?)([0-9]+))?")
# Exponents with more digits than this exceed any coefficient length a 5 MiB body can hold.
_MAX_EXPONENT_DIGITS: Final = 18
_SAFE_DIGITS: Final = len(str(MAX_SAFE_NUMBER))  # 16


def contains_forbidden_code_point(text: str) -> bool:
    """True if ``text`` contains U+0000 or an unpaired surrogate (spec §4.2)."""
    return _FORBIDDEN_CODE_POINT.search(text) is not None


class JsonNumber:
    """A JSON number retained as its exact source token.

    ``is_integer_token`` is true when the token has neither a decimal point nor an exponent.
    Range membership is decided from the token's digits without expanding exponents or
    rounding, and is cached because the same node may be consulted by several checks.
    """

    __slots__ = ("_safe", "is_integer_token", "token")

    def __init__(self, token: str, *, is_integer_token: bool) -> None:
        self.token = token
        self.is_integer_token = is_integer_token
        self._safe: bool | None = None

    def __repr__(self) -> str:
        return f"JsonNumber({self.token!r})"

    @property
    def is_safe(self) -> bool:
        """Whether the exact value lies within ±9007199254740991."""
        if self._safe is None:
            self._safe = _token_is_safe(self.token)
        return self._safe

    def safe_int(self) -> int | None:
        """The exact ``int`` of a safe integer token (at most 16 digits); otherwise ``None``."""
        if self.is_integer_token and self.is_safe:
            return int(self.token)
        return None


def _token_is_safe(token: str) -> bool:
    match = _NUMBER_TOKEN.fullmatch(token)
    if match is None:  # pragma: no cover - the JSON scanner only produces grammar-valid tokens
        raise ValueError("not a JSON number token")
    _, int_part, frac_part, exp_sign, exp_digits = match.groups()
    frac = frac_part or ""
    digits = (int_part + frac).lstrip("0")
    if not digits:
        return True  # any zero, including -0 and 0e999999
    significant = digits.rstrip("0")
    exp_text = (exp_digits or "0").lstrip("0") or "0"
    if len(exp_text) > _MAX_EXPONENT_DIGITS:
        # Enormous exponent: magnitude is either far above the bound or a tiny nonzero fraction.
        return exp_sign == "-"
    exponent = int(exp_text) * (-1 if exp_sign == "-" else 1)
    # value = int(significant) * 10**scale, with no trailing zeros in ``significant``
    scale = exponent - len(frac) + (len(digits) - len(significant))
    integer_digits = len(significant) + scale
    if integer_digits < _SAFE_DIGITS:
        return True
    if integer_digits > _SAFE_DIGITS:
        return False
    if scale >= 0:
        return int(significant + "0" * scale) <= MAX_SAFE_NUMBER
    # A nonzero fraction remains, so the value exceeds its integer part.
    return int(significant[:_SAFE_DIGITS]) < MAX_SAFE_NUMBER


type JsonValue = dict[str, JsonValue] | list[JsonValue] | str | JsonNumber | bool | None

type DecodeFailureCode = Literal["INVALID_JSON", "DUPLICATE_PROPERTY", "LIMIT_EXCEEDED"]


class DecodeFailure(Exception):
    """Gate-3 failure. Carries only a code, never source content."""

    def __init__(self, code: DecodeFailureCode) -> None:
        super().__init__(code)
        self.code: DecodeFailureCode = code


@dataclass(frozen=True, slots=True)
class DecodedDocument:
    value: JsonValue
    # The validated JSON document as received, without a transport BOM or outer whitespace.
    # Only used to embed an exact echo after every gate has passed.
    document_bytes: bytes


_UTF8_BOM: Final = b"\xef\xbb\xbf"
_JSON_WHITESPACE: Final = b" \t\n\r"

# Removes JSON strings (including unterminated ones) and every non-bracket character, leaving
# only structural brackets. Brackets and escaped quotes inside strings never count.
_DEPTH_NOISE: Final = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"?|[^\[\]{}"]+', re.DOTALL)
# Openers map to 2 and closers to 0, so depth after byte i is cumsum[i] - (i + 1).
_DEPTH_STEPS: Final = bytes.maketrans(b"[{]}", b"\x02\x02\x00\x00")


def exceeds_depth(text: str, limit: int = MAX_JSON_DEPTH) -> bool:
    """String-aware container-depth guard run before recursive JSON decoding."""
    brackets = _DEPTH_NOISE.sub("", text).encode("ascii").translate(_DEPTH_STEPS)
    deepest = max(map(sub, accumulate(brackets), count(1)), default=0)
    return deepest > limit


def _reject_constant(_name: str) -> JsonValue:
    raise ValueError("nonstandard JSON constant")


def _int_token(token: str) -> JsonNumber:
    return JsonNumber(token, is_integer_token=True)


def _float_token(token: str) -> JsonNumber:
    return JsonNumber(token, is_integer_token=False)


def decode_document(body: bytes) -> DecodedDocument:
    """Gate 3: strict UTF-8, one optional leading BOM, depth guard, JSON decoding, duplicates."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise DecodeFailure("INVALID_JSON") from None
    bom_bytes = 0
    if text.startswith("\ufeff"):
        text = text[1:]
        bom_bytes = len(_UTF8_BOM)
    if exceeds_depth(text):
        raise DecodeFailure("LIMIT_EXCEEDED")

    duplicate_seen = False

    def object_pairs(pairs: list[tuple[str, JsonValue]]) -> JsonValue:
        nonlocal duplicate_seen
        obj = dict(pairs)
        if len(obj) != len(pairs):
            # Keep parsing: a syntax error anywhere takes precedence over a duplicate name.
            duplicate_seen = True
        return obj

    try:
        value: JsonValue = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_int=_int_token,
            parse_float=_float_token,
            parse_constant=_reject_constant,
        )
    except ValueError:  # JSONDecodeError (incl. a repeated BOM) and rejected constants
        raise DecodeFailure("INVALID_JSON") from None
    if duplicate_seen:
        raise DecodeFailure("DUPLICATE_PROPERTY")
    document = body[bom_bytes:].strip(_JSON_WHITESPACE)
    return DecodedDocument(value=value, document_bytes=document)


async def read_limited_body(chunks: AsyncIterator[bytes], limit: int) -> bytes | None:
    """Accumulate the body while counting bytes; ``None`` as soon as it exceeds ``limit``."""
    buffer = bytearray()
    async for chunk in chunks:
        if len(buffer) + len(chunk) > limit:
            return None
        buffer += chunk
    return bytes(buffer)
