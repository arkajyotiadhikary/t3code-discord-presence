#!/usr/bin/env python3
"""Draws the Discord art assets in the T3 Code style. Needs Pillow: pip install pillow"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
SIZE = 1024
SCALE = 4
S = SIZE * SCALE

BG = "#000000"
CARD = "#0A0A0A"
FG = "#F5F5F5"
RING_IDLE = "#525252"
ACCENT = "#6193FF"

LOGOS = OUT / "logos"

AGENTS = {
    "agent_claude": "claude",
    "agent_codex": "codex",
    "agent_cursor": "cursor",
    "agent_opencode": "opencode",
    "agent_grok": "grok",
    "agent_antigravity": "antigravity",
    "agent_kiro": "kiro",
    "agent_muse": "muse",
    "agent_pi": "pi",
    "agent_fx": "fx",
    "agent_acp": "acp",
    "agent_other": "A",
}
LOGO_SCALE = 0.5

FONT_PATHS = (
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "DejaVuSans-Bold.ttf",
)


def px(v):
    return int(v * S)


def bold_font(size):
    for path in FONT_PATHS:
        try:
            f = ImageFont.truetype(path, size)
        except OSError:
            continue
        try:
            f.set_variation_by_name("Bold")
        except (OSError, ValueError):
            if path.endswith("HelveticaNeue.ttc"):
                f = ImageFont.truetype(path, size, index=1)
        return f
    return ImageFont.load_default(size)


def centered_text(d, text, font, fill):
    left, top, right, bottom = d.textbbox((0, 0), text, font=font)
    d.text(((S - (right - left)) / 2 - left, (S - (bottom - top)) / 2 - top), text, font=font, fill=fill)


def badge_base(ring_color):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, S, S], fill=ring_color)
    inset = px(0.075)
    d.ellipse([inset, inset, S - inset, S - inset], fill=CARD)
    return img, d


def draw_agent(mark):
    img, d = badge_base(ACCENT)
    logo_path = LOGOS / f"{mark}.png"
    if logo_path.exists():
        side = px(LOGO_SCALE)
        logo = Image.open(logo_path).convert("RGBA").resize((side, side), Image.LANCZOS)
        img.alpha_composite(logo, ((S - side) // 2, (S - side) // 2))
    else:
        centered_text(d, mark, bold_font(px(0.52)), FG)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def draw_idle():
    img, d = badge_base(RING_IDLE)
    w, h, gap = px(0.085), px(0.32), px(0.075)
    top = (S - h) // 2
    for x in (S // 2 - gap - w, S // 2 + gap):
        d.rounded_rectangle([x, top, x + w, top + h], radius=px(0.02), fill="#A3A3A3")
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def draw_terminal():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S, S], radius=px(0.2), fill=BG)
    d.rounded_rectangle([px(0.1), px(0.1), px(0.9), px(0.9)], radius=px(0.12), fill=CARD, outline="#242424", width=px(0.01))
    w = px(0.065)
    d.line([px(0.27), px(0.38), px(0.44), px(0.5), px(0.27), px(0.62)], fill=ACCENT, width=w, joint="curve")
    d.rounded_rectangle([px(0.5), px(0.585), px(0.73), px(0.645)], radius=px(0.02), fill=FG)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main():
    draw_terminal().save(OUT / "terminal.png")
    for name, mark in AGENTS.items():
        draw_agent(mark).save(OUT / f"{name}.png")
    draw_idle().save(OUT / "agent_idle.png")
    print("Wrote", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
