"""Visual morning-digest card (PNG) built from the same read-only evidence as the text digest.

Pillow is imported lazily and every failure is the caller's cue to send the plain text digest
instead, so a missing font or library can never stop the digest. Nothing here reads the
database or the network. Text is Bosnian like the existing digest and never infers a sale.
"""

from datetime import datetime, timedelta, timezone
from functools import lru_cache
import io
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
WIDTH = 1080
SCALE = 2
MARGIN = 48

BG = (13, 19, 28)
SURFACE = (22, 32, 46)
SURFACE_2 = (30, 43, 61)
TEXT = (232, 238, 245)
MUTED = (143, 163, 184)
GOOD = (74, 222, 128)
WARN = (251, 191, 36)
BAD = (248, 113, 113)
BLUE = (96, 165, 250)
MONTHS = ("jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec")
DAYS = ("pon", "uto", "sri", "čet", "pet", "sub", "ned")


def _utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _num(value):
    return f"{int(value or 0):,}".replace(",", ".")


def _money(value):
    return f"{int(round(value)):,} KM".replace(",", ".") if value is not None else "—"


def _short_date(value, tz):
    value = _utc(value)
    if value is None:
        return "nema zapisa"
    local = value.astimezone(tz)
    return f"{local.day}. {MONTHS[local.month - 1]}, {local.strftime('%H:%M')}"


def _day_label(value, tz):
    value = _utc(value)
    if value is None:
        return "?"
    local = value.astimezone(tz)
    return f"{local.day}.{local.month}."


def card_state(evidence):
    """Return (label, colour) for the overall collector state, mirroring the text digest."""
    run = evidence.get("run")
    if not run:
        return "NEMA PODATAKA", MUTED
    status = str(run.get("status") or "").lower()
    if status == "failed":
        return "NEUSPJELO", BAD
    if status == "completed" and int(run.get("errors") or 0) == 0:
        return "ČISTO", GOOD
    return "DJELIMIČNO", WARN


def _stale(evidence, now):
    clean = _utc(evidence.get("clean_at"))
    return clean is None or (now - clean) > timedelta(days=14)


class _Canvas:
    """Draws in design units on a supersampled image."""

    def __init__(self, height):
        from PIL import Image, ImageDraw

        self.image = Image.new("RGB", (WIDTH * SCALE, height * SCALE), BG)
        self.draw = ImageDraw.Draw(self.image)
        self.height = height

    @staticmethod
    @lru_cache(maxsize=32)
    def font(size, bold=False):
        from PIL import ImageFont

        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        return ImageFont.truetype(str(FONT_DIR / name), int(size * SCALE))

    def rect(self, x, y, w, h, fill, radius=18, outline=None):
        self.draw.rounded_rectangle(
            [x * SCALE, y * SCALE, (x + w) * SCALE, (y + h) * SCALE],
            radius=radius * SCALE, fill=fill, outline=outline)

    def text(self, x, y, value, size, fill=TEXT, bold=False, anchor="la"):
        self.draw.text((x * SCALE, y * SCALE), str(value), font=self.font(size, bold), fill=fill, anchor=anchor)

    def width(self, value, size, bold=False):
        return self.draw.textlength(str(value), font=self.font(size, bold)) / SCALE

    def fit(self, value, size, max_width, bold=False):
        value = str(value)
        if self.width(value, size, bold) <= max_width:
            return value
        while value and self.width(value + "…", size, bold) > max_width:
            value = value[:-1]
        return value.rstrip() + "…"

    def ellipse(self, cx, cy, r, fill):
        self.draw.ellipse([(cx - r) * SCALE, (cy - r) * SCALE, (cx + r) * SCALE, (cy + r) * SCALE], fill=fill)

    def line(self, x1, y1, x2, y2, fill, width=2):
        self.draw.line([x1 * SCALE, y1 * SCALE, x2 * SCALE, y2 * SCALE], fill=fill, width=width * SCALE)

    def png(self):
        from PIL import Image

        out = self.image.resize((WIDTH, self.height), Image.LANCZOS)
        buffer = io.BytesIO()
        out.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


def _section(c, y, title, height, note=None):
    c.rect(MARGIN, y, WIDTH - 2 * MARGIN, height, SURFACE)
    c.text(MARGIN + 28, y + 22, title, 32, bold=True)
    if note:
        c.text(WIDTH - MARGIN - 28, y + 28, note, 24, MUTED, anchor="ra")
    return y + 78


def _header(c, evidence, tz, now):
    local = now.astimezone(tz)
    label, colour = card_state(evidence)
    # simple garage mark
    c.rect(MARGIN, 44, 84, 84, SURFACE_2, radius=22)
    c.draw.polygon([(MARGIN * SCALE + 14 * SCALE, 86 * SCALE), (MARGIN * SCALE + 42 * SCALE, 62 * SCALE),
                    (MARGIN * SCALE + 70 * SCALE, 86 * SCALE)], fill=BLUE)
    c.rect(MARGIN + 18, 84, 48, 30, BLUE, radius=4)
    c.rect(MARGIN + 26, 96, 32, 18, SURFACE_2, radius=3)
    c.text(MARGIN + 108, 40, "Garaža", 58, bold=True)
    c.text(MARGIN + 108, 106, f"Pregled platforme · {DAYS[local.weekday()]}, {local.day}. "
                              f"{MONTHS[local.month - 1]} · {local.strftime('%H:%M')}", 28, MUTED)
    width = c.width(label, 30, True) + 70
    c.rect(WIDTH - MARGIN - width, 58, width, 58, tuple(int(v * 0.22 + SURFACE[i] * 0.78) for i, v in enumerate(colour)),
           radius=29)
    c.ellipse(WIDTH - MARGIN - width + 28, 87, 9, colour)
    c.text(WIDTH - MARGIN - width + 48, 87, label, 30, colour, True, anchor="lm")
    return 168


def _tiles(c, y, evidence, stale):
    run = evidence.get("run") or {}
    errors = int(run.get("errors") or 0)
    drops = None if stale else int(evidence.get("price_drops") or 0)
    tiles = [
        ("Zapažanja", _num(run.get("observations")), "zadnji prolaz", BLUE),
        ("Prvi put viđeno", _num(run.get("new_listings")), "novih ID-jeva", GOOD),
        ("Greške", _num(errors), "zadnji prolaz", BAD if errors else MUTED),
        ("Padovi cijene", "—" if drops is None else _num(drops), "zadnjih 7 dana", WARN if drops else MUTED),
    ]
    gap = 18
    width = (WIDTH - 2 * MARGIN - 3 * gap) / 4
    for index, (label, value, sub, colour) in enumerate(tiles):
        x = MARGIN + index * (width + gap)
        c.rect(x, y, width, 206, SURFACE)
        c.rect(x, y + 26, 8, 154, colour, radius=4)
        c.text(x + 28, y + 24, label, 25, MUTED)
        c.text(x + 28, y + 76, c.fit(value, 66, width - 44, True), 66, TEXT, True)
        c.text(x + 28, y + 160, c.fit(sub, 23, width - 44), 23, MUTED)
    return y + 206 + 28


RUNS_H = 452


def _runs_chart(c, y, evidence, tz):
    runs = list(reversed(evidence.get("recent_runs") or []))[-7:]
    top = _section(c, y, "Zadnji prolazi kolektora", RUNS_H)
    legend_x = WIDTH - MARGIN - 28
    for label, colour in (("novi ID-jevi", GOOD), ("zapažanja", BLUE)):
        legend_x -= c.width(label, 24)
        c.text(legend_x, y + 28, label, 24, MUTED)
        legend_x -= 26
        c.rect(legend_x, y + 34, 16, 16, colour, radius=4)
        legend_x -= 30
    if not runs:
        c.text(MARGIN + 28, top + 60, "Nema završenih prolaza u bazi.", 30, MUTED)
        return y + RUNS_H + 28
    inner_w = WIDTH - 2 * MARGIN - 56
    slot = inner_w / max(len(runs), 7)
    base_y = y + 332
    max_value = max([int(r.get("observations") or 0) for r in runs] + [1])
    plot_h = 190
    for index, item in enumerate(runs):
        cx = MARGIN + 28 + slot * index + slot / 2
        obs = int(item.get("observations") or 0)
        new = int(item.get("new_listings") or 0)
        status = str(item.get("status") or "").lower()
        colour = BAD if status == "failed" else (WARN if status != "completed" or int(item.get("errors") or 0) else GOOD)
        bar_w = slot * 0.30
        for offset, value, fill in ((-bar_w * 0.55, obs, BLUE), (bar_w * 0.55, new, GOOD)):
            height = max(4, plot_h * value / max_value) if value else 4
            c.rect(cx + offset - bar_w / 2, base_y - height, bar_w, height, fill, radius=6)
            if value:
                c.text(cx + offset, base_y - height - 8, _num(value), 22, TEXT, anchor="ms")
        c.text(cx, base_y + 16, _day_label(item.get("started_at"), tz), 23, MUTED, anchor="ma")
        c.ellipse(cx, base_y + 62, 8, colour)
    lx = MARGIN + 28
    for label, colour in (("čisto", GOOD), ("djelimično", WARN), ("neuspjelo", BAD)):
        c.ellipse(lx + 8, y + RUNS_H - 28, 8, colour)
        c.text(lx + 26, y + RUNS_H - 28, label, 22, MUTED, anchor="lm")
        lx += 26 + c.width(label, 22) + 34
    return y + RUNS_H + 28


def _price_block(c, y, evidence, stale):
    top = _section(c, y, "Tražene cijene · zadnjih 7 dana", 232, None if stale else f"uzorak {_num(evidence.get('price_sample'))} oglasa")
    if stale:
        c.text(MARGIN + 28, top + 4, "Trend potisnut: zadnji čist prolaz je star", 30, WARN, True)
        c.text(MARGIN + 28, top + 52, f"ili ga nema ({_short_date(evidence.get('clean_at'), c.tz)}). Brojevi nisu tržišni indeks.",
               26, MUTED)
        return y + 232 + 28
    drops = int(evidence.get("price_drops") or 0)
    rises = int(evidence.get("price_increases") or 0)
    biggest = max(drops, rises, 1)
    bar_x = MARGIN + 250
    bar_w = WIDTH - 2 * MARGIN - 250 - 130
    for index, (label, value, colour) in enumerate((("↓ Padovi", drops, GOOD), ("↑ Rastovi", rises, BAD))):
        row = top + index * 66
        c.text(MARGIN + 28, row + 12, label, 30, TEXT, True)
        c.rect(bar_x, row + 8, bar_w, 38, SURFACE_2, radius=19)
        if value:
            c.rect(bar_x, row + 8, max(38, bar_w * value / biggest), 38, colour, radius=19)
        c.text(WIDTH - MARGIN - 28, row + 10, _num(value), 34, TEXT, True, anchor="ra")
    return y + 232 + 28


def _model_mix(c, y, evidence):
    mix = (evidence.get("new_by_model") or [])[:5]
    height = 96 + max(1, len(mix)) * 58
    top = _section(c, y, "Novo u bazi po modelu", height, "prvi put viđeno")
    if not mix:
        c.text(MARGIN + 28, top + 4, "Nema novih ID-jeva u ovom prolazu.", 28, MUTED)
        return y + height + 28
    biggest = max(int(item.get("n") or 0) for item in mix) or 1
    label_w = 390
    bar_x = MARGIN + 28 + label_w
    bar_w = WIDTH - MARGIN - 28 - bar_x - 90
    for index, item in enumerate(mix):
        row = top + index * 58
        c.text(MARGIN + 28, row + 6, c.fit(item.get("label") or "Nepoznat model", 29, label_w - 16), 29)
        c.rect(bar_x, row + 8, bar_w, 30, SURFACE_2, radius=15)
        n = int(item.get("n") or 0)
        c.rect(bar_x, row + 8, max(30, bar_w * n / biggest), 30, BLUE, radius=15)
        c.text(WIDTH - MARGIN - 28, row + 6, _num(n), 30, TEXT, True, anchor="ra")
    return y + height + 28


def _cuts(c, y, evidence, tz):
    cuts = (evidence.get("notable_cuts") or [])[:3]
    height = 96 + max(1, len(cuts)) * 92
    top = _section(c, y, "Najveći padovi tražene cijene", height)
    if not cuts:
        c.text(MARGIN + 28, top + 4, "Nema zabilježenih padova u ovom prozoru.", 28, MUTED)
        return y + height + 28
    for index, item in enumerate(cuts):
        row = top + index * 92
        base = " ".join(p for p in (item.get("make"), item.get("model")) if p) or "Nepoznat model"
        year = f" · {item['year']}" if item.get("year") else ""
        name = c.fit(base, 30, 610 - c.width(year, 30, True), True) + year
        now_price, before = item.get("asking_price"), item.get("previous_price")
        pct = round((before - now_price) * 100 / before) if before else 0
        c.text(MARGIN + 28, row + 2, name, 30, bold=True)
        c.text(MARGIN + 28, row + 44, f"{_money(now_price)}  ←  {_money(before)}   ·   zapaženo {_short_date(item.get('observed_at'), tz)}",
               24, MUTED)
        c.rect(WIDTH - MARGIN - 28 - 150, row + 8, 150, 52, (26, 66, 48), radius=26)
        c.text(WIDTH - MARGIN - 28 - 75, row + 34, f"−{pct}%", 30, GOOD, True, anchor="mm")
    return y + height + 28


def _footer(c, y, evidence, tz, stale):
    c.rect(MARGIN, y, WIDTH - 2 * MARGIN, 214, SURFACE)
    chips = [
        ("Zadnje zapažanje", _short_date(evidence.get("latest_snapshot"), tz), TEXT),
        ("Zadnji čist prolaz", _short_date(evidence.get("clean_at"), tz) + ("  (staro)" if stale else ""),
         WARN if stale else TEXT),
    ]
    for index, (label, value, colour) in enumerate(chips):
        x = MARGIN + 28 + index * ((WIDTH - 2 * MARGIN - 56) / 2)
        c.text(x, y + 22, label, 24, MUTED)
        c.text(x, y + 56, c.fit(value, 30, 440, True), 30, colour, True)
    c.line(MARGIN + 28, y + 118, WIDTH - MARGIN - 28, y + 118, SURFACE_2, 2)
    c.text(MARGIN + 28, y + 134, "Nestanak iz pretrage nije prodaja. Tražene cijene nisu prodajne cijene.", 23, MUTED)
    c.text(MARGIN + 28, y + 168, "Prvo viđeno kod nas nije provjeren datum objave.", 23, MUTED)
    return y + 214


def render_card(evidence, tz):
    """Return the digest card as PNG bytes. Raises on any problem so the caller can fall back."""
    now = _utc(evidence.get("generated_at")) or datetime.now(timezone.utc)
    stale = _stale(evidence, now)
    # height depends on the number of rows
    mix = min(5, len(evidence.get("new_by_model") or [])) or 1
    cuts = min(3, len(evidence.get("notable_cuts") or [])) or 1
    height = 168 + 206 + 28 + RUNS_H + 28 + 232 + 28 + (96 + mix * 58) + 28 + (96 + cuts * 92) + 28 + 214 + 48
    canvas = _Canvas(height)
    canvas.tz = tz
    y = _header(canvas, evidence, tz, now)
    y = _tiles(canvas, y, evidence, stale)
    y = _runs_chart(canvas, y, evidence, tz)
    y = _price_block(canvas, y, evidence, stale)
    y = _model_mix(canvas, y, evidence)
    y = _cuts(canvas, y, evidence, tz)
    _footer(canvas, y, evidence, tz, stale)
    return canvas.png()
