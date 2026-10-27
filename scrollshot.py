#!/usr/bin/env python3
"""scrollshot: kaydırılan bir alanın (Teams thread, Slack, Chrome sayfası...) tek parça ekran görüntüsü.

Akış: alanı fareyle seç -> program alanı otomatik kaydırıp kareler alır -> kareleri
çakışan satırlardan birleştirir -> PNG / JPG / PDF olarak masaüstüne yazar.
"""
import argparse
import datetime
import os
import sys
import time

import numpy as np
from PIL import Image

PDF_MAX_PT = 14000  # PDF sayfa üst sınırı ~14400 pt; altında kal


# ---------- birleştirme çekirdeği (ekrandan bağımsız, test edilebilir) ----------

def to_gray(img, col_step=4):
    """Karşılaştırma için gri + sütun seyreltme. Satır sayısı korunur."""
    return np.asarray(img.convert("L"), dtype=np.int16)[:, ::col_step]


def find_shift(prev, new, min_overlap_frac=0.15, tol=4.0):
    """prev ve new aynı boyutlu gri diziler; içerik s satır yukarı kaymış: prev[s:] == new[:h-s].

    Dönüş: 0 = kareler aynı (kaydırma bitti), s>0 = kayma miktarı, None = eşleşme yok.
    """
    h = prev.shape[0]
    if np.abs(prev - new).mean() < 0.3:
        return 0
    min_ov = max(int(h * min_overlap_frac), 16)
    if h - min_ov < 1:
        return None
    d = np.empty(h - min_ov, dtype=np.float64)
    for s in range(1, h - min_ov + 1):
        d[s - 1] = np.abs(prev[s:] - new[: h - s]).mean()
    i = int(d.argmin())
    # Hem mutlak olarak düşük hem de diğer kaymalardan belirgin ayrışmalı.
    if d[i] < tol and d[i] < 0.5 * float(np.median(d)):
        return i + 1
    return None


class Stitcher:
    """Kareleri alt alta (direction='down') veya üst üste (direction='up') birleştirir."""

    def __init__(self, direction="down"):
        self.up = direction == "up"
        self.strips = []
        self.prev = None
        self.prev_gray = None

    def add(self, frame):
        """Dönüş: 'ok' | 'end' (aynı kare, bitti) | 'fail' (örtüşme bulunamadı)."""
        g = to_gray(frame)
        if self.prev is None:
            self.strips.append(frame)
            self.prev, self.prev_gray = frame, g
            return "ok"
        h = frame.height
        if self.up:
            s = find_shift(self.prev_gray[::-1], g[::-1])
        else:
            s = find_shift(self.prev_gray, g)
        if s == 0:
            return "end"
        if s is None:
            return "fail"
        if self.up:
            self.strips.insert(0, frame.crop((0, 0, frame.width, s)))
        else:
            self.strips.append(frame.crop((0, h - s, frame.width, h)))
        self.prev, self.prev_gray = frame, g
        return "ok"

    def result(self):
        total = sum(s.height for s in self.strips)
        out = Image.new("RGB", (self.strips[0].width, total))
        y = 0
        for s in self.strips:
            out.paste(s, (0, y))
            y += s.height
        return out


# ---------- ekran işleri ----------

def select_region():
    """Tam ekran yarı saydam katman; sürükle-bırak ile dikdörtgen seç. (x, y, w, h) döner."""
    import tkinter as tk

    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.25)
    root.configure(bg="black", cursor="crosshair")
    canvas = tk.Canvas(root, bg="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        root.winfo_screenwidth() // 2, 40, fill="white", font=("Helvetica", 20),
        text="Kaydırılacak alanı sürükleyerek seç  (Esc = iptal)",
    )
    st = {"x0": 0, "y0": 0, "rect": None, "box": None}

    def down(e):
        st["x0"], st["y0"] = e.x_root, e.y_root
        st["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="red", width=3)

    def move(e):
        if st["rect"]:
            x0, y0 = st["x0"] - root.winfo_rootx(), st["y0"] - root.winfo_rooty()
            canvas.coords(st["rect"], x0, y0, e.x, e.y)

    def up(e):
        x1, y1 = e.x_root, e.y_root
        x, y = min(st["x0"], x1), min(st["y0"], y1)
        w, h = abs(x1 - st["x0"]), abs(y1 - st["y0"])
        if w > 20 and h > 20:
            st["box"] = (x, y, w, h)
            root.destroy()

    canvas.bind("<ButtonPress-1>", down)
    canvas.bind("<B1-Motion>", move)
    canvas.bind("<ButtonRelease-1>", up)
    root.bind("<Escape>", lambda e: root.destroy())
    root.mainloop()
    return st["box"]


def capture(region, direction, amount, delay, max_frames):
    import mss
    import pyautogui

    pyautogui.FAILSAFE = True  # fareyi ekran köşesine götürürsen durur
    x, y, w, h = region
    sign = -1 if direction == "down" else 1
    pyautogui.moveTo(x + w // 2, y + h // 2)

    def scroll(n):
        pyautogui.scroll(sign * n)
        time.sleep(delay)

    with mss.mss() as sct:
        def grab():
            shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
            return Image.frombytes("RGB", shot.size, shot.rgb)

        st = Stitcher(direction)
        st.add(grab())
        frames, fails = 1, 0
        while frames < max_frames:
            scroll(amount)
            res = st.add(grab())
            if res == "end":
                print("  Sona gelindi.")
                break
            if res == "fail":
                fails += 1
                scroll(-amount)  # geri al
                if amount == 1 or fails >= 3:
                    print("  UYARI: kareler eşleşmedi, burada durdum (animasyon/kayan öğe olabilir).")
                    break
                amount = max(1, amount // 2)
                print(f"  Eşleşme yok, kaydırma {amount} satıra düşürüldü.")
                continue
            fails = 0
            frames += 1
            print(f"  kare {frames}  (yükseklik {sum(s.height for s in st.strips)} px)", end="\r")
        else:
            print("\n  UYARI: --max-frames sınırına ulaşıldı; sayfa sonu olmayabilir.")
        print()
        return st.result(), w


# ---------- çıktı ----------

def save_outputs(img, region_w, formats, outdir, name, split):
    os.makedirs(outdir, exist_ok=True)
    scale = img.width / region_w  # Retina'da 2.0
    written = []

    def chunks():
        if not split:
            yield "", img
            return
        for i, top in enumerate(range(0, img.height, split), 1):
            yield f"_{i:02d}", img.crop((0, top, img.width, min(top + split, img.height)))

    for fmt in formats:
        if fmt in ("png", "jpg"):
            if fmt == "jpg" and img.height > 65000 and not split:
                print("  JPG: 65000 px sınırı aşıldı, --split kullan ya da PNG/PDF seç. Atlandı.")
                continue
            for suffix, part in chunks():
                path = os.path.join(outdir, f"{name}{suffix}.{fmt}")
                if fmt == "png":
                    part.save(path, dpi=(72 * scale, 72 * scale))
                else:
                    part.save(path, quality=92)
                written.append(path)
        elif fmt == "pdf":
            chunk_px = int(PDF_MAX_PT * scale)
            pages = [img.crop((0, t, img.width, min(t + chunk_px, img.height)))
                     for t in range(0, img.height, chunk_px)]
            path = os.path.join(outdir, f"{name}.pdf")
            pages[0].save(path, "PDF", resolution=72 * scale, save_all=True, append_images=pages[1:])
            written.append(path)
            if len(pages) > 1:
                print(f"  PDF {len(pages)} sayfaya bölündü (tek sayfa sınırı aşıldı).")
    return written


def ask_formats():
    print("Çıktı biçimi:\n  1) PNG\n  2) PDF\n  3) JPG\n  4) PNG + PDF")
    choice = input("Seçim [1]: ").strip() or "1"
    return {"1": ["png"], "2": ["pdf"], "3": ["jpg"], "4": ["png", "pdf"]}.get(choice, ["png"])


def main():
    ap = argparse.ArgumentParser(description="Kaydırmalı alanın tam ekran görüntüsü")
    ap.add_argument("--format", help="png,pdf,jpg (virgülle birden fazla). Verilmezse sorar.")
    ap.add_argument("--direction", choices=["down", "up"], default="down",
                    help="down: yukarıdan aşağı (varsayılan). up: aşağıdan yukarı (Teams'te eski mesajlar için)")
    ap.add_argument("--out", default=os.path.join(os.path.expanduser("~"), "Desktop"))
    ap.add_argument("--name", help="dosya adı (uzantısız)")
    ap.add_argument("--scroll", type=int, default=5, help="her adımda kaydırma miktarı (satır)")
    ap.add_argument("--delay", type=float, default=0.8, help="kaydırmadan sonra bekleme (sn)")
    ap.add_argument("--max-frames", type=int, default=300)
    ap.add_argument("--split", type=int, default=0, help="PNG/JPG'yi bu piksel yükseklikte parçala (0=bölme)")
    ap.add_argument("--region", help="x,y,w,h (verilmezse fareyle seçilir)")
    ap.add_argument("--countdown", type=int, default=3)
    args = ap.parse_args()

    formats = [f.strip().lower().replace("jpeg", "jpg") for f in args.format.split(",")] if args.format else ask_formats()
    if args.region:
        region = tuple(int(v) for v in args.region.split(","))
    else:
        print("Alan seçimi açılıyor... (kaydırılacak mesaj/sayfa alanını seç; başlık ve yazma kutusunu dışarıda bırak)")
        region = select_region()
        if not region:
            sys.exit("İptal edildi.")
    print(f"Alan: {region}. {args.countdown} sn sonra başlıyor; pencereyi öne al, fareye dokunma.")
    for i in range(args.countdown, 0, -1):
        print(f"  {i}...", end="\r")
        time.sleep(1)
    img, region_w = capture(region, args.direction, args.scroll, args.delay, args.max_frames)
    name = args.name or "scrollshot_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    for p in save_outputs(img, region_w, formats, args.out, name, args.split):
        print("Kaydedildi:", p)
    print(f"Boyut: {img.width}x{img.height} px")


if __name__ == "__main__":
    main()
