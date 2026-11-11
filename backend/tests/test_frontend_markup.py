"""Static checks on frontend/index.html — a stray closing tag once pushed the sizing
result out of its column (the result rendered inside the 280px parameter column)."""
import os
from html.parser import HTMLParser

INDEX = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "index.html")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.errors, self.ancestors = [], [], {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ancestors[a["id"]] = [(t, c) for t, c, _ in self.stack]
        if tag not in VOID:
            self.stack.append((tag, a.get("class") or "", self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()
            return
        self.errors.append((tag, self.getpos()[0], self.stack[-1][:3] if self.stack else None))
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def _parse():
    p = _Tree()
    with open(INDEX, encoding="utf-8") as fh:
        p.feed(fh.read())
    return p


def test_index_html_tags_are_balanced():
    p = _parse()
    assert p.errors == [], f"mismatched closing tags: {p.errors[:5]}"
    assert p.stack == [], f"unclosed tags: {p.stack[:5]}"


def test_sizing_result_sits_in_the_main_column():
    anc = _parse().ancestors["sizingResult"]
    classes = [c for _, c in anc]
    assert any("sim-shell" in c.split() for c in classes), "sizingResult must be inside .sim-shell"
    # and the PDF options panel stays inside its dropdown
    pdf = _parse().ancestors["btnSizingPdf"]
    assert any("sz-pdf-panel" in c for _, c in pdf)
