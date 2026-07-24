"""HTTP fetch — download a URL and reduce it to readable text.

Standard library only (runs in an isolated subprocess): urllib for the
request, html.parser to strip markup. Output is size-capped; the caller
(the tool loop) additionally fences the result as untrusted data.
"""

from __future__ import annotations

import contextlib
import gzip
import io
import json
import urllib.error
import urllib.request
from html.parser import HTMLParser

_MAX_DOWNLOAD = 2 * 1024 * 1024  # bytes read from the wire
_MAX_TEXT = 6_000  # characters returned to the model
_TIMEOUT_S = 15
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.title:
            self.title = data.strip()
        if not self._skip_depth and data.strip():
            self._chunks.append(data)

    def text(self) -> str:
        joined = "".join(self._chunks)
        lines = [" ".join(line.split()) for line in joined.splitlines()]
        return "\n".join(line for line in lines if line)


def run(url: str) -> str:
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return "Only http:// and https:// URLs can be fetched."
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; CleanWispr-tool/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/json,text/*;q=0.9",
            "Accept-Encoding": "gzip, identity",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
            raw = response.read(_MAX_DOWNLOAD)
            if response.headers.get("Content-Encoding", "") == "gzip":
                with contextlib.suppress(OSError):
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(_MAX_DOWNLOAD)
            content_type = (response.headers.get("Content-Type") or "").lower()
            charset = response.headers.get_content_charset() or "utf-8"
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        return f"HTTP error {exc.code} fetching {url}: {exc.reason}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return f"Could not fetch {url}: {exc}"

    body = raw.decode(charset, errors="replace")
    if "json" in content_type:
        # pretty-print JSON APIs so the model reads structure, not a blob
        with contextlib.suppress(json.JSONDecodeError):
            body = json.dumps(json.loads(body), indent=2, ensure_ascii=False)
        text, title = body, ""
    elif "html" in content_type or body.lstrip()[:1] == "<":
        parser = _TextExtractor()
        parser.feed(body)
        text, title = parser.text(), parser.title
    else:
        text, title = body, ""

    if len(text) > _MAX_TEXT:
        text = text[:_MAX_TEXT] + "\n… [page truncated]"
    header = f"URL: {final_url}" + (f"\nTitle: {title}" if title else "")
    return f"{header}\n\n{text}".strip()
