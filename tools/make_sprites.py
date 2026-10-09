"""Процедурная отрисовка спрайтов мухи (Pillow). Версия 2 (исправлена геометрия).

Рисуем в системе координат 64x64, умножая всё на S (суперсэмплинг),
затем уменьшаем до 64x64 через LANCZOS. Муха смотрит вправо.
Кадры: idle (2), walk (6), sleep (3), chew (2) + анимированные превью (GIF).
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

S = 4                      # суперсэмплинг
SIZE = 64                  # итоговый размер кадра

BODY = (44, 46, 58, 255)
BODY_LIGHT = (104, 108, 126, 255)
HEAD = (38, 40, 52, 255)
EYE = (164, 44, 56, 255)
EYE_HL = (240, 205, 205, 235)
WING = (204, 228, 255, 96)
WING_EDGE = (150, 195, 240, 150)
LEG = (30, 30, 38, 255)
MOUTH = (86, 88, 102, 255)


def p(v: float) -> float:
    """Из координат 64x64 в координаты холста."""
    return v * S


def draw_wing(d: ImageDraw.ImageDraw, root: tuple[float, float], length: float,
              angle_deg: float, width: float, alpha: int, flip: int) -> None:
    """Каплевидное крыло из точки root под углом к вертикали."""
    rx, ry = root[0] * S, root[1] * S
    length *= S
    width *= S
    a = math.radians(angle_deg)
    outer, inner = [], []
    for i in range(25):
        t = i / 24
        r = length * (math.sin(math.pi * t) ** 0.65)
        outer.append((rx + flip * (r * math.sin(a) + width * 0.5 * math.sin(a)),
                      ry - r * math.cos(a)))
        inner.append((rx + flip * (r * math.sin(a) - width * 0.5),
                      ry - r * math.cos(a) * 0.85 + width * 0.15))
    poly = outer + inner[::-1]
    d.polygon(poly, fill=(WING[0], WING[1], WING[2], alpha),
              outline=(WING_EDGE[0], WING_EDGE[1], WING_EDGE[2], min(255, alpha + 70)))


def draw_leg(d: ImageDraw.ImageDraw, x: float, y: float, dx: float, dy: float,
             bend: float) -> None:
    x0, y0 = p(x), p(y)
    x1, y1 = p(x + dx), p(y + dy)
    knee = ((x0 + x1) / 2 + bend * S * 0.6, (y0 + y1) / 2 - abs(bend) * S * 0.25 + 2 * S)
    d.line([(x0, y0), knee], fill=LEG, width=int(1.6 * S))
    d.line([knee, (x1, y1)], fill=LEG, width=int(1.2 * S))


def draw_fly(*, legs_phase: float = 0.0, leg_spread: float = 1.0, body_dy: float = 0.0,
             wing_angle: float = 26.0, wing_alpha: int = 96, wing_len: float = 15.0,
             head_dy: float = 0.0, chewing: bool = False,
             eyes_closed: bool = False) -> Image.Image:
    img = Image.new("RGBA", (SIZE * S, SIZE * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")

    by = 33 + body_dy                      # центр корпуса по вертикали
    hx, hy = 47.0, by + head_dy            # центр головы

    # крылья (под корпусом), корень — у передней части груди
    for flip in (1, -1):
        draw_wing(d, (30.0, by - 4.0), wing_len, wing_angle, 5.4, wing_alpha, flip)

    # лапки: три пары
    for i, lx in enumerate((20.0, 30.0, 40.0)):
        fwd = math.sin((legs_phase + i * 0.33) * 2 * math.pi)
        for flip in (1, -1):
            reach = (9.0 + 3.0 * fwd) * leg_spread
            draw_leg(d, lx, by + 6.0,
                     dx=flip * (4.5 + 2.0 * abs(fwd)) * leg_spread, dy=reach,
                     bend=flip * (1.0 + 1.6 * fwd))

    # корпус
    d.ellipse([p(14.0), p(by - 9.0), p(46.0), p(by + 9.0)], fill=BODY)
    # сегменты на спинке
    for x0 in (20.0, 27.0):
        d.arc([p(x0), p(by - 9.5), p(x0 + 16.0), p(by + 9.5)], start=215, end=325,
              fill=BODY_LIGHT, width=int(1.1 * S))

    # голова
    d.ellipse([p(hx - 9.0), p(hy - 8.5), p(hx + 9.0), p(hy + 8.5)], fill=HEAD)

    # глаза
    if eyes_closed:
        d.line([(p(hx + 1.0), p(hy - 1.0)), (p(hx + 8.0), p(hy - 1.0))], fill=EYE,
               width=int(1.4 * S))
    else:
        d.ellipse([p(hx + 0.5), p(hy - 6.5), p(hx + 8.5), p(hy + 1.5)], fill=EYE)
        d.ellipse([p(hx + 3.0), p(hy - 5.5), p(hx + 6.0), p(hy - 3.0)], fill=EYE_HL)
    d.ellipse([p(hx - 8.5), p(hy - 5.0), p(hx - 1.0), p(hy + 1.5)], fill=(58, 60, 74, 255))

    # хоботок
    d.line([(p(hx + 7.0), p(hy + 4.0)), (p(hx + 12.0), p(hy + 9.0))], fill=LEG,
           width=int(1.1 * S))
    if chewing:
        d.line([(p(hx + 4.0), p(hy + 9.0)), (p(hx + 9.0), p(hy + 12.0))], fill=MOUTH,
               width=int(1.3 * S))
        d.line([(p(hx + 2.0), p(hy + 12.0)), (p(hx + 7.0), p(hy + 15.0))], fill=MOUTH,
               width=int(1.1 * S))

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def frames() -> list[tuple[str, Image.Image]]:
    out: list[tuple[str, Image.Image]] = []
    for i in range(2):
        out.append((f"idle_{i + 1}", draw_fly(
            wing_angle=26.0 + 8.0 * i, wing_alpha=92 + 22 * i, wing_len=15.0 + 1.2 * i,
            body_dy=0.0 if i == 0 else 0.35)))
    for i in range(6):
        ph = i / 6.0
        out.append((f"walk_{i + 1}", draw_fly(
            legs_phase=ph, leg_spread=1.0, body_dy=0.5 * math.sin(ph * 2 * math.pi),
            wing_angle=24.0, wing_alpha=88, wing_len=14.0)))
    for i in range(3):
        out.append((f"sleep_{i + 1}", draw_fly(
            leg_spread=0.4, body_dy=2.4 + 0.4 * i, wing_angle=6.0, wing_alpha=118,
            wing_len=11.0, eyes_closed=True)))
    for i in range(2):
        out.append((f"chew_{i + 1}", draw_fly(
            body_dy=0.4 * i, wing_angle=16.0, wing_alpha=110, wing_len=12.0,
            head_dy=-0.8 + 1.6 * i, chewing=True)))
    return out


def save_gif(path: Path, images: list[Image.Image], ms: int = 110, scale: int = 4) -> None:
    """Анимированный GIF из кадров (для просмотра в чате)."""
    composed = []
    for im in images:
        big = im.resize((SIZE * scale, SIZE * scale), Image.NEAREST)
        bg = Image.new("RGBA", big.size, (236, 236, 236, 255))
        step = 8 * scale
        for y in range(0, big.size[1], step):
            for x in range(0, big.size[0], step):
                if ((x // step + y // step) % 2) == 0:
                    bg.paste((214, 214, 214, 255), (x, y, x + step, y + step))
        bg.alpha_composite(big)
        composed.append(bg.convert("P", palette=Image.ADAPTIVE, colors=64))
    composed[0].save(path, save_all=True, append_images=composed[1:], duration=ms, loop=0,
                     disposal=2)


def main() -> None:
    out = Path(r"C:\Users\igora\AppData\Local\hermes\cache\scratch\fly_sprites")
    out.mkdir(parents=True, exist_ok=True)
    fr = frames()
    for name, im in fr:
        im.save(out / f"{name}.png")
    print(f"кадров: {len(fr)}")

    for name in ("idle", "walk", "sleep", "chew"):
        group = [im for n, im in fr if n.startswith(name)]
        save_gif(out / f"_{name}.gif", group, ms=320 if name == "sleep" else 110, scale=4)
        print("gif:", f"_{name}.gif", len(group), "кадров")

    scale = 4
    cell = SIZE * scale
    cols = len(fr)
    sheet = Image.new("RGBA", (cols * (cell + 6) + 6, cell + 12), (255, 255, 255, 255))
    for y in range(sheet.height):
        for x in range(sheet.width):
            v = 236 if ((x // 12 + y // 12) % 2 == 0) else 212
            sheet.putpixel((x, y), (v, v, v, 255))
    for idx, (_, im) in enumerate(fr):
        sheet.alpha_composite(im.resize((cell, cell), Image.NEAREST),
                              (6 + idx * (cell + 6), 6))
    sheet.save(out / "_sheet.png")
    print("лист:", out / "_sheet.png", sheet.size)


if __name__ == "__main__":
    main()
