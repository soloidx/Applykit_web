"""Canonicalization of extracted document text.

The output is bounded canonical UTF-8 text: normalized to NFC, with line
endings normalized, unsafe control characters removed, and meaningful
structure (headings and paragraphs) preserved. The bound is a hard failure,
never a truncation.
"""

import unicodedata

__all__ = ["TextOverBudget", "canonicalize"]

_LINE_SEPARATORS = frozenset(
    {
        "\x0b",  # vertical tab
        "\x0c",  # form feed
        "\x85",  # NEL
        "\u2028",  # line separator
        "\u2029",  # paragraph separator
    }
)

_REMOVED_CHARACTERS = frozenset(
    {chr(code) for code in range(0x00, 0x09)}
    | {chr(code) for code in range(0x0E, 0x20)}
    | {"\x7f"}
    | {chr(code) for code in range(0x80, 0xA0)}  # C1 controls
    | {chr(code) for code in range(0x202A, 0x202F)}  # bidi embedding controls
    | {chr(code) for code in range(0x2066, 0x206A)}  # bidi isolate controls
    | {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}
)


class TextOverBudget(Exception):
    """The canonical text exceeds the configured code point budget."""


def canonicalize(text: str, *, max_code_points: int) -> str:
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")

    lines: list[str] = []
    for line in normalized.split("\n"):
        rebuilt: list[str] = []
        for character in line:
            if character == "\t":
                rebuilt.append(" ")
            elif character in _LINE_SEPARATORS:
                rebuilt.append("\n")
            elif character in _REMOVED_CHARACTERS:
                continue
            else:
                rebuilt.append(character)
        lines.append("".join(rebuilt))
    flattened = "\n".join(lines)

    if len(flattened) > max_code_points:
        raise TextOverBudget

    collapsed: list[str] = []
    blank_run = 0
    for line in (line.strip() for line in flattened.split("\n")):
        if line == "":
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        collapsed.append(line)
    return "\n".join(collapsed).strip("\n")
