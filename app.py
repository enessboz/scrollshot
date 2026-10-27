#!/usr/bin/env python3
"""ScrollShot uygulaması: terminalsiz, pencereli kullanım (çift tıkla aç)."""
import datetime
import os
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox

import _version
import scrollshot as ss
import updater

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")


def missing_permissions():
    """macOS izinleri; eksik olanlar için sistem istemini tetikler. Eksik izin adlarını döner."""
    if sys.platform != "darwin":
        return []
    missing = []
    try:
        import Quartz
        if not Quartz.CGPreflightScreenCaptureAccess():
            Quartz.CGRequestScreenCaptureAccess()
            missing.append("Ekran Kaydı")
    except Exception:
        pass
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}):
            missing.append("Erişilebilirlik")
    except Exception:
        pass
    return missing


def ask_options():
    root = tk.Tk()
    root.title("ScrollShot")
    root.geometry("380x350")
    root.resizable(False, False)
    out = {"ok": False}
    fmts = {"png": tk.BooleanVar(value=True), "pdf": tk.BooleanVar(value=False), "jpg": tk.BooleanVar(value=False)}
    direction = tk.StringVar(value="down")

    tk.Label(root, text="Kaydırmalı alanın tam görüntüsü", font=("Helvetica", 15, "bold")).pack(pady=(14, 4))
    tk.Label(root, text=f"sürüm {_version.BUILD}", fg="gray").pack()
    tk.Label(root, text="Başlat'a bas → kaydırılacak alanı fareyle seç.\nBaşlık ve yazma kutusunu seçme.",
             justify="center").pack()

    box = tk.LabelFrame(root, text="Çıktı biçimi (masaüstüne kaydedilir)")
    box.pack(fill="x", padx=18, pady=10)
    for key, label in (("png", "PNG (resim)"), ("pdf", "PDF"), ("jpg", "JPG (küçük dosya)")):
        tk.Checkbutton(box, text=label, variable=fmts[key]).pack(anchor="w", padx=8)

    box2 = tk.LabelFrame(root, text="Kaydırma yönü")
    box2.pack(fill="x", padx=18)
    tk.Radiobutton(box2, text="Yukarıdan aşağı", variable=direction, value="down").pack(anchor="w", padx=8)
    tk.Radiobutton(box2, text="Aşağıdan yukarı (Teams'te eski mesajlar)", variable=direction, value="up").pack(anchor="w", padx=8)

    def start():
        if not any(v.get() for v in fmts.values()):
            messagebox.showwarning("ScrollShot", "En az bir biçim seç.")
            return
        out["ok"] = True
        out["formats"] = [k for k, v in fmts.items() if v.get()]
        out["direction"] = direction.get()
        root.destroy()

    tk.Button(root, text="Başlat", command=start, width=18, height=2).pack(pady=14)
    root.mainloop()
    return out if out["ok"] else None


def notify(title, text, error=False):
    r = tk.Tk()
    r.withdraw()
    (messagebox.showerror if error else messagebox.showinfo)(title, text)
    r.destroy()


def main():
    res = updater.auto_update()  # yeni sürüm varsa kurar ve uygulamayı yeniden açar
    if res and res[0] == "full":
        notify("ScrollShot", "Yeni sürüm için uygulamayı bir kez yeniden indirmen gerekiyor:\n" + res[1])
    missing = missing_permissions()
    if missing:
        notify("ScrollShot: izin gerekli",
               "Sistem Ayarları → Gizlilik ve Güvenlik altında ScrollShot'a şu izin(ler)i ver:\n\n• "
               + "\n• ".join(missing)
               + "\n\nİzin verdikten sonra ScrollShot'ı kapatıp yeniden aç.")
        return
    opts = ask_options()
    if not opts:
        return
    region = ss.select_region()
    if not region:
        return
    time.sleep(1.0)  # seçim katmanı tamamen kapansın
    try:
        img, region_w = ss.capture(region, opts["direction"], 5, 0.8, 300)
        name = "scrollshot_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        paths = ss.save_outputs(img, region_w, opts["formats"], DESKTOP, name, 0)
    except Exception as e:  # kullanıcıya göster, sessiz çökme olmasın
        notify("ScrollShot hata", f"{type(e).__name__}: {e}", error=True)
        return
    if sys.platform == "darwin" and paths:
        subprocess.run(["open", "-R", paths[0]])
    notify("ScrollShot", "Hazır, masaüstüne kaydedildi:\n\n" + "\n".join(os.path.basename(p) for p in paths))


if __name__ == "__main__":
    main()
