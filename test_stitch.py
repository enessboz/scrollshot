"""Birleştirme çekirdeğini sentetik kaydırmayla test eder (ekran gerekmez).
Çalıştır: python tools/scrollshot/test_stitch.py
"""
import os
import random
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))
from scrollshot import Stitcher  # noqa: E402


def make_page(w=600, h=4000, seed=1):
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    y = 10
    while y < h - 40:
        # sohbet balonu benzeri: rastgele renkli blok + "metin" çizgileri
        d.rectangle([20, y, w - 20, y + 60], fill=(rnd.randint(200, 255),) * 3)
        for k in range(4):
            x0 = 30 + rnd.randint(0, 40)
            d.line([x0, y + 10 + k * 12, x0 + rnd.randint(100, 450), y + 10 + k * 12], fill=(rnd.randint(0, 120),) * 3, width=3)
        y += 60 + rnd.randint(10, 50)
    return img


def run(direction, steps, vh=800):
    page = make_page()
    H = page.height
    st = Stitcher(direction)
    pos = 0 if direction == "down" else H - vh
    out = None
    for step in steps + [0] * 2:  # sonda aynı kare -> end
        frame = page.crop((0, pos, page.width, pos + vh))
        res = st.add(frame)
        if res == "end":
            out = st.result()
            break
        assert res == "ok", f"{direction}: beklenmeyen {res} (pos={pos})"
        pos = pos + step if direction == "down" else pos - step
        pos = max(0, min(H - vh, pos))
    assert out is not None, "bitiş algılanmadı"
    assert out.size == page.size, f"{direction}: boyut {out.size} != {page.size}"
    diff = np.abs(np.asarray(out, dtype=int) - np.asarray(page, dtype=int)).max()
    assert diff == 0, f"{direction}: piksel farkı {diff}"
    print(f"OK {direction}: {out.size}")


if __name__ == "__main__":
    rnd = random.Random(7)
    steps = [rnd.randint(250, 650) for _ in range(20)]
    run("down", steps)
    run("up", steps)
    print("Tüm testler geçti.")
