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


def export(entry, fmt):
    """Geçmişteki bir kaydı masaüstüne seçilen biçimde yazar; dosya yolunu döner."""
    master = os.path.join(LIB, entry["file"])
    base = f'{safe_name(entry["title"])} {entry["stamp"][:16]}'  # "Başlık 2026-10-27 18.40"
    path = os.path.join(DESKTOP, f"{base}.{fmt}")
    n = 2
    while os.path.exists(path):
        path = os.path.join(DESKTOP, f"{base} ({n}).{fmt}")
        n += 1
    if fmt == "png":
        shutil.copyfile(master, path)
    else:
        with Image.open(master) as im:
            ss.export_image(im.convert("RGB"), fmt, path, entry.get("scale", 2.0))
    return path


def main():
    if sys.platform != "darwin":
        sys.exit("ScrollShot şimdilik yalnız macOS'ta çalışır.")
    update_result = updater.auto_update()  # yeni sürüm varsa kurar ve yeniden açar

    import AppKit
    import Quartz
    import rumps
    from AppKit import NSApplication, NSEvent, NSWorkspace
    from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

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
                rumps.notification("ScrollShot", "Yeni sürüm var",
                                   "Uygulamayı bir kez yeniden indirmen gerekiyor: " + update_result[1])
            else:
                rumps.notification("ScrollShot", f"Menü çubuğunda ({HOTKEY_LABEL})",
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
                rumps.notification("ScrollShot", "Yakalanamadı", job["error"])
                return
            self.after_capture(job["entry"])

        # ---- yakalama ----
        def trigger(self):
            if self.job:
                return
            if not AXIsProcessTrusted():
                rumps.notification("ScrollShot", "İzin gerekli",
                                   "Erişilebilirlik iznini ver (Sistem Ayarları → Gizlilik ve Güvenlik).")
                return
            if not Quartz.CGPreflightScreenCaptureAccess():
                Quartz.CGRequestScreenCaptureAccess()
                rumps.notification("ScrollShot", "İzin gerekli", "Ekran Kaydı iznini ver, sonra ScrollShot'ı kapatıp aç.")
                return
            win = front_window()
            if not win:
                rumps.notification("ScrollShot", "Pencere bulunamadı", "Yakalanacak pencereyi öne alıp tekrar dene.")
                return
            rumps.notification("ScrollShot", win["title"], "Yakalanıyor. Bitene kadar fareye dokunma.")
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
                    img, scale = ss.capture_window(win["rect"], progress)
                    job["entry"] = store(img, scale, win["title"], win["app"])
                except Exception as e:
                    job["error"] = str(e) or type(e).__name__
                finally:
                    job["done"] = True

            threading.Thread(target=work, daemon=True).start()

        def after_capture(self, entry):
            self.refresh_history()
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            w, h = entry["size"]
            r = rumps.alert(title="Yakalandı",
                            message=f'{entry["title"]}\n{w}×{h} px\n\nHangi biçimde masaüstüne kaydedeyim?',
                            ok="PNG", cancel="Şimdilik sakla", other="PDF")
            if r == 1:
                self.export_and_show(entry, "png")
            elif r == -1:
                self.export_and_show(entry, "pdf")

        # ---- geçmiş ----
        def refresh_history(self):
            self.history_item.clear()
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
                rumps.notification("ScrollShot", "Kaydedilemedi", str(e))

    ScrollShot().run()


if __name__ == "__main__":
    main()
