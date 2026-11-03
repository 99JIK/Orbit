"""공유 자료(텍스트, 링크, HTML, PDF, 이미지)에서 모델에 넘길 텍스트와 첨부를 뽑는다."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import httpx

TEXT_EXTS = {".txt", ".md", ".markdown"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
HTML_EXTS = {".html", ".htm"}
URL_EXTS = {".url", ".webloc"}
SUPPORTED = TEXT_EXTS | IMAGE_EXTS | HTML_EXTS | URL_EXTS | {".pdf"}

MAX_CHARS = 60000  # CLI 프롬프트 길이와 구독 사용량을 고려한 상한. 넘으면 앞부분만 쓴다
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


class UnsupportedFile(ValueError):
    pass


@dataclass
class Extracted:
    text: str
    images: list[Path] = field(default_factory=list)
    source_url: str = ""
    truncated: bool = False


class _HtmlText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"}

    def __init__(self):
        super().__init__()
        self.out: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.out.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    p = _HtmlText()
    p.feed(html)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return p.title.strip(), text


def fetch_url(url: str) -> str:
    with httpx.Client(follow_redirects=True, timeout=20,
                      headers={"User-Agent": "orbit-server/0.1"}) as c:
        r = c.get(url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        body = r.text[:2_000_000]
    if "html" in ctype or body.lstrip().startswith("<"):
        title, text = html_to_text(body)
        return f"제목: {title}\nURL: {url}\n\n{text}"
    return f"URL: {url}\n\n{body}"


def _clip(text: str) -> tuple[str, bool]:
    return (text, False) if len(text) <= MAX_CHARS else (text[:MAX_CHARS], True)


def _url_from_shortcut(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    m = URL_RE.search(raw)
    if not m:
        raise UnsupportedFile(f"URL 없음: {path.name}")
    return m.group(0)


def from_file(path: Path, fetch=fetch_url) -> Extracted:
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedFile(f"지원 안 하는 형식: {ext}")
    if ext in IMAGE_EXTS:
        return Extracted(text=f"(이미지 파일: {path.name})", images=[path])
    if ext in URL_EXTS:
        url = _url_from_shortcut(path)
        t, cut = _clip(fetch(url))
        return Extracted(text=t, source_url=url, truncated=cut)
    if ext in HTML_EXTS:
        title, text = html_to_text(path.read_text(encoding="utf-8", errors="replace"))
        t, cut = _clip(f"제목: {title}\n\n{text}")
        return Extracted(text=t, truncated=cut)
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n\n".join((pg.extract_text() or "") for pg in reader.pages).strip()
        if len(text) < 50:
            # 스캔본은 텍스트 레이어가 없다. 로컬 OCR이 없으므로 첨부로 넘긴다(claude·gemini는 PDF를 읽는다)
            return Extracted(text=f"(텍스트 없는 PDF: {path.name}, 첨부 참고)", images=[path])
        t, cut = _clip(text)
        return Extracted(text=t, truncated=cut)
    return from_text(path.read_text(encoding="utf-8", errors="replace"), fetch)


def from_text(text: str, fetch=fetch_url) -> Extracted:
    """share 이벤트의 payload.text. 링크 하나뿐이면 페이지를 가져온다."""
    text = text.strip()
    urls = URL_RE.findall(text)
    if len(urls) == 1 and text == urls[0]:
        t, cut = _clip(fetch(urls[0]))
        return Extracted(text=t, source_url=urls[0], truncated=cut)
    t, cut = _clip(text)
    return Extracted(text=t, truncated=cut)
