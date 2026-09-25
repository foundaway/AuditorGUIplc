"""Genera assets/l5x_auditor.ico y assets/l5x_auditor.png (requiere Pillow).

Uso:  python tools/make_icon.py
"""

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "assets")
S = 1024  # se dibuja grande y se reduce para que quede suave


def draw():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # fondo: degradado azul en un cuadrado redondeado
    grad = Image.new("RGBA", (S, S))
    g = ImageDraw.Draw(grad)
    top, bot = (79, 140, 255), (29, 78, 216)
    for y in range(S):
        t = y / (S - 1)
        g.line([(0, y), (S, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bot)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([24, 24, S - 24, S - 24], radius=220, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    # rieles del diagrama escalera (L1 / L2) y un renglon
    rail = (255, 255, 255, 110)
    d.rounded_rectangle([200, 210, 250, 814], radius=25, fill=rail)
    d.rounded_rectangle([774, 210, 824, 814], radius=25, fill=rail)
    d.rounded_rectangle([250, 300, 774, 340], radius=20, fill=rail)
    # marca de verificacion
    white = (255, 255, 255, 255)
    d.line([(318, 560), (460, 700), (716, 420)], fill=white, width=96, joint="curve")
    for x, y in ((318, 560), (716, 420)):
        d.ellipse([x - 48, y - 48, x + 48, y + 48], fill=white)
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    img = draw()
    img.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "l5x_auditor.png"))
    img.save(os.path.join(OUT, "l5x_auditor.ico"),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Iconos generados en", os.path.normpath(OUT))


if __name__ == "__main__":
    main()
