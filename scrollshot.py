"""ScrollShot çekirdeği: kaydırılan alanı otomatik bulur, kaydırarak yakalar, birleştirir, dışa aktarır."""
import os
import time

import numpy as np
from PIL import Image

PDF_MAX_PT = 14000  # PDF sayfa üst sınırı ~14400 pt; altında kal


class CaptureError(Exception):
    pass


def to_gray(img, col_step=4):
    """Karşılaştırma için gri + sütun seyreltme. Satır sayısı korunur."""
    return np.asarray(img.convert("L"), dtype=np.int16)[:, ::col_step]


def find_shift(prev, new, min_overlap_frac=0.1, tol=6.0, margin=0.1, diag=None):
    """prev ve new aynı boyutlu gri diziler; içerik s satır yukarı kaymış: prev[s:] == new[:h-s].

    Üst/alt `margin` kadar satır karşılaştırmadan çıkarılır (kayan alanın üstünde duran 'Dün/Bugün' etiketi gibi sabit öğeler).
    Dönüş: 0 = kareler aynı (kaydırma bitti), s>0 = kayma miktarı, None = eşleşme yok.
    diag: verilirse {'best','median','shift'} yazılır (günlük için).
    """
    h = prev.shape[0]
    if np.abs(prev - new).mean() < 0.3:
        return 0
    m = int(h * margin)
    min_len = max(int(h * min_overlap_frac), 16)
    smax = h - 2 * m - min_len
    if smax < 1:
        return None
    d = np.empty(smax, dtype=np.float64)
    for s in range(1, smax + 1):
        d[s - 1] = np.abs(prev[s + m:h - m] - new[m:h - m - s]).mean()
    i = int(d.argmin())
    if diag is not None:
        diag.update(best=float(d[i]), median=float(np.median(d)), shift=i + 1)
    # Hem mutlak olarak düşük hem de diğer kaymalardan belirgin ayrışmalı.
    if d[i] < tol and d[i] < 0.5 * float(np.median(d)):
        return i + 1
    return None


def detect_scroll_region(a_img, b_img, thresh=25, row_frac=0.01, col_frac=0.01):
    """İki kare arasında değişen alan = kaydırılan alan (sabit başlık/yan liste/yazma kutusu elenir).

    Dönüş: (x0, y0, x1, y1) piksel, ya da None.
    """
    a = np.asarray(a_img.convert("L"), dtype=np.int16)
    b = np.asarray(b_img.convert("L"), dtype=np.int16)
    d = np.abs(a - b) > thresh
    H, W = d.shape
    rows = d.sum(1) > max(3, row_frac * W)
    idx = np.flatnonzero(rows)
    if idx.size == 0:
        return None
    gap = max(8, int(0.05 * H))
    runs, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > gap:
            runs.append((start, prev))
            start = i
        prev = i
    runs.append((start, prev))
    y0, y1 = max(runs, key=lambda r: rows[r[0]:r[1] + 1].sum())
    if y1 - y0 < 0.15 * H:
        return None
    cols = d[y0:y1 + 1].sum(0) > max(2, col_frac * (y1 - y0 + 1))
    cidx = np.flatnonzero(cols)
    if cidx.size == 0:
        return None
    return int(cidx[0]), int(y0), int(cidx[-1]) + 1, int(y1) + 1


class Stitcher:
    """Kareleri alt alta (direction='down') veya üst üste (direction='up') birleştirir."""

    def __init__(self, direction="down"):
        self.up = direction == "up"
        self.strips = []
        self.prev = None
        self.prev_gray = None
        self.last_shift = 0
        self.diag = {}

    def add(self, frame, hints=()):
        """Dönüş: 'ok' | 'end' (aynı kare, bitti) | 'fail' (örtüşme bulunamadı).

        hints: bilinen olası kayma miktarları (piksel). Önce bunlar denenir (örn. sayfa içinden okunan scrollTop farkı),
        tutmazsa tüm kaymalar taranır.
        """
        g = to_gray(frame)
        if self.prev is None:
            self.strips.append(frame)
            self.prev, self.prev_gray = frame, g
            return "ok"
        h = frame.height
        if np.abs(self.prev_gray - g).mean() < 0.3:
            return "end"
        s = None
        for hs in hints:
            if self._hint_ok(g, hs):
                s = hs
                break
        if s is None:
            if self.up:
                s = find_shift(self.prev_gray[::-1], g[::-1], diag=self.diag)
            else:
                s = find_shift(self.prev_gray, g, diag=self.diag)
        if s == 0:
            return "end"
        if s is None:
            return "fail"
        if self.up:
            self.strips.insert(0, frame.crop((0, 0, frame.width, s)))
        else:
            self.strips.append(frame.crop((0, h - s, frame.width, h)))
        self.prev, self.prev_gray = frame, g
        self.last_shift = s
        return "ok"

    def _diff_at(self, g, s):
        h = g.shape[0]
        if not 1 <= s < h - 8:
            return None
        if self.up:
            a, b = self.prev_gray[:h - s], g[s:]
        else:
            a, b = self.prev_gray[s:], g[:h - s]
        return float(np.abs(a - b).mean())

    def _hint_ok(self, g, s):
        """Bilinen kayma doğrulaması: neredeyse birebir eşleşmeli ya da komşu kaymalardan belirgin iyi olmalı."""
        d0 = self._diff_at(g, s)
        if d0 is None:
            return False
        if d0 < 0.5:
            return True
        if d0 >= 3.0:
            return False
        nb = [d for d in (self._diff_at(g, s + k) for k in (-30, -15, 15, 30)) if d is not None]
        return bool(nb) and d0 < 0.6 * min(nb)

    def height(self):
        return sum(s.height for s in self.strips)

    def result(self):
        out = Image.new("RGB", (self.strips[0].width, self.height()))
        y = 0
        for s in self.strips:
            out.paste(s, (0, y))
            y += s.height
        return out


def capture_window(win, progress=None, delay=0.7, max_frames=400):
    """win = (x, y, w, h) nokta cinsinden pencere. Kaydırılan alanı bulur, en alta gidip yukarı doğru yakalar.

    Dönüş: (PIL görüntü, ölçek, info). info = kare sayısı, durma nedeni (end/fail/max/failsafe), ayrıntılı günlük.
    """
    import mss
    import pyautogui

    pyautogui.FAILSAFE = True  # fareyi ekran köşesine götürürsen durur (o ana kadarki kaydedilir)
    pyautogui.PAUSE = 0
    x, y, w, h = win
    with mss.mss() as sct:
        def grab(r):
            shot = sct.grab({"left": r[0], "top": r[1], "width": r[2], "height": r[3]})
            return Image.frombytes("RGB", shot.size, shot.rgb)

        # 1) Kaydırılan alanı bul: pencereyi azıcık kaydır, değişen bölge = kaydırılan alan.
        rect = None
        for fx in (0.6, 0.5, 0.75, 0.4):
            pyautogui.moveTo(x + int(w * fx), y + h // 2)
            time.sleep(0.1)
            for sign in (1, -1):
                a = grab(win)
                pyautogui.scroll(sign * 4)
                time.sleep(delay)
                b = grab(win)
                box = detect_scroll_region(a, b)
                if box:
                    scale = a.width / w
                    rect = (x + int(box[0] / scale), y + int(box[1] / scale),
                            int((box[2] - box[0]) / scale), int((box[3] - box[1]) / scale))
                    break
            if rect:
                break
        if rect is None:
            raise CaptureError("Kaydırılabilir alan bulunamadı. Pencerede kaydırılan bir içerik var mı?")
        rx, ry, rw, rh = rect
        # Fare, kaydırılan alanın alt kısmında dursun: yukarı giderken hover araç çubuğu şeritlere girmesin.
        pyautogui.moveTo(rx + int(rw * 0.7), ry + int(rh * 0.85))
        time.sleep(0.1)

        # 2) En alta git (zaten altaysa hemen biter).
        prev = None
        for _ in range(150):
            pyautogui.scroll(-40)
            time.sleep(0.25)
            g = to_gray(grab(rect))
            if prev is not None and np.abs(g - prev).mean() < 0.3:
                break
            prev = g

        # 3) Yukarı doğru yakala.
        st = Stitcher("up")
        st.add(grab(rect))
        amount, fails, frames = 4, 0, 1
        stop = "max"
        log = [f"pencere={win} alan={rect} ölçek={scale:.1f}"]
        try:
            while frames < max_frames:
                pyautogui.scroll(amount)
                time.sleep(delay)
                res = st.add(grab(rect))
                log.append(f"kaydır={amount} sonuç={res} kayma={st.last_shift} tanı={st.diag}")
                if res == "end":  # tembel yükleme olabilir: bekle, bir daha dene
                    time.sleep(1.5)
                    pyautogui.scroll(amount)
                    time.sleep(delay)
                    res = st.add(grab(rect))
                    log.append(f"  yeniden: sonuç={res}")
                    if res == "end":
                        stop = "end"
                        break
                if res == "fail":
                    fails += 1
                    pyautogui.scroll(-amount)
                    time.sleep(delay)
                    if amount == 1 or fails >= 4:
                        stop = "fail"
                        break
                    amount = max(1, amount // 2)
                    continue
                fails = 0
                frames += 1
                if st.last_shift > 0:  # kayma ~ yarım kare olacak şekilde miktarı ayarla
                    amount = max(1, min(60, round(amount * 0.45 * st.strips[-1].height / st.last_shift)))
                if progress:
                    progress(frames, st.height())
        except pyautogui.FailSafeException:
            stop = "failsafe"
        info = {"frames": frames, "stop": stop, "log": log}
        return st.result(), scale, info


def export_image(img, fmt, path, scale=2.0):
    """img'yi png/jpg/pdf olarak path'e yazar. Çok uzun PDF birkaç sayfaya bölünür."""
    if fmt == "png":
        img.save(path, dpi=(72 * scale, 72 * scale))
    elif fmt == "jpg":
        if img.height > 65000:
            raise CaptureError("JPG en fazla 65000 px yüksekliği destekler; PNG veya PDF seç.")
        img.convert("RGB").save(path, quality=92)
    elif fmt == "pdf":
        chunk = int(PDF_MAX_PT * scale)
        pages = [img.crop((0, t, img.width, min(t + chunk, img.height))).convert("RGB")
                 for t in range(0, img.height, chunk)]
        pages[0].save(path, "PDF", resolution=72 * scale, save_all=True, append_images=pages[1:])
    else:
        raise ValueError(fmt)
    return path
