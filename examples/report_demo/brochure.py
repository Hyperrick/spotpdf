"""English brochure artwork for the reproducible diagnostic report example."""

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4


def draw_brochure(canvas):
    """Draw two pages of process-color text and vector artwork."""
    width, height = A4
    canvas.setTitle("NORD Coffee – Sierra Verde | fictional product brochure")
    canvas.setAuthor("spotpdf realistic preflight demo")
    cream = "#F5F1E7"
    green = "#163E32"
    muted = "#637165"
    gold = "#AA773D"

    def rect(x, y, w, h, color):
        canvas.setFillColor(HexColor(color))
        canvas.rect(x, y, w, h, stroke=0, fill=1)

    def text(x, y, value, size=12, font="Helvetica", color=green):
        canvas.setFillColor(HexColor(color))
        canvas.setFont(font, size)
        canvas.drawString(x, y, value)

    def lines(x, y, values, size=12, leading=18, font="Helvetica", color=green):
        for i, v in enumerate(values):
            text(x, y - i * leading, v, size, font, color)

    def rule(y):
        canvas.setStrokeColor(HexColor("#D2D6C9"))
        canvas.setLineWidth(0.7)
        canvas.line(48, y, width - 48, y)

    def frame(number):
        rect(0, 0, width, height, cream)
        text(48, height - 51, "NORD", 23, "Helvetica-Bold")
        text(128, height - 48, "COFFEE / ROASTERY", 8, "Helvetica-Bold")
        canvas.setStrokeColor(HexColor(green))
        canvas.setLineWidth(0.8)
        canvas.line(48, height - 68, width - 48, height - 68)
        text(48, 25, "Fictional print layout · spotpdf preflight demo", 8, color=muted)
        text(width - 98, 25, f"{number:02d} / 02", 8, color=muted)

    def bean(x, y, scale=1):
        canvas.saveState()
        canvas.translate(x, y)
        canvas.rotate(28)
        canvas.setFillColor(HexColor(green))
        canvas.ellipse(-7 * scale, -11 * scale, 7 * scale, 11 * scale, stroke=0, fill=1)
        canvas.setStrokeColor(HexColor(cream))
        canvas.setLineWidth(1.2 * scale)
        p = canvas.beginPath()
        p.moveTo(0, -8 * scale)
        p.curveTo(-5 * scale, 0, 5 * scale, 0, 0, 8 * scale)
        canvas.drawPath(p)
        canvas.restoreState()

    frame(1)
    text(48, 739, "SIERRA VERDE / SPECIAL LOT", 9, "Helvetica-Bold", gold)
    lines(46, 688, ["Great coffee.", "Clear origins."], 46, 51, "Helvetica-Bold")
    lines(
        49,
        600,
        ["Balanced coffee for slow mornings", "and the little pauses in between."],
        12,
        18,
        color=muted,
    )
    canvas.setFillColor(HexColor("#E7E5D3"))
    canvas.circle(288, 390, 164, stroke=0, fill=1)
    # Vector product illustration, with a realistic folded pouch and printed label.
    canvas.setFillColor(HexColor("#D4D3BF"))
    canvas.ellipse(173, 208, 422, 237, stroke=0, fill=1)
    p = canvas.beginPath()
    p.moveTo(192, 237)
    p.lineTo(181, 495)
    p.lineTo(203, 529)
    p.lineTo(388, 529)
    p.lineTo(410, 495)
    p.lineTo(399, 237)
    p.close()
    canvas.setFillColor(HexColor(green))
    canvas.drawPath(p, stroke=0, fill=1)
    rect(203, 515, 185, 13, "#295244")
    rect(198, 490, 195, 4, "#335E4D")
    canvas.setStrokeColor(HexColor("#426551"))
    canvas.setLineWidth(1)
    canvas.line(201, 249, 194, 488)
    canvas.line(388, 249, 398, 488)
    rect(217, 286, 161, 174, cream)
    text(235, 433, "NORD", 17, "Helvetica-Bold")
    text(235, 406, "SIERRA", 25, "Helvetica-Bold")
    text(235, 378, "VERDE", 25, "Helvetica-Bold")
    canvas.setStrokeColor(HexColor(gold))
    canvas.line(235, 363, 359, 363)
    text(235, 343, "COCOA · ORANGE · ALMOND", 7.5, "Helvetica-Bold")
    text(235, 324, "FILTER & ESPRESSO", 8)
    text(235, 305, "WHOLE BEAN / 250 g", 8)
    bean(142, 299, 1.25)
    bean(437, 277, 1.0)
    bean(453, 299, 0.72)
    # The fixture generator adds a spot-color image in this reserved region.
    text(430, 462, "GOLD FOIL FINISH", 7.5, "Helvetica-Bold", gold)
    text(48, 172, "FULL OF FLAVOUR. A MOMENT OF CALM.", 10, "Helvetica-Bold")
    rule(153)
    lines(
        48,
        128,
        [
            "Dark chocolate meets delicate citrus notes and a smooth finish.",
            "Our Sierra Verde edition brings balance to your cup – as a clean filter coffee",
            "or a rounded espresso with milk. Grind fresh, slow down and enjoy.",
        ],
        10.5,
        16,
    )
    canvas.showPage()
    frame(2)
    text(48, 739, "BREW / DISCOVER / ENJOY", 9, "Helvetica-Bold", gold)
    lines(47, 695, ["From the first aroma", "to the very last sip."], 35, 42, "Helvetica-Bold")
    lines(
        48,
        589,
        [
            "Great coffee does not need complicated rules. These three steps give you",
            "a starting point – let your own taste guide you from there.",
        ],
        11,
        17,
        color=muted,
    )
    for n, y, title, body in [
        (
            "01",
            523,
            "Grind fresh",
            [
                "Grind your beans just before brewing. Choose a medium grind for filter",
                "coffee and a finer setting for espresso.",
            ],
        ),
        (
            "02",
            424,
            "Find your balance",
            [
                "For filter coffee, start with 18 g of coffee to 300 ml of water. A scale helps",
                "you recreate your favourite recipe next time.",
            ],
        ),
        (
            "03",
            325,
            "Take your time",
            [
                "Let your coffee cool a little. Its aroma and flavour develop with every",
                "minute, revealing new nuances in your cup.",
            ],
        ),
    ]:
        text(48, y, n, 26, "Helvetica-Bold", gold)
        text(101, y + 4, title, 18, "Helvetica-Bold")
        lines(101, y - 21, body, 10, 16)
        rule(y - 52)
    rect(48, 75, 499, 161, green)
    text(70, 207, "YOUR MORNING. YOUR RECIPE.", 12, "Helvetica-Bold", cream)
    lines(
        70,
        181,
        [
            "Filter: 18 g / 300 ml / about 3 minutes",
            "Espresso: 18 g / 36 g / about 28 seconds",
            "These values are a starting point.",
            "Adjust them to suit your taste.",
        ],
        10,
        18,
        color=cream,
    )
    text(70, 100, "NORD / COFFEE WORTH YOUR TIME", 8, "Helvetica-Bold", "#C6D5BC")
    # Second spot-color seal occupies the right side of the recipe card.
    canvas.save()
