"""ScrollShot Windows uygulaması: tepsi simgesi + genel kısayol (Ctrl+Alt+S).

Teams / Slack gibi Chromium tabanlı masaüstü uygulamalarında içeriden (CDP) tam sayfa yakalar; olmazsa görüntüden birleştirir.
"""
import datetime
import json
import os
import queue
import subprocess
import sys
import threading
import time

import _version
import app as common  # dosya adı, geçmiş, dışa aktarma mantığı (platformdan bağımsız)
import scrollshot as ss
import updater

HOTKEY_LABEL = "Ctrl+Alt+S"
APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
HOME = os.path.join(APPDATA, "ScrollShot")
LOG = os.path.join(HOME, "ScrollShot.log")
SETTINGS = os.path.join(HOME, "settings.json")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# CDP ile içeriden yakalanabilen uygulamalar: exe adı -> (ad, hata ayıklama portu)
CDP_APPS = {"ms-teams.exe": ("Teams", 9222), "slack.exe": ("Slack", 9223)}


def log(text):
    try:
        os.makedirs(HOME, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except OSError:
        pass


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(s):
    try:
        os.makedirs(HOME, exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(s, f)
    except OSError:
        pass


# ---------------- Windows yardımcıları ----------------

def set_dpi_aware():
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def known_folder(guid, fallback):
    import ctypes
    from ctypes import wintypes
    try:
        class GUID(ctypes.Structure):
            _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]
        g = GUID()
        ctypes.windll.ole32.CLSIDFromString(guid, ctypes.byref(g))
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
            path = p.value
            ctypes.windll.ole32.CoTaskMemFree(p)
            if path:
                return path
    except Exception:
        pass
    return fallback


def foreground():
    """Öndeki pencere: {'rect': (x, y, w, h) fiziksel piksel, 'title', 'exe': küçük harfli dosya adı, 'path'}."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return None
    buf = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(hwnd, buf, 512)
    title = buf.value
    rect = wintypes.RECT()
    try:  # görünür çerçeve (gölge/yeniden boyutlandırma kenarları hariç)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect))
    except Exception:
        u.GetWindowRect(hwnd, ctypes.byref(rect))
    if rect.right - rect.left < 50:
        u.GetWindowRect(hwnd, ctypes.byref(rect))
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    path = ""
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
    if h:
        n = wintypes.DWORD(1024)
        b = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(n)):
            path = b.value
        ctypes.windll.kernel32.CloseHandle(h)
    return {"rect": (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top), "title": title,
            "exe": os.path.basename(path).lower(), "path": path, "pid": pid.value}


def modifiers_down():
    import ctypes
    g = ctypes.windll.user32.GetAsyncKeyState
    return any(g(vk) & 0x8000 for vk in (0x11, 0x12, 0x10, 0x5B))  # Ctrl, Alt, Shift, Win


def hotkey_loop(q):
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    if not u.RegisterHotKey(None, 1, 0x2 | 0x1 | 0x4000, ord("S")):  # Ctrl+Alt+S
        q.put(("notify", f"{HOTKEY_LABEL} kısayolu başka bir program tarafından kullanılıyor. Tepsi menüsünden yakalayabilirsin."))
        return
    msg = wintypes.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:
            q.put(("capture",))


def single_instance():
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\ScrollShotSingleInstance")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "ScrollShot")
            return True
    except OSError:
        return False


def set_autostart(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "ScrollShot", 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(k, "ScrollShot")
            except OSError:
                pass


# ---------------- Teams / Slack'i yakalama modunda açma ----------------

def relaunch_in_capture_mode(name, port, exe_path):
    """Uygulamayı kapatıp hata ayıklama portuyla yeniden açar. Port açılırsa True."""
    flags = 0x08000000  # CREATE_NO_WINDOW
    exe = os.path.basename(exe_path)
    subprocess.run(["taskkill", "/F", "/T", "/IM", exe], capture_output=True, creationflags=flags)
    time.sleep(3)
    env = os.environ.copy()
    if name == "Teams":
        env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--remote-debugging-port={port}"
        alias = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "ms-teams.exe")
        subprocess.Popen([alias if os.path.exists(alias) else exe_path], env=env)
    else:
        subprocess.Popen([exe_path, f"--remote-debugging-port={port}"], env=env)
    import cdp
    for _ in range(60):
        time.sleep(1)
        if cdp.port_open(port):
            return True
    return False


# ---------------- Uygulama ----------------

def main():
    if sys.platform != "win32":
        sys.exit("Bu dosya Windows içindir.")
    update_result = updater.auto_update()  # yeni sürüm varsa kurar ve yeniden açar
    set_dpi_aware()
    if not single_instance():
        return

    import tkinter as tk
    from tkinter import messagebox

    import pystray
    from PIL import Image, ImageDraw

    import cdp

    common.LIB = os.path.join(known_folder("{33E28130-4E1E-4676-835A-98395C3BC3BB}", os.path.expanduser("~\\Pictures")), "ScrollShot")
    common.DESKTOP = known_folder("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", os.path.expanduser("~\\Desktop"))
    common.HIST = os.path.join(common.LIB, "history.json")
    settings = load_settings()
    q = queue.Queue()
    state = {"busy": False, "frames": 0}

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    # --- tepsi simgesi ---
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 14, 60, 54], 8, fill=(30, 110, 230, 255))
    d.rectangle([22, 8, 42, 16], fill=(30, 110, 230, 255))
    d.ellipse([20, 20, 44, 46], fill=(255, 255, 255, 255))
    d.ellipse([27, 27, 37, 39], fill=(30, 110, 230, 255))

    def notify(msg, title="ScrollShot"):
        try:
            icon.notify(msg, title)
        except Exception:
            log(f"bildirim: {title}: {msg}")

    menu = pystray.Menu(
        pystray.MenuItem(f"Öndeki pencereyi yakala   {HOTKEY_LABEL}", lambda: q.put(("capture",)), default=True),
        pystray.MenuItem("Geçmiş...", lambda: q.put(("history",))),
        pystray.MenuItem("Windows ile başlat", lambda: q.put(("autostart",)), checked=lambda _: autostart_enabled()),
        pystray.MenuItem("Günlüğü aç", lambda: os.startfile(LOG) if os.path.exists(LOG) else None),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(f"Sürüm {_version.BUILD}", lambda: None, enabled=False),
        pystray.MenuItem("Çıkış", lambda: q.put(("quit",))),
    )
    icon = pystray.Icon("ScrollShot", img, "ScrollShot", menu)
    icon.run_detached()
    threading.Thread(target=hotkey_loop, args=(q,), daemon=True).start()

    # --- pencereler ---
    def ask_format(entry, info):
        top = tk.Toplevel(root)
        top.title("ScrollShot")
        top.attributes("-topmost", True)
        top.resizable(False, False)
        tk.Label(top, text="Yakalandı", font=("Segoe UI", 13, "bold")).pack(padx=24, pady=(16, 2))
        tk.Label(top, text=entry["title"][:70], wraplength=360).pack(padx=24)
        w, h = entry["size"]
        tk.Label(top, text=f"{w}×{h} px  ·  {common.stop_text(info)}", fg="gray").pack(padx=24, pady=(2, 10))
        tk.Label(top, text="Hangi biçimde masaüstüne kaydedeyim?").pack()
        row = tk.Frame(top)
        row.pack(pady=14)

        def pick(fmt):
            top.destroy()
            if fmt:
                export_and_show(entry, fmt)
        for fmt, label in (("png", "PNG"), ("pdf", "PDF"), ("jpg", "JPG"), (None, "Şimdilik sakla")):
            tk.Button(row, text=label, width=12 if fmt is None else 7, command=lambda f=fmt: pick(f)).pack(side="left", padx=4)
        top.update_idletasks()
        top.geometry(f"+{(top.winfo_screenwidth() - top.winfo_width()) // 2}+{(top.winfo_screenheight() - top.winfo_height()) // 3}")
        top.focus_force()

    def export_and_show(entry, fmt):
        try:
            path = common.export(entry, fmt)
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception as e:
            log(f"dışa aktarma hatası: {e}")
            messagebox.showerror("ScrollShot", f"Kaydedilemedi: {e}")

    def history_window():
        items = common.load_history()[::-1]
        top = tk.Toplevel(root)
        top.title("ScrollShot geçmişi")
        top.attributes("-topmost", True)
        lb = tk.Listbox(top, width=70, height=14)
        lb.pack(padx=10, pady=10)
        for e in items:
            lb.insert("end", f'{e["stamp"][:16].replace(".", ":")}   {e["title"][:55]}')
        row = tk.Frame(top)
        row.pack(pady=(0, 10))

        def sel():
            i = lb.curselection()
            return items[i[0]] if i else None

        for fmt in ("png", "pdf", "jpg"):
            tk.Button(row, text=f"{fmt.upper()} olarak masaüstüne", command=lambda f=fmt: sel() and export_and_show(sel(), f)).pack(side="left", padx=4)
        tk.Button(row, text="Klasörde göster", command=lambda: sel() and subprocess.Popen(
            ["explorer", "/select,", os.path.normpath(os.path.join(common.LIB, sel()["file"]))])).pack(side="left", padx=4)

    # --- yakalama ---
    def finish(result):
        state["busy"] = False
        icon.title = "ScrollShot"
        if result.get("error"):
            log("yakalama hatası: " + result["error"])
            notify(result["error"], "Yakalanamadı")
            return
        ask_format(result["entry"], result["info"])

    def run_capture(fg, mode, app_name, port):
        result = {}
        try:
            def progress(n, _h):
                state["frames"] = n
                icon.title = f"ScrollShot: {n} kare"
            if mode == "cdp":
                ws, _t = cdp.pick_target(port, title_hint=fg["title"])
                img, scale, info = cdp.capture_target(ws, progress)
            else:
                while modifiers_down():
                    time.sleep(0.05)
                time.sleep(0.3)
                img, scale, info = ss.capture_window(fg["rect"], progress)
            result["entry"] = common.store(img, scale, fg["title"], app_name or fg["exe"])
            result["info"] = info
            log(f"yakalama [{mode}] '{fg['title']}': {common.stop_text(info)}")
            for line in info["log"][-60:]:
                log("   " + line)
        except Exception as e:
            import traceback
            log(traceback.format_exc())
            result["error"] = f"{type(e).__name__}: {e}"
        q.put(("done", result))

    def relaunch_worker(name, port, path):
        ok = relaunch_in_capture_mode(name, port, path)
        if ok:
            q.put(("notify", f"{name} yakalama modunda açıldı. Sohbeti açıp {HOTKEY_LABEL} tuşlarına tekrar bas."))
        else:
            settings.setdefault("no_cdp", []).append(name)
            save_settings(settings)
            q.put(("notify", f"{name} bu yolu kabul etmedi; görüntü yöntemiyle devam edeceğim."))
        q.put(("relaunched",))

    def on_capture():
        if state["busy"]:
            return
        fg = foreground()
        if not fg or fg["exe"] in ("", "scrollshot.exe"):
            notify("Yakalanacak pencereyi öne alıp tekrar dene.")
            return
        mode, app_name, port = "pixel", None, None
        if fg["exe"] in CDP_APPS and CDP_APPS[fg["exe"]][0] not in settings.get("no_cdp", []):
            app_name, port = CDP_APPS[fg["exe"]]
            if cdp.port_open(port):
                mode = "cdp"
            elif messagebox.askyesno(
                    "ScrollShot",
                    f"{app_name}'i tam sayfa yakalayabilmem için yakalama modunda yeniden başlatmam gerekiyor.\n\n"
                    f"{app_name} kapanıp açılacak (açık toplantı/arama varsa kesilir). Bu bir kereliğine; kapatıp açana kadar geçerli.\n\n"
                    "Şimdi yeniden başlatayım mı?\n(Hayır dersen görüntüden birleştirme yöntemini kullanırım.)"):
                state["busy"] = True
                notify(f"{app_name} yeniden başlatılıyor, bekle...")
                threading.Thread(target=relaunch_worker, args=(app_name, port, fg["path"]), daemon=True).start()
                return
        state["busy"] = True
        notify(f"{fg['title'][:60]}: yakalanıyor" + (" (fareye dokunma)" if mode == "pixel" else ""))
        threading.Thread(target=run_capture, args=(fg, mode, app_name, port), daemon=True).start()

    def pump():
        try:
            while True:
                ev = q.get_nowait()
                kind = ev[0]
                if kind == "capture":
                    on_capture()
                elif kind == "done":
                    finish(ev[1])
                elif kind == "relaunched":
                    state["busy"] = False
                elif kind == "notify":
                    notify(ev[1])
                elif kind == "history":
                    history_window()
                elif kind == "autostart":
                    set_autostart(not autostart_enabled())
                elif kind == "quit":
                    icon.stop()
                    root.destroy()
                    return
        except queue.Empty:
            pass
        root.after(150, pump)

    if update_result and update_result[0] == "full":
        q.put(("notify", "Yeni sürüm için uygulamayı yeniden indirmen gerekiyor: " + update_result[1]))
    else:
        q.put(("notify", f"Tepsi simgesinde. Bir pencerede {HOTKEY_LABEL} tuşlarına bas."))
    log(f"başladı (sürüm {_version.BUILD})")
    root.after(150, pump)
    root.mainloop()


if __name__ == "__main__":
    main()
