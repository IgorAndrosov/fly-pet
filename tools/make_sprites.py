"""Спрайты мухи, версия 7: лапки как на фото — длинные, веером; передние вынесены вперёд за голову.

Кадры: idle 2, walk 6, sleep 3, chew 2, fly 4, land 1, rub 4.
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
LEG = (30, 30, 38, 255)
LEG_HL = (74, 76, 92, 255)

# геометрия лапок: корень, сгиб, кончик (в системе 64x64, x — вперёд к голове)
LEG_GEOM = {
    # кончики передних уходят вперёд ЗА голову, задних — назад за брюшко (как на фото)
    "front": ((40.0, 4.0), (47.0, 7.0), (56.0, 9.5)),
    "mid": ((33.0, 6.0), (36.0, 13.0), (36.0, 19.5)),
    "hind": ((27.0, 6.0), (21.0, 12.5), (13.5, 17.0)),
}
LEG_ORDER = (("front", 0.0), ("mid", 0.33), ("hind", 0.66))


def px(v: float) -> float:
    return v * S


def draw_wing(d, root, length, width, spread_deg, side, alpha_mul=1.0):
    s = math.radians(spread_deg)
    dx, dy = -math.cos(s), side * math.sin(s)
    nx, ny = -dy, dx
    rx, ry = px(root[0]), px(root[1])
    L, W = length * S, width * S
    upper, lower = [], []
    for i in range(45):
        t = i / 44
        w = W * (math.sin(math.pi * min(1.0, t * 0.86 + 0.1)) ** 0.45)
        bend = math.sin(math.pi * t) * L * 0.05 * -side
        cx = rx + dx * L * t + nx * bend
        cy = ry + dy * L * t + ny * bend
        upper.append((cx + nx * w * 0.5, cy + ny * w * 0.5))
        lower.append((cx - nx * w * 0.5, cy - ny * w * 0.5))
    d.polygon(upper + lower[::-1],
              fill=(WING_FILL[0], WING_FILL[1], WING_FILL[2], int(WING_FILL[3] * alpha_mul)),
              outline=(WING_EDGE[0], WING_EDGE[1], WING_EDGE[2], int(WING_EDGE[3] * alpha_mul)))
    for k in (0.45, 0.68, 0.88):
        d.line([(rx + dx * L * 0.08, ry + dy * L * 0.08),
                (rx + dx * L * k + nx * (k - 0.5) * W * 0.22 * side,
                 ry + dy * L * k + ny * (k - 0.5) * W * 0.22 * side)],
               fill=(WING_VEIN[0], WING_VEIN[1], WING_VEIN[2], int(WING_VEIN[3] * alpha_mul)),
               width=int(0.5 * S))
    hl = [(x - nx * 1.2 * S, y - ny * 1.2 * S) for x, y in upper[4:28]]
    if len(hl) > 2:
        d.line(hl, fill=(WING_HL[0], WING_HL[1], WING_HL[2], int(WING_HL[3] * alpha_mul)),
               width=int(0.7 * S))


def seg(d, p0, p1, width, color=LEG):
    d.line([(px(p0[0]), px(p0[1])), (px(p1[0]), px(p1[1]))], fill=color, width=int(width * S))


def joint(d, p, r, color=LEG):
    d.ellipse([px(p[0] - r), px(p[1] - r), px(p[0] + r), px(p[1] + r)], fill=color)


def draw_leg(d, pair: str, side: int, cy: float, swing: float, leg_scale: float = 1.0) -> None:
    """Лапка веером: корень у груди, сгиб наружу, кончик далеко.

    swing —0..1: смещение вперёд-назад вдоль оси тела (шаг).
    """
    (root_x, root_y), (knee_x, knee_y), (tip_x, tip_y) = LEG_GEOM[pair]
    cx, ky, ty = root_x + swing * 2.6, knee_y, tip_y
    kx, tx = knee_x + swing * 1.4, tip_x + swing * 2.2
    root = (cx, cy + side * root_y * leg_scale)
    knee = (kx, cy + side * ky * leg_scale)
    tip = (tx, cy + side * ty * leg_scale)
    seg(d, root, knee, 1.5)
    seg(d, knee, tip, 1.1)
    joint(d, knee, 0.75, LEG_HL)
    joint(d, tip, 0.6)


def draw_front_rubbing(d, cy: float, phase: float) -> None:
    """Передние лапки перед лицом: смыкаются у основания, кончики слегка разведены."""
    for side in (-1, 1):
        rub = math.sin(phase * 2 * math.pi + (0.0 if side < 0 else math.pi))
        root = (40.0, cy + side * 4.0)
        knee = (47.5 + 0.9 * rub, cy + side * 2.8)          # сомкнуты к центру
        tip = (56.0 + 1.8 * rub, cy + side * 6.2)           # кончики разведены, впереди головы
        seg(d, root, knee, 1.5, LEG_HL)
        seg(d, knee, tip, 1.2, LEG_HL)
        joint(d, knee, 0.8, LEG_HL)
        joint(d, tip, 0.65, LEG_HL)


def draw_fly(*, legs_phase: float = 0.0, leg_scale: float = 1.0, body_dy: float = 0.0,
             spread_deg: float = 14.0, wing_len: float = 23.0, wing_width: float = 7.4,
             wing_alpha: float = 1.0, body_len: float = 1.0, head_dy: float = 0.0,
             chewing: bool = False, eyes_closed: bool = False,
             rub_phase: float | None = None) -> Image.Image:
    img = Image.new("RGBA", (SIZE * S, SIZE * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")

    cy = 32 + body_dy

    # --- лапки (под корпусом) ---
    for pair, offset in LEG_ORDER:
        for side in (-1, 1):
            if pair == "front" and rub_phase is not None:
                continue
            swing = math.sin((legs_phase + offset) * 2 * math.pi)
            if (pair == "front") == (side < 0):          # тренога: чередуем пары по сторонам
                swing = -swing
            draw_leg(d, pair, side, cy, swing, leg_scale)

    # --- брюшко ---
    ab = 16.0 * body_len
    d.ellipse([px(30.0 - ab), px(cy - 7.6), px(30.0 + 4.0), px(cy + 7.6)], fill=ABDOMEN,
              outline=ABDOMEN_DARK, width=int(0.8 * S))
    for off in (0.2, 0.42, 0.64):
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
    d.line([(px(hx + 5.0), px(hy + 0.6)), (px(hx + 9.2), px(hy + 1.4))], fill=LEG,
           width=int(0.9 * S))
    if chewing:
        d.line([(px(hx + 6.0), px(hy + 2.6)), (px(hx + 10.0), px(hy + 3.2))],
               fill=(92, 94, 108, 255), width=int(1.0 * S))

    # --- передние лапки при груминге (поверх груди и головы) ---
    if rub_phase is not None:
        draw_front_rubbing(d, cy, rub_phase)

    # --- крылья поверх корпуса ---
    for side in (-1, 1):
        draw_wing(d, (35.0, cy + side * 3.2), wing_len, wing_width, spread_deg, side,
                  alpha_mul=wing_alpha)

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
            legs_phase=ph, body_dy=0.5 * math.sin(ph * 2 * math.pi),
            spread_deg=18.0, wing_len=22.5, wing_width=7.2, wing_alpha=0.85)))
    for i in range(3):
        out.append((f"sleep_{i + 1}", draw_fly(
            leg_scale=0.55, body_dy=0.6 + 0.25 * i, spread_deg=7.0, wing_len=22.0,
            wing_width=6.8, wing_alpha=1.0, body_len=1.04, eyes_closed=True)))
    for i in range(2):
        out.append((f"chew_{i + 1}", draw_fly(
            spread_deg=12.0, wing_len=21.5, wing_width=7.0, wing_alpha=0.95,
            head_dy=-0.7 + 1.4 * i, chewing=True)))
    for i, (spread, alpha, ln, wdt, dy) in enumerate((
            (42.0, 1.0, 22.0, 8.6, -0.3),
            (58.0, 1.15, 23.0, 9.2, -0.7),
            (50.0, 1.05, 22.0, 8.8, -0.3),
            (34.0, 0.95, 21.0, 8.2, -0.5))):
        out.append((f"fly_{i + 1}", draw_fly(
            spread_deg=spread, wing_len=ln, wing_width=wdt, wing_alpha=alpha,
            leg_scale=0.5, body_dy=dy)))
    out.append(("land_1", draw_fly(
        spread_deg=26.0, wing_len=21.0, wing_width=8.6, wing_alpha=1.1,
        leg_scale=1.15, body_dy=0.3)))
    for i in range(4):
        ph = i / 4.0
        out.append((f"rub_{i + 1}", draw_fly(
            spread_deg=15.0, wing_len=22.5, wing_width=7.2, wing_alpha=0.95,
            body_dy=-0.4 + 0.3 * math.sin(ph * 2 * math.pi), head_dy=-0.5, rub_phase=ph)))
    return out


def save_gif(path: Path, images, ms=120, scale=4):
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
    for name in ("idle", "walk", "sleep", "chew", "fly", "rub"):
        group = [im for n, im in fr if n.startswith(name)]
        save_gif(out / f"_{name}.gif", group, ms=300 if name == "sleep" else 120)
    # крупное сравнение: покой, ходьба, груминг
    picks = [f for f in fr if f[0] in ("idle_1", "walk_1", "walk_3", "rub_1", "rub_2", "rub_3")]
    cell = SIZE * 7
    sheet = Image.new("RGBA", (len(picks) * (cell + 8) + 8, cell + 16), (244, 244, 244, 255))
    for i, (_n, im) in enumerate(picks):
        bg = Image.new("RGBA", im.size, (244, 244, 244, 255))
        bg.alpha_composite(im)
        sheet.alpha_composite(bg.resize((cell, cell), Image.NEAREST), (8 + i * (cell + 8), 8))
    sheet.save(out / "_legs_preview.png")
    print("превью лапок:", out / "_legs_preview.png", sheet.size)


if __name__ == "__main__":
    main()
