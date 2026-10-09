"""Спрайты мухи, версия 5: вид строго сверху, крылья — широкие лопасти в плоскости картинки.

Оси: тело горизонтально, голова справа. Крыло строится по вектору направления в плоскости экрана:
сложенное (вдоль брюшка) ~14°, раскрытое (полёт) ~45–65°.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

S = 4
SIZE = 64

ABDOMEN = (48, 51, 64, 255)
ABDOMEN_DARK = (36, 38, 48, 255)
THORAX = (66, 70, 86, 255)
THORAX_HL = (98, 103, 124, 150)
HEAD = (40, 42, 54, 255)
EYE = (152, 40, 50, 255)
EYE_DARK = (104, 24, 32, 255)
EYE_HL = (250, 220, 220, 210)
WING_FILL = (222, 240, 255, 140)
WING_EDGE = (146, 190, 232, 205)
WING_VEIN = (120, 164, 212, 150)
WING_HL = (255, 255, 255, 90)
LEG = (28, 28, 36, 255)


def px(v: float) -> float:
    return v * S


def draw_wing(d: ImageDraw.ImageDraw, root: tuple[float, float], length: float, width: float,
              spread_deg: float, side: int, alpha_mul: float = 1.0) -> None:
    """Крыло-лопасть вдоль направления (-cos s, ∓sin s) в плоскости картинки.

    side = -1 — крыло со стороны «верх экрана», +1 — снизу. spread — угол от оси тела.
    """
    s = math.radians(spread_deg)
    dx = -math.cos(s)
    dy = side * math.sin(s)
    nx, ny = -dy, dx                      # нормаль к оси крыла
    rx, ry = px(root[0]), px(root[1])
    L = length * S
    W = width * S
    upper, lower = [], []
    steps = 44
    for i in range(steps + 1):
        t = i / steps
        # профиль ширины: узкий корень, максимум к середине, скруглённый кончик
        w = W * (math.sin(math.pi * min(1.0, t * 0.86 + 0.1)) ** 0.45)
        # лёгкий изгиб в сторону брюшка
        bend = math.sin(math.pi * t) * L * 0.05 * -side
        cx = rx + dx * L * t + nx * bend
        cy = ry + dy * L * t + ny * bend
        upper.append((cx + nx * w * 0.5, cy + ny * w * 0.5))
        lower.append((cx - nx * w * 0.5, cy - ny * w * 0.5))
    poly = upper + lower[::-1]
    d.polygon(poly,
              fill=(WING_FILL[0], WING_FILL[1], WING_FILL[2], int(WING_FILL[3] * alpha_mul)),
              outline=(WING_EDGE[0], WING_EDGE[1], WING_EDGE[2], int(WING_EDGE[3] * alpha_mul)))
    # прожилки
    for k in (0.45, 0.68, 0.88):
        x0 = rx + dx * L * 0.08
        y0 = ry + dy * L * 0.08
        x1 = rx + dx * L * k + nx * (k - 0.5) * W * 0.22 * side
        y1 = ry + dy * L * k + ny * (k - 0.5) * W * 0.22 * side
        d.line([(x0, y0), (x1, y1)],
               fill=(WING_VEIN[0], WING_VEIN[1], WING_VEIN[2], int(WING_VEIN[3] * alpha_mul)),
               width=int(0.5 * S))
    # блеск по передней кромке
    hl = [(x - nx * 1.2 * S, y - ny * 1.2 * S) for x, y in upper[4:28]]
    if len(hl) > 2:
        d.line(hl, fill=(WING_HL[0], WING_HL[1], WING_HL[2], int(WING_HL[3] * alpha_mul)),
               width=int(0.7 * S))


def draw_leg(d: ImageDraw.ImageDraw, root: tuple[float, float], out: float, back: float,
             side: int) -> None:
    """Лапка в плоскости экрана: наружу в сторону стороны side и назад."""
    x0, y0 = px(root[0]), px(root[1])
    x1, y1 = px(root[0] - back), px(root[1] + side * out)
    x2, y2 = px(root[0] - back * 1.6), px(root[1] + side * out * 1.25)
    d.line([(x0, y0), (x1, y1)], fill=LEG, width=int(1.4 * S))
    d.line([(x1, y1), (x2, y2)], fill=LEG, width=int(1.0 * S))


def draw_fly(*, legs_phase: float = 0.0, leg_spread: float = 1.0, body_dy: float = 0.0,
             spread_deg: float = 14.0, wing_len: float = 21.0, wing_width: float = 7.5,
             wing_alpha: float = 1.0, body_len: float = 1.0, head_dy: float = 0.0,
             chewing: bool = False, eyes_closed: bool = False) -> Image.Image:
    img = Image.new("RGBA", (SIZE * S, SIZE * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")

    cy = 32 + body_dy                     # ось тела

    # --- лапки: три пары, попеременно ---
    # три пары: передние вынесены вперёд, задние — назад (как у насекомого)
    for i, (bx, base_back) in enumerate(((27.0, -4.0), (33.0, 0.5), (39.0, 5.0))):
        swing = math.sin((legs_phase + i * 0.33) * 2 * math.pi)
        for side in (-1, 1):
            s = -swing if (i + side) % 2 == 0 else swing
            out = (6.4 + 1.6 * abs(s)) * leg_spread
            back = (base_back + 3.2 * s) * leg_spread
            draw_leg(d, (bx, cy + side * 6.0), out, back, side)

    # --- брюшко ---
    ab = 16.0 * body_len
    d.ellipse([px(30.0 - ab), px(cy - 7.6), px(30.0 + 4.0), px(cy + 7.6)], fill=ABDOMEN,
              outline=ABDOMEN_DARK, width=int(0.8 * S))
    for k, off in enumerate((0.2, 0.42, 0.64)):
        x = 30.0 - ab * off
        d.arc([px(x - 3.6), px(cy - 6.6), px(x + 3.6), px(cy + 6.6)],
              start=248, end=292, fill=ABDOMEN_DARK, width=int(0.9 * S))

    # --- грудь ---
    d.ellipse([px(28.0), px(cy - 9.2), px(44.0), px(cy + 9.2)], fill=THORAX)
    d.ellipse([px(31.0), px(cy - 7.0), px(42.0), px(cy + 0.5)], fill=THORAX_HL)

    # --- голова и глаза ---
    hx = 47.0
    hy = cy + head_dy
    d.ellipse([px(hx - 6.0), px(hy - 6.6), px(hx + 5.6), px(hy + 6.6)], fill=HEAD)
    for side in (-1, 1):
        eye = [px(hx - 3.4), px(hy + side * 5.6 - 3.6), px(hx + 4.6), px(hy + side * 5.6 + 3.6)]
        if eyes_closed:
            d.line([(eye[0], (eye[1] + eye[3]) / 2), (eye[2], (eye[1] + eye[3]) / 2)],
                   fill=EYE_DARK, width=int(1.4 * S))
        else:
            d.ellipse([eye[0], min(eye[1], eye[3]), eye[2], max(eye[1], eye[3])], fill=EYE)
            d.ellipse([px(hx - 1.6), px(hy + side * 5.6 - 2.4), px(hx + 0.4),
                       px(hy + side * 5.6 - 0.4)], fill=EYE_HL)
    # хоботок
    d.line([(px(hx + 5.0), px(hy + 0.6)), (px(hx + 9.2), px(hy + 1.4))], fill=LEG,
           width=int(0.9 * S))

    # --- крылья поверх корпуса: в покое лежат вдоль брюшка, в полёте разведены ---
    for side in (-1, 1):
        draw_wing(d, (35.0, cy + side * 3.2), wing_len, wing_width, spread_deg, side,
                  alpha_mul=wing_alpha)
    if chewing:
        d.line([(px(hx + 6.0), px(hy + 2.6)), (px(hx + 10.0), px(hy + 3.2))],
               fill=(92, 94, 108, 255), width=int(1.0 * S))

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def frames():
    out = []
    for i in range(2):
        out.append((f"idle_{i + 1}", draw_fly(
            spread_deg=17.0 + 4.0 * i, wing_len=23.0, wing_width=7.4,
            wing_alpha=0.9 + 0.15 * i, body_dy=0.3 * i)))
    for i in range(6):
        ph = i / 6.0
        out.append((f"walk_{i + 1}", draw_fly(
            legs_phase=ph, leg_spread=1.0, body_dy=0.5 * math.sin(ph * 2 * math.pi),
            spread_deg=18.0, wing_len=22.5, wing_width=7.2, wing_alpha=0.85)))
    for i in range(3):
        out.append((f"sleep_{i + 1}", draw_fly(
            leg_spread=0.5, body_dy=0.6 + 0.25 * i, spread_deg=7.0, wing_len=22.0,
            wing_width=6.8, wing_alpha=1.0, body_len=1.04, eyes_closed=True)))
    for i in range(2):
        out.append((f"chew_{i + 1}", draw_fly(
            spread_deg=12.0, wing_len=21.5, wing_width=7.0, wing_alpha=0.95,
            head_dy=-0.7 + 1.4 * i, chewing=True)))
    # полёт: крылья раскрыты в стороны
    for i, (spread, alpha, ln, wdt, dy) in enumerate((
            (42.0, 1.0, 22.0, 8.6, -0.3),
            (58.0, 1.15, 23.0, 9.2, -0.7),
            (50.0, 1.05, 22.0, 8.8, -0.3),
            (34.0, 0.95, 21.0, 8.2, -0.5))):
        out.append((f"fly_{i + 1}", draw_fly(
            spread_deg=spread, wing_len=ln, wing_width=wdt, wing_alpha=alpha,
            leg_spread=0.45, body_dy=dy)))
    out.append(("land_1", draw_fly(
        spread_deg=26.0, wing_len=21.0, wing_width=8.6, wing_alpha=1.1,
        leg_spread=1.2, body_dy=0.3)))
    return out


def save_gif(path: Path, images: list[Image.Image], ms: int = 120, scale: int = 4) -> None:
    composed = []
    for im in images:
        big = im.resize((SIZE * scale, SIZE * scale), Image.NEAREST)
        bg = Image.new("RGBA", big.size, (238, 238, 238, 255))
        step = 8 * scale
        for y in range(0, big.size[1], step):
            for x in range(0, big.size[0], step):
                if ((x // step + y // step) % 2) == 0:
                    bg.paste((216, 216, 216, 255), (x, y, x + step, y + step))
        bg.alpha_composite(big)
        composed.append(bg.convert("P", palette=Image.ADAPTIVE, colors=64))
    composed[0].save(path, save_all=True, append_images=composed[1:], duration=ms, loop=0,
                     disposal=2)


def main() -> None:
    out = Path(r"C:\Users\igora\AppData\Local\hermes\cache\scratch\fly_top")
    out.mkdir(parents=True, exist_ok=True)
    fr = frames()
    for name, im in fr:
        im.save(out / f"{name}.png")
    print(f"кадров: {len(fr)}")
    for name in ("idle", "walk", "sleep", "chew", "fly"):
        group = [im for n, im in fr if n.startswith(name)]
        save_gif(out / f"_{name}.gif", group, ms=320 if name == "sleep" else 110)
    cell = SIZE * 4
    sheet = Image.new("RGBA", (len(fr) * (cell + 6) + 6, cell + 12), (255, 255, 255, 255))
    for y in range(sheet.height):
        for x in range(sheet.width):
            v = 238 if ((x // 12 + y // 12) % 2 == 0) else 214
            sheet.putpixel((x, y), (v, v, v, 255))
    for i, (_n, im) in enumerate(fr):
        sheet.alpha_composite(im.resize((cell, cell), Image.NEAREST), (6 + i * (cell + 6), 6))
    sheet.save(out / "_sheet.png")
    print("лист:", out / "_sheet.png", sheet.size)


if __name__ == "__main__":
    main()
