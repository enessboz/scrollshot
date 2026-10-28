"""ScrollShot menü çubuğu uygulaması (macOS): kısayola bas, öndeki pencere otomatik yakalansın."""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from functools import partial

from PIL import Image

import _version
import scrollshot as ss
import updater

LIB = os.path.join(os.path.expanduser("~"), "Pictures", "ScrollShot")  # ana kopyalar + geçmiş
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
HIST = os.path.join(LIB, "history.json")
HOTKEY_LABEL = "⌃⌥S"  # Control + Option + S
KEYCODE_S = 1
MAX_MENU = 15


def safe_name(title):
    t = re.sub(r"\s*\|\s*Microsoft Teams\s*$", "", title or "")
    t = re.sub(r'[\\/:|*?"<>]+', " - ", t)
    t = re.sub(r"\s+", " ", t).strip(" .-")
    return (t or "ScrollShot")[:80]


STOP_TEXT = {"end": "en üste kadar gidildi", "fail": "DURDU: kareler eşleşmedi (üst kısım eksik olabilir)",
             "max": "DURDU: kare sınırına ulaşıldı", "failsafe": "elle durduruldu"}


def stop_text(info):
    if not info:
        return ""
    secs = f' · {info["seconds"]:.0f} sn' if info.get("seconds") else ""
    note = f' · {info["note"]}' if info.get("note") else ""
    return f'{info["frames"]} kare, {STOP_TEXT.get(info["stop"], info["stop"])}{secs}{note}'


def log_capture(title, info):
    """Ayrıntıyı ~/Library/Logs/ScrollShot.log dosyasına yazar (sorun gidermek için)."""
    try:
        path = os.path.join(os.path.expanduser("~"), "Library", "Logs", "ScrollShot.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] yakalama '{title}': {stop_text(info)}\n")
            for line in info["log"][-60:]:
                f.write("   " + line + "\n")
    except OSError:
        pass


def load_history():
    try:
        with open(HIST, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_history(items):
    os.makedirs(LIB, exist_ok=True)
    with open(HIST, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)


def store(img, scale, title, app_name):
    """Ana PNG'yi kütüphaneye yazar, geçmişe ekler."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H.%M.%S")
    fname = f"{stamp} {safe_name(title)}.png"
    os.makedirs(LIB, exist_ok=True)
    ss.export_image(img, "png", os.path.join(LIB, fname), scale)
    entry = {"file": fname, "title": title or app_name, "app": app_name, "stamp": stamp,
             "scale": scale, "size": [img.width, img.height]}
    items = load_history()
    items.append(entry)
    save_history(items)
    return entry


def export(entry, fmt, dest=None):
    """Geçmişteki bir kaydı seçilen klasöre (varsayılan: DESKTOP) seçilen biçimde yazar; dosya yolunu döner."""
    DEST = dest or DESKTOP
    master = os.path.join(LIB, entry["file"])
    base = f'{safe_name(entry["title"])} {entry["stamp"][:16]}'  # "Başlık 2026-10-27 18.40"
    os.makedirs(DEST, exist_ok=True)
    path = os.path.join(DEST, f"{base}.{fmt}")
    n = 2
    while os.path.exists(path):
        path = os.path.join(DEST, f"{base} ({n}).{fmt}")
        n += 1
    if fmt == "png":
        shutil.copyfile(master, path)
    else:
        with Image.open(master) as im:
            ss.export_image(im.convert("RGB"), fmt, path, entry.get("scale", 2.0))
    return path


def export_as(entry, path):
    """Kaydı tam verilen dosya yoluna yazar; biçim uzantıdan anlaşılır (png/jpg/pdf)."""
    fmt = os.path.splitext(path)[1].lower().lstrip(".").replace("jpeg", "jpg")
    if fmt not in ("png", "jpg", "pdf"):
        raise ValueError("Uzantı .png, .jpg veya .pdf olmalı")
    master = os.path.join(LIB, entry["file"])
    if fmt == "png":
        shutil.copyfile(master, path)
    else:
        with Image.open(master) as im:
            ss.export_image(im.convert("RGB"), fmt, path, entry.get("scale", 2.0))
    return path


def delete_entry(entry):
    """Geçmişten ve diskten (ana PNG) siler."""
    try:
        os.remove(os.path.join(LIB, entry["file"]))
    except OSError:
        pass
    save_history([e for e in load_history() if e["file"] != entry["file"]])


def import_file(path):
    """Dışarıdaki bir resmi (png/jpg/webp/bmp) ya da PDF'i geçmişe ekler; yeni kaydı döner.
    PDF için PyMuPDF gerekir (yoksa ValueError)."""
    name = os.path.splitext(os.path.basename(path))[0]
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        try:
            import pymupdf as fitz
        except ImportError:
            try:
                import fitz
            except ImportError:
                raise ValueError("PDF içe aktarma bu kurulumda yok; yeni sürümü yeniden indirip kur.")
        pages = []
        with fitz.open(path) as doc:
            for pg in doc:
                pix = pg.get_pixmap(matrix=fitz.Matrix(2, 2))
                pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        if not pages:
            raise ValueError("PDF boş")
        w = max(p.width for p in pages)
        img = Image.new("RGB", (w, sum(p.height for p in pages)), "white")
        y = 0
        for p in pages:
            img.paste(p, (0, y))
            y += p.height
        scale = 2.0
    elif ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        with Image.open(path) as im:
            img = im.convert("RGB")
        scale = 1.0
    else:
        raise ValueError(f"Desteklenmeyen dosya türü: {ext or '?'}")
    return store(img, scale, name, "İçe aktarılan")


def main():
    if sys.platform != "darwin":
        sys.exit("ScrollShot şimdilik yalnız macOS'ta çalışır.")
    update_result = updater.auto_update()  # yeni sürüm varsa kurar ve yeniden açar

    import AppKit
    import Quartz
    import rumps
    from AppKit import NSApplication, NSEvent, NSWorkspace
    from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

    def notify(title, subtitle, message):
        """Bildirim hata verse bile uygulama çökmesin; olmazsa osascript'e düş."""
        try:
            rumps.notification(title, subtitle, message)
        except Exception:
            esc = lambda t: str(t).replace("\\", " ").replace('"', "'")
            try:
                subprocess.run(["osascript", "-e",
                                f'display notification "{esc(message)}" with title "{esc(title)}" subtitle "{esc(subtitle)}"'],
                               timeout=5)
            except Exception:
                pass

    key_mask = getattr(AppKit, "NSEventMaskKeyDown", 1 << 10)
    ctrl = getattr(AppKit, "NSEventModifierFlagControl", 1 << 18)
    opt = getattr(AppKit, "NSEventModifierFlagOption", 1 << 19)
    cmd = getattr(AppKit, "NSEventModifierFlagCommand", 1 << 20)
    shift = getattr(AppKit, "NSEventModifierFlagShift", 1 << 17)

    def front_window():
        """Öndeki uygulamanın en büyük normal penceresi: {'rect': (x,y,w,h), 'title', 'app'} ya da None."""
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None or app.processIdentifier() == os.getpid():
            return None
        wins = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID)
        best = None
        for w in wins:
            if w.get("kCGWindowOwnerPID") != app.processIdentifier() or w.get("kCGWindowLayer") != 0:
                continue
            b = w["kCGWindowBounds"]
            area = b["Width"] * b["Height"]
            if b["Width"] > 200 and b["Height"] > 200 and (best is None or area > best[0]):
                best = (area, w)
        if best is None:
            return None
        w = best[1]
        b = w["kCGWindowBounds"]
        return {"rect": (int(b["X"]), int(b["Y"]), int(b["Width"]), int(b["Height"])),
                "title": w.get("kCGWindowName") or app.localizedName(), "app": app.localizedName()}

    class ScrollShot(rumps.App):
        def __init__(self):
            super().__init__("ScrollShot", title="📸", quit_button="Çıkış")
            self.job = None
            self.monitor = None
            self.history_item = rumps.MenuItem("Geçmiş")
            self.menu = [
                rumps.MenuItem(f"Öndeki pencereyi yakala   {HOTKEY_LABEL}", callback=lambda _: self.trigger()),
                self.history_item,
                None,
                rumps.MenuItem(f"Sürüm {_version.BUILD}"),
            ]
            self.refresh_history()
            if not Quartz.CGPreflightScreenCaptureAccess():
                Quartz.CGRequestScreenCaptureAccess()
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
            if update_result and update_result[0] == "full":
                notify("ScrollShot", "Yeni sürüm var",
                                   "Uygulamayı bir kez yeniden indirmen gerekiyor: " + update_result[1])
            else:
                notify("ScrollShot", f"Menü çubuğunda ({HOTKEY_LABEL})",
                                   f"Bir pencerede {HOTKEY_LABEL} tuşlarına bas, tam sayfa yakalasın.")

        # ---- kısayol ----
        def install_monitor(self):
            def handler(event):
                f = event.modifierFlags()
                if event.keyCode() == KEYCODE_S and f & ctrl and f & opt and not f & (cmd | shift):
                    self.trigger()
            self.monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(key_mask, handler)

        @rumps.timer(0.5)
        def tick(self, _):
            if self.monitor is None and AXIsProcessTrusted():
                self.install_monitor()
            job = self.job
            if not job:
                return
            if not job["done"]:
                self.title = f"📸 {job['frames']}"
                return
            self.job, self.title = None, "📸"
            if job["error"]:
                notify("ScrollShot", "Yakalanamadı", job["error"])
                return
            self.after_capture(job["entry"], job.get("info"))

        # ---- yakalama ----
        def trigger(self):
            if self.job:
                return
            if not AXIsProcessTrusted():
                notify("ScrollShot", "İzin gerekli",
                                   "Erişilebilirlik iznini ver (Sistem Ayarları → Gizlilik ve Güvenlik).")
                return
            if not Quartz.CGPreflightScreenCaptureAccess():
                Quartz.CGRequestScreenCaptureAccess()
                notify("ScrollShot", "İzin gerekli", "Ekran Kaydı iznini ver, sonra ScrollShot'ı kapatıp aç.")
                return
            win = front_window()
            if not win:
                notify("ScrollShot", "Pencere bulunamadı", "Yakalanacak pencereyi öne alıp tekrar dene.")
                return
            notify("ScrollShot", win["title"], "Yakalanıyor. Bitene kadar fareye dokunma.")
            job = {"frames": 0, "done": False, "error": None, "entry": None}
            self.job = job

            def progress(n, _h):
                job["frames"] = n

            def work():
                try:
                    deadline = time.time() + 3  # kısayol tuşları bırakılsın (Ctrl+kaydırma = yakınlaştırma olur)
                    while NSEvent.modifierFlags() & (ctrl | opt | cmd | shift) and time.time() < deadline:
                        time.sleep(0.05)
                    time.sleep(0.3)
                    img, scale, info = ss.capture_window(win["rect"], progress)
                    job["entry"] = store(img, scale, win["title"], win["app"])
                    job["info"] = info
                    log_capture(win["title"], info)
                except Exception as e:
                    job["error"] = str(e) or type(e).__name__
                finally:
                    job["done"] = True

            threading.Thread(target=work, daemon=True).start()

        def after_capture(self, entry, info=None):
            self.refresh_history()
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            w, h = entry["size"]
            r = rumps.alert(title="Yakalandı",
                            message=f'{entry["title"]}\n{w}×{h} px  ·  {stop_text(info)}\n\nHangi biçimde masaüstüne kaydedeyim?',
                            ok="PNG", cancel="Şimdilik sakla", other="PDF")
            if r == 1:
                self.export_and_show(entry, "png")
            elif r == -1:
                self.export_and_show(entry, "pdf")

        # ---- geçmiş ----
        def refresh_history(self):
            try:
                self.history_item.clear()
            except AttributeError:  # alt menü henüz oluşmadıysa (ilk kurulum) temizlenecek bir şey yok
                pass
            items = load_history()[-MAX_MENU:][::-1]
            if not items:
                self.history_item.add(rumps.MenuItem("(henüz yok)"))
                return
            for e in items:
                parent = rumps.MenuItem(f'{e["stamp"][5:16].replace(".", ":")}  {e["title"][:45]}')
                for fmt in ("png", "pdf", "jpg"):
                    parent.add(rumps.MenuItem(f"{fmt.upper()} olarak masaüstüne",
                                              callback=partial(self.on_export, e, fmt)))
                parent.add(rumps.MenuItem("Finder'da göster", callback=partial(self.on_reveal, e)))
                self.history_item.add(parent)

        def on_export(self, entry, fmt, _sender):
            self.export_and_show(entry, fmt)

        def on_reveal(self, entry, _sender):
            subprocess.run(["open", "-R", os.path.join(LIB, entry["file"])])

        def export_and_show(self, entry, fmt):
            try:
                subprocess.run(["open", "-R", export(entry, fmt)])
            except Exception as e:
                notify("ScrollShot", "Kaydedilemedi", str(e))

    ScrollShot().run()


if __name__ == "__main__":
    main()
