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


def test_views_use_the_shared_ui_helpers():
    """Super app: one component set. Browser alert / prompt / confirm, hand-made downloads and
    hand-made <dialog> elements live in ui.js only (uiAlert, uiPrompt, uiConfirm, uiDownload, uiDialog)."""
    import pathlib
    import re
    js = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "js"
    allowed = {"ui.js"}
    bad = []
    for f in sorted(js.rglob("*.js")):
        if f.name in allowed:
            continue
        src = f.read_text(encoding="utf-8")
        for pat, what in ((r"(?<![\w.])(alert|prompt|confirm)\(", "window dialog"), (r"createObjectURL", "manual download"),
                          (r"createElement\('dialog'\)", "hand-made <dialog>")):
            for m in re.finditer(pat, src):
                line = src.count("\n", 0, m.start()) + 1
                bad.append(f"{f.relative_to(js)}:{line} {what}")
    # Known exceptions: the Ctrl+K palette (entity.js) and the spend editor dialog (spend.js) are their own UX.
    bad = [b for b in bad if not (b.startswith("views/entity.js") or b.startswith("views/spend.js")) or "dialog" not in b]
    assert bad == [], "\n".join(bad)


def test_no_cdn_scripts_and_lazy_modules_exist():
    """Chart.js / lucide ship in frontend/vendor (pinned, works behind App Proxy without Internet);
    every file of a lazy module (routes.js MODULES) exists and is not also loaded eagerly."""
    import re
    root = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
    html = open(INDEX, encoding="utf-8").read()
    srcs = re.findall(r'<script src="([^"]+)"', html)
    assert not [s for s in srcs if s.startswith("http")], srcs
    for s in srcs:
        assert os.path.exists(os.path.join(root, s.split("?")[0])), s
    routes = open(os.path.join(root, "js", "routes.js"), encoding="utf-8").read()
    block = routes[routes.index("const MODULES = {"):routes.index("};", routes.index("const MODULES = {"))]
    lazy = re.findall(r"'([a-z_]+)'", block)
    assert "spend" in lazy and "bom" in lazy
    for name in lazy:
        assert os.path.exists(os.path.join(root, "js", "views", name + ".js")), name
        assert f"js/views/{name}.js" not in html, name


def test_chart_colours_come_from_tokens():
    """Chart colours are CSS tokens read by chartPalette() — no hex literals in the chart code."""
    import re
    js = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "js")
    for name in ("views/sizing.js", "views/sizing_result.js", "views/history.js", "views/backup.js", "views/executive.js", "views/capacity.js"):
        src = open(os.path.join(js, name), encoding="utf-8").read()
        assert not re.findall(r"'#[0-9a-fA-F]{6}'", src), name



def test_form_labels_and_file_pickers_follow_master_layout_rules():
    """MASTER §6 “Form, nhãn và độ dài chữ” (skill web-layout): a label holds the field name + unit only —
    “(tuỳ chọn)” goes to the placeholder — and the browser's native file picker ("Choose File", English,
    unstyled) is never shown: uiDropzone or a .btn wrapping a hidden input."""
    import pathlib
    import re
    front = pathlib.Path(__file__).resolve().parents[2] / "frontend"
    files = [front / "index.html", *sorted((front / "js").rglob("*.js"))]
    bad = []
    optional_in_label = re.compile(r"<(label|span)\b[^>]*>[^<]*\(tu[ỳỷ] chọn\)[^<]*</\1>")
    file_input = re.compile(r"<input\b[^>]*type=\"file\"[^>]*>")
    for f in files:
        src = f.read_text(encoding="utf-8")
        rel = f.relative_to(front)
        for m in optional_in_label.finditer(src):
            bad.append(f"{rel}:{src.count(chr(10), 0, m.start()) + 1} label with (tuỳ chọn): {m.group(0)[:60]}")
        for m in file_input.finditer(src):
            tag = m.group(0)
            if not re.search(r"\bhidden\b|class=\"[^\"]*\b(sr-only|hidden)\b", tag):
                bad.append(f"{rel}:{src.count(chr(10), 0, m.start()) + 1} visible native file input: {tag[:60]}")
    assert bad == [], "\n".join(bad)
