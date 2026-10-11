"""Shared helpers for the documentation PDFs (workflow and user manual).

Needs ReportLab and Pillow, which are build-time tools only and are not part of
requirements.txt:    pip install reportlab pillow

Used by build_architecture_pdf.py and build_user_manual_pdf.py.
"""
import math
import os

from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Flowable, Frame, HRFlowable, KeepTogether, ListFlowable, ListItem,
    PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO = os.path.join(ROOT, "app", "static", "img", "ananta_logo_header.png")

# ---------------------------------------------------------------- palette
NAVY = HexColor("#1B3A4B")
NAVY_DK = HexColor("#10222D")
TEAL = HexColor("#0E6B66")
GOLD = HexColor("#B0893E")
GOLD_LT = HexColor("#F3E7C9")
INK = HexColor("#1F2933")
MUTED = HexColor("#52606D")
LINE = HexColor("#D9E2EC")
BAND = HexColor("#F4F7F8")
ROW = HexColor("#F8FBFC")
RED = HexColor("#B23A3A")
GREEN = HexColor("#2E7D4F")
AMBER = HexColor("#B9791C")

# status chip colours taken from app/static/css/style.css
CHIP = {
    "Valid": ("#E6F4EA", "#2E7D4F"),
    "Expiring in 90 Days": ("#EEF6E9", "#5B8A2F"),
    "Expiring in 60 Days": ("#FDF3DD", "#B9791C"),
    "Expiring in 30 Days": ("#FCE9DC", "#C1621C"),
    "Expiring in 7 Days": ("#FBE3EA", "#B23A6B"),
    "Expired": ("#FBE9E7", "#B23A3A"),
}

# ---------------------------------------------------------------- fonts
FONT_DIR = r"C:\Windows\Fonts"
SANS = SANS_B = SANS_I = SANS_BI = "Helvetica"
MONO = "Courier"
TTF_OK = False


def register_fonts():
    """Embed Segoe UI (and Consolas) when present; fall back to Helvetica."""
    global SANS, SANS_B, SANS_I, SANS_BI, MONO, TTF_OK
    try:
        for name, fn in (("UI", "segoeui.ttf"), ("UI-B", "segoeuib.ttf"),
                         ("UI-I", "segoeuii.ttf"), ("UI-BI", "segoeuiz.ttf")):
            pdfmetrics.registerFont(TTFont(name, os.path.join(FONT_DIR, fn)))
        pdfmetrics.registerFontFamily("UI", normal="UI", bold="UI-B", italic="UI-I", boldItalic="UI-BI")
        SANS, SANS_B, SANS_I, SANS_BI = "UI", "UI-B", "UI-I", "UI-BI"
        TTF_OK = True
    except Exception:
        pass
    try:
        pdfmetrics.registerFont(TTFont("MONO", os.path.join(FONT_DIR, "consola.ttf")))
        MONO = "MONO"
    except Exception:
        pass


register_fonts()
ARROW = "\u2192" if TTF_OK else "->"
BULLET = "\u2022" if TTF_OK else "-"


# ---------------------------------------------------------------- styles
def build_styles(body_size=9.6):
    S = {}

    def add(name, **kw):
        kw.setdefault("fontName", SANS)
        kw.setdefault("textColor", INK)
        kw.setdefault("alignment", TA_LEFT)
        S[name] = ParagraphStyle(name, **kw)

    lead = body_size * 1.42
    add("Body", fontSize=body_size, leading=lead, spaceAfter=6)
    add("BodyTight", fontSize=body_size, leading=lead, spaceAfter=2)
    add("Lead", fontSize=body_size + 1.6, leading=(body_size + 1.6) * 1.45, textColor=MUTED, spaceAfter=8)
    add("H1", fontName=SANS_B, fontSize=17, leading=21, textColor=NAVY, spaceBefore=6, spaceAfter=4, keepWithNext=1)
    add("H2", fontName=SANS_B, fontSize=12.5, leading=16, textColor=TEAL, spaceBefore=11, spaceAfter=4, keepWithNext=1)
    add("H3", fontName=SANS_B, fontSize=10.4, leading=14, textColor=NAVY, spaceBefore=7, spaceAfter=2, keepWithNext=1)
    add("Bullet", fontSize=body_size, leading=lead)
    add("Small", fontSize=8.4, leading=11.4, textColor=MUTED)
    add("Caption", fontName=SANS_I, fontSize=8.4, leading=11, textColor=MUTED, spaceBefore=3, spaceAfter=9)
    add("Th", fontName=SANS_B, fontSize=8.5, leading=11, textColor=white)
    add("Td", fontSize=8.6, leading=11.4)
    add("TdB", fontName=SANS_B, fontSize=8.6, leading=11.4)
    add("TdMono", fontName=MONO, fontSize=8.2, leading=11)
    add("Mono", fontName=MONO, fontSize=8.6, leading=12, backColor=BAND, borderPadding=(4, 5, 4, 5),
        spaceBefore=3, spaceAfter=9, leftIndent=4)
    add("TocH1", fontName=SANS_B, fontSize=10, leading=13, textColor=NAVY, leftIndent=0, spaceBefore=3.5)
    add("TocH2", fontSize=8.8, leading=11.4, textColor=MUTED, leftIndent=14)
    add("H1Plain", fontName=SANS_B, fontSize=17, leading=21, textColor=NAVY, spaceBefore=6, spaceAfter=4)
    add("CalloutTitle", fontName=SANS_B, fontSize=9.2, leading=12, textColor=NAVY)
    add("CalloutBody", fontSize=body_size - 0.4, leading=(body_size - 0.4) * 1.4)
    return S


# ---------------------------------------------------------------- flowables
def hrule(color=GOLD, thickness=1, before=0, after=7):
    rule = HRFlowable(width="100%", thickness=thickness, color=color, spaceBefore=before, spaceAfter=after)
    rule.keepWithNext = 1
    return rule


def bullets(S, items, style="Bullet", indent=14, after=7):
    flow = [ListItem(Paragraph(t, S[style]), leftIndent=indent, bulletColor=TEAL) for t in items]
    return ListFlowable(flow, bulletType="bullet", start=BULLET, leftIndent=indent + 2,
                        bulletFontName=SANS, bulletFontSize=9, spaceBefore=1, spaceAfter=after)


def data_table(S, headers, rows, col_widths, zebra=True, first_bold=False, mono_cols=()):
    head = [Paragraph(h, S["Th"]) for h in headers]
    body = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            if isinstance(cell, Flowable):
                cells.append(cell)
                continue
            style = "TdMono" if i in mono_cols else ("TdB" if (first_bold and i == 0) else "Td")
            cells.append(Paragraph(str(cell), S[style]))
        body.append(cells)
    tbl = Table([head] + body, colWidths=col_widths, repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE),
        ("BOX", (0, 0), (-1, -1), 0.4, LINE),
    ]
    if zebra:
        for i in range(1, len(body) + 1):
            if i % 2 == 0:
                cmds.append(("BACKGROUND", (0, i), (-1, i), ROW))
    tbl.setStyle(TableStyle(cmds))
    tbl.spaceAfter = 9
    return tbl


CALLOUT_KINDS = {
    "tip": ("Tip", HexColor("#E8F3F2"), TEAL),
    "note": ("Note", HexColor("#EEF2F7"), NAVY),
    "important": ("Important", HexColor("#FBEFD9"), AMBER),
    "warning": ("Careful", HexColor("#FBE9E7"), RED),
    "admin": ("Administrators", HexColor("#F1EBF7"), HexColor("#6B4A8F")),
}


class Stack(Flowable):
    """Stacks flowables vertically and never splits.

    Unlike KeepTogether it is not a container, so a heading marked keepWithNext
    stays attached to it (ReportLab refuses to bind a heading to a container).
    """

    def __init__(self, parts, gap=3):
        Flowable.__init__(self)
        self.parts, self.gap = parts, gap
        self._sizes = []

    def wrap(self, aw, ah):
        self._sizes = []
        total = 0
        for part in self.parts:
            w, h = part.wrap(aw, ah)
            self._sizes.append((w, h))
            total += h + self.gap
        self.width, self.height = aw, total
        return aw, total

    def split(self, aw, ah):
        return []

    def draw(self):
        y = self.height
        for part, (w, h) in zip(self.parts, self._sizes):
            y -= h
            part.drawOn(self.canv, 0, y, _sW=self.width - w)
            y -= self.gap


def callout(S, kind, text, title=None, width=None, keep=True):
    label, bg, accent = CALLOUT_KINDS[kind]
    head = Paragraph(f"<font color='{accent.hexval().replace('0x', '#')}'>{title or label}</font>", S["CalloutTitle"])
    body = Paragraph(text, S["CalloutBody"])
    t = Table([[head], [body]], colWidths=[width] if width else None)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (0, 0), 5),
        ("BOTTOMPADDING", (0, 0), (0, 0), 0),
        ("TOPPADDING", (0, 1), (0, 1), 1),
        ("BOTTOMPADDING", (0, 1), (0, 1), 6),
    ]))
    return Stack([t, Spacer(1, 7)]) if keep else t


class NumCircle(Flowable):
    def __init__(self, n, d=6.2 * mm, fill=TEAL):
        Flowable.__init__(self)
        self.n, self.d, self.fill = n, d, fill
        self.width = self.height = d

    def draw(self):
        c = self.canv
        c.setFillColor(self.fill)
        c.circle(self.d / 2, self.d / 2, self.d / 2, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont(SANS_B, 8.4)
        c.drawCentredString(self.d / 2, self.d / 2 - 3, str(self.n))


def steps(S, items, width):
    """Numbered how-to steps. Each item is an HTML string."""
    rows = []
    for i, text in enumerate(items, 1):
        rows.append([NumCircle(i), Paragraph(text, S["BodyTight"])])
    t = Table(rows, colWidths=[9 * mm, width - 9 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.2),
    ]))
    return t


class Chip(Flowable):
    """A status label drawn like the one in the application."""

    def __init__(self, status, font_size=8):
        Flowable.__init__(self)
        self.status, self.fs = status, font_size
        bg, fg = CHIP[status]
        self.bg, self.fg = HexColor(bg), HexColor(fg)
        self.width = stringWidth(status, SANS_B, font_size) + 12
        self.height = font_size + 8

    def draw(self):
        c = self.canv
        c.setFillColor(self.bg)
        c.roundRect(0, 0, self.width, self.height, self.height / 2, fill=1, stroke=0)
        c.setFillColor(self.fg)
        c.setFont(SANS_B, self.fs)
        c.drawString(6, 4.4, self.status)


class RolePill(Flowable):
    def __init__(self, text, fill=NAVY, font_size=7.6):
        Flowable.__init__(self)
        self.text, self.fill, self.fs = text, fill, font_size
        self.width = stringWidth(text, SANS_B, font_size) + 10
        self.height = font_size + 6

    def draw(self):
        c = self.canv
        c.setFillColor(self.fill)
        c.roundRect(0, 0, self.width, self.height, 3, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont(SANS_B, self.fs)
        c.drawString(5, 3.6, self.text)


class Screenshot(Flowable):
    """A screenshot scaled to width, optionally cropped, with numbered markers.

    crop     (x0, y0, x1, y1) in source pixels
    markers  [(x, y, n)] in SOURCE pixel coordinates (before cropping)
    """

    def __init__(self, path, width, crop=None, markers=None, max_height=None):
        from PIL import Image
        Flowable.__init__(self)
        im = Image.open(path).convert("RGB")
        self.src_w, self.src_h = im.size
        self.x0 = self.y0 = 0
        if crop:
            x0, y0, x1, y1 = crop
            x1 = min(x1, self.src_w)
            y1 = min(y1, self.src_h)
            im = im.crop((x0, y0, x1, y1))
            self.x0, self.y0 = x0, y0
        self.im = im
        self.hAlign = "CENTER"
        self.markers = markers or []
        w, h = im.size
        scale = width / float(w)
        if max_height and h * scale > max_height:
            scale = max_height / float(h)
        self.scale = scale
        self.width = w * scale
        self.height = h * scale

    def wrap(self, aw, ah):
        return self.width, self.height + 2

    def draw(self):
        from reportlab.lib.utils import ImageReader
        c = self.canv
        c.setStrokeColor(LINE)
        c.setLineWidth(0.8)
        c.setFillColor(white)
        c.rect(-0.5, 0.5, self.width + 1, self.height + 1, fill=1, stroke=1)
        c.drawImage(ImageReader(self.im), 0, 1, self.width, self.height)
        r = 2.9 * mm
        for x, y, n in self.markers:
            px = (x - self.x0) * self.scale
            py = self.height + 1 - (y - self.y0) * self.scale
            c.setFillColor(white)
            c.circle(px, py, r + 1.1, fill=1, stroke=0)
            c.setFillColor(GOLD)
            c.circle(px, py, r, fill=1, stroke=0)
            c.setFillColor(NAVY_DK)
            c.setFont(SANS_B, 8.6)
            c.drawCentredString(px, py - 3, str(n))


def figure(S, shot, caption=None, legend=None, width=None):
    """Screenshot + caption + optional marker legend, kept on one page."""
    parts = [shot]
    if caption:
        centred = ParagraphStyle("CaptionC", parent=S["Caption"], alignment=TA_CENTER)
        parts.append(Paragraph(caption, centred))
    if legend:
        rows = []
        for n, text in legend:
            rows.append([NumCircle(n, d=5 * mm, fill=GOLD), Paragraph(text, S["BodyTight"])])
        t = Table(rows, colWidths=[8 * mm, (width or 170 * mm) - 8 * mm])
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ]))
        t.hAlign = "LEFT"
        parts.append(t)
        parts.append(Spacer(1, 8))
    return Stack(parts, gap=1)


# ---------------------------------------------------------------- diagrams
def wrap_text(text, font, size, max_width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if stringWidth(trial, font, size) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_arrow(c, x1, y1, x2, y2, color=GOLD, width=1.3, head=2.3 * mm):
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.line(x1, y1, x2, y2)
    ang = math.atan2(y2 - y1, x2 - x1)
    for da in (28, -28):
        c.line(x2, y2, x2 - head * math.cos(ang - math.radians(da)), y2 - head * math.sin(ang - math.radians(da)))


def draw_box(c, x, y, w, h, title, body=None, fill=white, stroke=TEAL, title_color=NAVY,
             tsize=8.6, bsize=7.2, number=None, body_lines=3):
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(1.1)
    c.roundRect(x, y, w, h, 2.4 * mm, fill=1, stroke=1)
    cx = x + w / 2
    pad = 3.5 * mm
    if number is not None:
        r = 3.1 * mm
        bx, by = x + r + 2 * mm, y + h - r - 2 * mm
        c.setFillColor(stroke)
        c.circle(bx, by, r, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont(SANS_B, 7.4)
        c.drawCentredString(bx, by - 2.5, str(number))
        pad = 2 * mm + 2 * r + 1.5 * mm
    title_lines = wrap_text(title, SANS_B, tsize, w - pad - 3 * mm)
    ty = y + h - 5.2 * mm
    c.setFillColor(title_color)
    c.setFont(SANS_B, tsize)
    for line in title_lines[:2]:
        if number is not None:
            c.drawString(x + pad, ty, line)
        else:
            c.drawCentredString(cx, ty, line)
        ty -= tsize + 1.6
    if body:
        c.setFillColor(MUTED)
        c.setFont(SANS, bsize)
        ty -= 0.6 * mm
        if number is not None:
            for line in wrap_text(body, SANS, bsize, w - pad - 3 * mm)[:body_lines]:
                c.drawString(x + pad, ty, line)
                ty -= bsize + 1.7
        else:
            for line in wrap_text(body, SANS, bsize, w - 6 * mm)[:body_lines]:
                c.drawCentredString(cx, ty, line)
                ty -= bsize + 1.7


class FlowDiagram(Flowable):
    """Numbered boxes joined by arrows, top to bottom. A row is a single box or a
    {"split": [step, step]} pair that forks and rejoins."""

    def __init__(self, rows, width, box_h=14.5 * mm, gap=5.5 * mm):
        Flowable.__init__(self)
        self.rows, self.width, self.box_h, self.gap = rows, width, box_h, gap
        self.height = len(rows) * box_h + (len(rows) - 1) * gap + 2 * mm

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c = self.canv
        cx = self.width / 2
        y = self.height - 1 * mm
        prev = None
        n = 1
        for row in self.rows:
            top, bot = y, y - self.box_h
            if "split" in row:
                g = 6 * mm
                w = (self.width - g) / 2
                pts = []
                for xoff, step in zip((0, w + g), row["split"]):
                    cxb = xoff + w / 2
                    if prev:
                        for px, py in prev:
                            draw_arrow(c, px, py, cxb, top)
                    draw_box(c, xoff, bot, w, self.box_h, step["title"], step["body"], number=n)
                    pts.append((cxb, bot))
                    n += 1
                prev = pts
            else:
                if prev:
                    for px, py in prev:
                        draw_arrow(c, px, py, cx, top)
                draw_box(c, 0, bot, self.width, self.box_h, row["title"], row["body"], number=n)
                prev = [(cx, bot)]
                n += 1
            y -= self.box_h + self.gap


class CanvasFigure(Flowable):
    """Run any drawing function(canvas, width, height) as a flowable."""

    def __init__(self, width, height, fn):
        Flowable.__init__(self)
        self.width, self.height, self.fn = width, height, fn

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        self.fn(self.canv, self.width, self.height)


# ---------------------------------------------------------------- document
class KitDoc(BaseDocTemplate):
    """Builds a document with a table of contents and PDF bookmarks."""

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            name = flowable.style.name
            if name in ("H1", "H2"):
                level = 0 if name == "H1" else 1
                text = flowable.getPlainText()
                slug = "".join(ch if ch.isalnum() else "-" for ch in text.lower())[:48]
                key = "h%d-%d-%s" % (level, self.page, slug)  # deterministic, so the TOC converges
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, level)
                self.notify("TOCEntry", (level, text, self.page, key))


def make_toc(S):
    toc = TableOfContents()
    toc.levelStyles = [S["TocH1"], S["TocH2"]]
    toc.dotsMinLevel = 0
    return toc


def band_header_footer(title_left, title_right, footer_left):
    def _draw(canvas, doc):
        canvas.saveState()
        W, H = A4
        canvas.setFillColor(NAVY)
        canvas.rect(0, H - 12 * mm, W, 12 * mm, fill=1, stroke=0)
        canvas.setFillColor(GOLD)
        canvas.rect(0, H - 13.2 * mm, W, 1.2 * mm, fill=1, stroke=0)
        canvas.setFillColor(white)
        canvas.setFont(SANS_B, 9)
        canvas.drawString(16 * mm, H - 8 * mm, title_left)
        canvas.setFont(SANS, 8)
        canvas.drawRightString(W - 16 * mm, H - 8 * mm, title_right)
        canvas.setFillColor(NAVY)
        canvas.rect(0, 0, W, 10 * mm, fill=1, stroke=0)
        canvas.setFillColor(white)
        canvas.setFont(SANS, 8)
        canvas.drawString(16 * mm, 3.6 * mm, footer_left)
        canvas.drawRightString(W - 16 * mm, 3.6 * mm, "Page %d" % doc.page)
        canvas.restoreState()
    return _draw


def cover_page_fn(kicker, title, subtitle, tagline_lines, meta_lines):
    def _draw(canvas, doc):
        W, H = A4
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, 0, W, H, fill=1, stroke=0)
        # logo on a white tile
        if os.path.exists(LOGO):
            canvas.setFillColor(white)
            canvas.roundRect(16 * mm, H - 40 * mm, 70 * mm, 20 * mm, 3 * mm, fill=1, stroke=0)
            try:
                canvas.drawImage(LOGO, 20 * mm, H - 37 * mm, 62 * mm, 14 * mm,
                                 preserveAspectRatio=True, mask="auto", anchor="c")
            except Exception:
                pass
        canvas.setFillColor(GOLD)
        canvas.rect(0, 92 * mm, W, 2.2 * mm, fill=1, stroke=0)
        canvas.setFillColor(HexColor("#D9B76A"))
        canvas.setFont(SANS_B, 10.5)
        canvas.drawString(18 * mm, 168 * mm, kicker.upper())
        canvas.setFillColor(white)
        canvas.setFont(SANS_B, 32)
        y = 154 * mm
        for line in title.split("\n"):
            canvas.drawString(18 * mm, y, line)
            y -= 13 * mm
        canvas.setFont(SANS, 14)
        canvas.setFillColor(HexColor("#E6EDF2"))
        canvas.drawString(18 * mm, y - 2 * mm, subtitle)
        canvas.setFillColor(HexColor("#B8C7D3"))
        canvas.setFont(SANS_I, 11)
        ty = 104 * mm
        for line in tagline_lines:
            canvas.drawString(18 * mm, ty, line)
            ty -= 6 * mm
        canvas.setFillColor(HexColor("#D9B76A"))
        canvas.setFont(SANS, 9.6)
        my = 74 * mm
        for label, value in meta_lines:
            canvas.setFont(SANS_B, 9.6)
            canvas.drawString(18 * mm, my, label)
            canvas.setFont(SANS, 9.6)
            canvas.drawString(52 * mm, my, value)
            my -= 6 * mm
        canvas.restoreState()
    return _draw


def new_document(out_path, title, author, cover_fn, body_fn):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    doc = KitDoc(out_path, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                 topMargin=19 * mm, bottomMargin=16 * mm, title=title, author=author)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[Frame(0, 0, A4[0], A4[1], id="cover")], onPage=cover_fn),
        PageTemplate(id="body", frames=[frame], onPage=body_fn),
    ])
    return doc


