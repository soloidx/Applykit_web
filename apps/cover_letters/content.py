from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

_ALLOWED_TAGS = frozenset({"p", "br", "strong", "em", "ul", "ol", "li", "a"})
_SUPPRESSED_TAGS = frozenset({"script", "style", "template"})


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[str | None] = []
        self.suppressed_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_values = dict(attrs)
        if tag in _SUPPRESSED_TAGS:
            self.suppressed_depth += 1
            self.stack.append(None)
            return
        if self.suppressed_depth:
            self.stack.append(None)
            return
        if tag == "br":
            self.parts.append("<br>")
            return
        if tag not in _ALLOWED_TAGS:
            self.stack.append(None)
            return
        if tag == "ol" and attr_values.get("data-list") == "bullet":
            tag = "ul"
        elif tag == "li" and attr_values.get("data-list") == "bullet":
            for index in range(len(self.parts) - 1, -1, -1):
                if self.parts[index] == "<ol>":
                    self.parts[index] = "<ul>"
                    break
            if self.stack and self.stack[-1] == "ol":
                self.stack[-1] = "ul"
        if tag == "a":
            href = _safe_href(attr_values.get("href"))
            if href is None:
                self.stack.append(None)
                return
            self.parts.append(
                f'<a href="{escape(href, quote=True)}" rel="nofollow noopener noreferrer">'
            )
        else:
            self.parts.append(f"<{tag}>")
        self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() != "br":
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "ol" and self.stack and self.stack[-1] == "ul":
            tag = "ul"
        if tag in _SUPPRESSED_TAGS:
            if self.suppressed_depth:
                self.suppressed_depth -= 1
            if self.stack:
                self.stack.pop()
            return
        if self.suppressed_depth:
            if self.stack:
                self.stack.pop()
            return
        if tag == "br" or not self.stack:
            return
        for index in range(len(self.stack) - 1, -1, -1):
            open_tag = self.stack[index]
            if open_tag == tag:
                for closed_tag in reversed(self.stack[index:]):
                    if closed_tag is not None:
                        self.parts.append(f"</{closed_tag}>")
                del self.stack[index:]
                return
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        if not self.suppressed_depth:
            self.parts.append(escape(data, quote=False))


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in _SUPPRESSED_TAGS:
            self.suppressed_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _SUPPRESSED_TAGS and self.suppressed_depth:
            self.suppressed_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.suppressed_depth:
            self.parts.append(data)


def _safe_href(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        if not parsed.netloc:
            return None
        return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment))
    if scheme == "mailto" and parsed.path:
        return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment))
    return None


def sanitize_cover_letter_html(value: str) -> str:
    parser = _Sanitizer()
    parser.feed(value)
    parser.close()
    for open_tag in reversed(parser.stack):
        if open_tag is not None:
            parser.parts.append(f"</{open_tag}>")
    return "".join(parser.parts)


def cover_letter_visible_text(value: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)
