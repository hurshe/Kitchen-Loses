"""Иконки (колпак повара) — рисуем кодом, чтобы не таскать бинарники
в репозитории и можно было перекрасить одной строкой.

    python gen_icons.py

Кладёт:
    icon.ico        — окно программы и установщик
    server/icon.png — иконка формы на телефоне повара
"""

import os

from PIL import Image, ImageDraw

BG = (14, 16, 19, 255)          # --bg приложения
ACCENT = (217, 149, 46, 255)    # золото Beer Count
WHITE = (237, 239, 242, 255)
HERE = os.path.dirname(os.path.abspath(__file__))


def chef_hat(size=512, radius_ratio=0.22, bleed=False):
    """Колпак повара на тёмном фоне.

    `bleed=True` — фон во весь квадрат и рисунок мельче: так иконка
    переживает обрезку под любую форму (Android, плитки Windows).
    """
    s = size * 4                    # рисуем крупнее и уменьшаем — сглаживание
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if bleed:
        d.rectangle([0, 0, s, s], fill=BG)
    else:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * radius_ratio),
                            fill=BG)

    inset = s * (0.30 if bleed else 0.17)
    x0, y0, x1 = inset, inset, s - inset
    w = x1 - x0

    def px(fx, fy):
        return x0 + w * fx, y0 + w * fy

    for cx, cy, rr in ((0.24, 0.34, 0.24), (0.50, 0.26, 0.29), (0.76, 0.34, 0.24)):
        c = px(cx, cy)
        rad = w * rr
        d.ellipse([c[0] - rad, c[1] - rad, c[0] + rad, c[1] + rad], fill=WHITE)

    d.rectangle([*px(0.20, 0.34), *px(0.80, 0.70)], fill=WHITE)
    d.rounded_rectangle([*px(0.16, 0.66), *px(0.84, 0.94)],
                        radius=w * 0.06, fill=WHITE)
    d.rounded_rectangle([*px(0.16, 0.76), *px(0.84, 0.84)],
                        radius=w * 0.03, fill=ACCENT)

    return img.resize((size, size), Image.LANCZOS)


def main():
    big = chef_hat(512)
    # .ico держит несколько размеров: Windows берёт подходящий сам —
    # 16/32 для панели задач, 256 для крупных плиток
    big.save(os.path.join(HERE, "icon.ico"),
             sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128),
                    (256, 256)])
    os.makedirs(os.path.join(HERE, "server"), exist_ok=True)
    chef_hat(192, bleed=True).save(os.path.join(HERE, "server", "icon.png"))
    # 512 — без неё Chrome на Android не предложит «Zainstaluj aplikację»;
    # заодно служит maskable-иконкой (рисунок уже внутри безопасной зоны)
    chef_hat(512, bleed=True).save(os.path.join(HERE, "server", "icon-512.png"))
    print("Zapisano icon.ico, server/icon.png i server/icon-512.png")


if __name__ == "__main__":
    main()
