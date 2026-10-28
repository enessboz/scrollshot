"""Görüntüden birleştirme akışını (alan tespiti, üste çıkış, tembel yükleme atlaması, aşağı yakalama) sahte ekranla uçtan uca test eder.
Çalıştır: python test_pixel_sim.py
"""
import os
import sys
import types

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scrollshot as ss  # noqa: E402

N, W_AREA, H_AREA = 100, 800, 520
WIN = (100, 50, 1000, 700)  # x, y, w, h
AREA = (200, 80)  # penceredeki kayan alanın sol-üstü


def item(i):
    h = 40 + (i * 37 % 60)
    im = Image.new("RGB", (W_AREA, h), (30, 30, 34))  # koyu tema
    d = ImageDraw.Draw(im)
    d.text((14, 8), f"Mesaj {i} - " + "x" * (i % 40), fill=(235, 235, 235))
    d.line([0, h - 1, W_AREA, h - 1], fill=(70, 70, 76))
    return im


class Sim:
    def __init__(self, anchor):
        self.anchor, self.first = anchor, N - 25
        self.top = None
        self.mouse = (0, 0)
        self.tick = 0
        self.top = self.height() - H_AREA  # en altta

    def content(self):
        ims = [item(i) for i in range(self.first, N)]
        out = Image.new("RGB", (W_AREA, sum(m.height for m in ims)))
        y = 0
        for m in ims:
            out.paste(m, (0, y))
            y += m.height
        return out

    def height(self):
        return sum(item(i).height for i in range(self.first, N))

    def scroll(self, clicks):
        mx, my = self.mouse
        if not (WIN[0] + AREA[0] <= mx < WIN[0] + AREA[0] + W_AREA and WIN[1] + AREA[1] <= my < WIN[1] + AREA[1] + H_AREA):
            return
        self.top = int(max(0, min(self.height() - H_AREA, self.top - clicks * 40)))
        if self.top < 100 and self.first > 0:  # tembel yükleme: eski 25 mesaj başa eklenir
            old = self.height()
            self.first = max(0, self.first - 25)
            if self.anchor:
                self.top += self.height() - old  # tarayıcı konumu korur
            # anchor=False: görünüm yeni içeriğin başına atlar (konum sayısı aynı kalır)

    def screen(self):
        self.tick += 1
        im = Image.new("RGB", (WIN[2], WIN[3]), (20, 20, 24))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, WIN[2], 80], fill=(60, 60, 200))
        d.rectangle([0, 80, AREA[0], WIN[3]], fill=(40, 40, 50))
        for k in range(8):
            d.line([10, 100 + k * 40, 150, 100 + k * 40], fill=(200, 200, 200), width=3)
        d.rectangle([AREA[0], AREA[1] + H_AREA, WIN[2], WIN[3]], fill=(44, 44, 50))
        if self.tick % 2:
            d.rectangle([AREA[0] + 20, 640, AREA[0] + 22, 660], fill=(255, 255, 255))  # yanıp sönen imleç
        c = self.content().crop((0, self.top, W_AREA, self.top + H_AREA))
        im.paste(c, AREA)
        full = Image.new("RGB", (1500, 900), (0, 0, 0))
        full.paste(im, (WIN[0], WIN[1]))
        return full


def install_fakes(sim):
    class Shot:
        def __init__(self, img):
            self.size, self.rgb = img.size, img.tobytes()

    class FakeMSS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def grab(self, r):
            return Shot(sim.screen().crop((r["left"], r["top"], r["left"] + r["width"], r["top"] + r["height"])))

    class FailSafe(Exception):
        pass

    mss_mod = types.ModuleType("mss")
    mss_mod.mss = FakeMSS
    pg = types.ModuleType("pyautogui")
    pg.FAILSAFE, pg.PAUSE, pg.FailSafeException = True, 0, FailSafe
    pg.moveTo = lambda x, y: setattr(sim, "mouse", (x, y))
    pg.scroll = sim.scroll
    sys.modules["mss"], sys.modules["pyautogui"] = mss_mod, pg
    ss.time = types.SimpleNamespace(sleep=lambda s: None, time=__import__("time").time)


def run(anchor):
    sim = Sim(anchor)
    install_fakes(sim)
    img, scale, info = ss.capture_window(WIN)
    truth = Sim(anchor)
    truth.first = 0
    full = truth.content()
    assert info["stop"] == "end", f"anchor={anchor}: durma nedeni {info['stop']}; son günlük: {info['log'][-3:]}"
    rx, ry, rw, rh = info["rect"]
    x0 = rx - (WIN[0] + AREA[0])
    a = np.asarray(img, dtype=int)
    b = np.asarray(full.crop((x0, 0, x0 + rw, full.height)), dtype=int)
    # Birleşmiş görüntü, gerçeğin ardışık bir parçası olmalı (üst/alt kenar boşlukları tespitte kırpılabilir)
    best = min(range(0, 40), key=lambda off: np.abs(a[:200] - b[off:off + 200]).mean())
    n = min(len(a), len(b) - best)
    diff = np.abs(a[:n] - b[best:best + n]).max()
    assert diff == 0, f"anchor={anchor}: piksel farkı {diff}"
    assert len(b) - len(a) <= 80, f"anchor={anchor}: görüntü çok kısa: {len(a)} / {len(b)}"
    print(f"OK pixel anchor={anchor}: {info['frames']} kare, {img.size}, gerçek {b.shape[1]}x{b.shape[0]}, ofset {best}")


if __name__ == "__main__":
    run(True)
    run(False)
