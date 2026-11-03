#!/usr/bin/env python3
"""Draws the Discord art assets. Needs Pillow: pip install pillow"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
SIZE = 1024
SCALE = 2
S = SIZE * SCALE

AGENTS = {
    "agent_claude": {"bg": "#F4A261", "head": "#FFF4E6", "accent": "#E76F51", "mood": "happy"},
    "agent_codex": {"bg": "#2A9D8F", "head": "#E9FFF9", "accent": "#1D6F65", "mood": "focused"},
    "agent_other": {"bg": "#8E7CC3", "head": "#F3EFFF", "accent": "#5E4B9C", "mood": "happy"},
    "agent_idle": {"bg": "#8D99AE", "head": "#EDF2F4", "accent": "#5C677D", "mood": "sleepy"},
}

INK = "#2B2D42"


def px(v):
    return int(v * S)


def font(size):
    for name in ("/System/Library/Fonts/SFNSMono.ttf", "/System/Library/Fonts/Menlo.ttc", "DejaVuSansMono-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def draw_robot(spec):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    line = px(0.022)

    d.ellipse([0, 0, S, S], fill=spec["bg"])

    d.line([px(0.5), px(0.27), px(0.5), px(0.16)], fill=INK, width=line)
    d.ellipse([px(0.455), px(0.11), px(0.545), px(0.2)], fill=spec["accent"], outline=INK, width=line)

    d.rounded_rectangle([px(0.17), px(0.42), px(0.25), px(0.6)], radius=px(0.03), fill=spec["accent"], outline=INK, width=line)
    d.rounded_rectangle([px(0.75), px(0.42), px(0.83), px(0.6)], radius=px(0.03), fill=spec["accent"], outline=INK, width=line)

    d.rounded_rectangle([px(0.22), px(0.27), px(0.78), px(0.76)], radius=px(0.14), fill=spec["head"], outline=INK, width=line)

    d.ellipse([px(0.27), px(0.56), px(0.35), px(0.62)], fill=spec["bg"])
    d.ellipse([px(0.65), px(0.56), px(0.73), px(0.62)], fill=spec["bg"])

    mood = spec["mood"]
    if mood == "sleepy":
        for cx in (0.38, 0.62):
            d.arc([px(cx - 0.065), px(0.43), px(cx + 0.065), px(0.53)], 20, 160, fill=INK, width=line)
        d.ellipse([px(0.47), px(0.63), px(0.53), px(0.68)], outline=INK, width=line)
        z = font(px(0.09))
        d.text((px(0.7), px(0.17)), "z", font=z, fill=INK)
        d.text((px(0.79), px(0.08)), "z", font=font(px(0.12)), fill=INK)
    else:
        for cx in (0.38, 0.62):
            d.ellipse([px(cx - 0.06), px(0.41), px(cx + 0.06), px(0.53)], fill=INK)
            d.ellipse([px(cx - 0.015), px(0.425), px(cx + 0.025), px(0.465)], fill="white")
        if mood == "focused":
            d.line([px(0.31), px(0.37), px(0.45), px(0.39)], fill=INK, width=line)
            d.line([px(0.55), px(0.39), px(0.69), px(0.37)], fill=INK, width=line)
            d.line([px(0.43), px(0.65), px(0.57), px(0.65)], fill=INK, width=line)
        else:
            d.arc([px(0.4), px(0.56), px(0.6), px(0.69)], 20, 160, fill=INK, width=line)

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def draw_large():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S, S], radius=px(0.22), fill="#14161F")
    d.rounded_rectangle([px(0.1), px(0.1), px(0.9), px(0.9)], radius=px(0.14), fill="#1F2230", outline="#3A3F55", width=px(0.012))
    for i, color in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        x = 0.19 + i * 0.07
        d.ellipse([px(x - 0.022), px(0.18), px(x + 0.022), px(0.224)], fill=color)
    w = px(0.06)
    d.line([px(0.25), px(0.4), px(0.42), px(0.53), px(0.25), px(0.66)], fill="#F4A261", width=w, joint="curve")
    d.rounded_rectangle([px(0.48), px(0.62), px(0.74), px(0.68)], radius=px(0.02), fill="#EDF2F4")
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main():
    draw_large().save(OUT / "terminal.png")
    for name, spec in AGENTS.items():
        draw_robot(spec).save(OUT / f"{name}.png")
    print("Wrote", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
