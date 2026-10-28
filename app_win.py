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
SINCE_CHOICES = [("Sınırsız", None), ("Son 1 gün", "days:1"), ("Son 3 gün", "days:3"), ("Son 7 gün", "days:7"),
                 ("Son 14 gün", "days:14"), ("Son 30 gün", "days:30")]
FORMAT_CHOICES = [("Her seferinde sor", "ask"), ("PNG", "png"), ("PDF", "pdf"), ("PNG + PDF", "png+pdf")]
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

WV2_POLICY = r"Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments"


def _wv2_policy(exe, args):
    """WebView2'nin kayıt defteri üzerinden ek bağımsız değişken yolu (ortam değişkeni işe yaramazsa). args=None siler."""
    import winreg
    try:
        if args is None:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WV2_POLICY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, exe)
        else:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, WV2_POLICY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, exe, 0, winreg.REG_SZ, args)
    except OSError as e:
        log(f"kayıt defteri ({'sil' if args is None else 'yaz'}): {e}")


def relaunch_in_capture_mode(name, port, exe_path):
    """Uygulamayı kapatıp hata ayıklama portuyla yeniden açar. Port açılırsa (yöntem adı) döner, açılmazsa None."""
    import cdp
    flags = 0x08000000  # CREATE_NO_WINDOW
    exe = os.path.basename(exe_path)
    args = f"--remote-debugging-port={port}"
    for via in (("env", "registry") if name == "Teams" else ("arg",)):
        log(f"{name}: yeniden başlatılıyor ({via})")
        subprocess.run(["taskkill", "/F", "/T", "/IM", exe], capture_output=True, creationflags=flags)
        time.sleep(3)
        env = os.environ.copy()
        try:
            if name == "Teams":
                alias = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "ms-teams.exe")
                target = alias if os.path.exists(alias) else exe_path
                if via == "env":
                    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = args
                else:
                    _wv2_policy(exe, args)
                subprocess.Popen([target], env=env)
            else:
                subprocess.Popen([exe_path, args], env=env)
            for _ in range(40):
                time.sleep(1)
                if cdp.port_open(port):
                    log(f"{name}: port {port} açıldı ({via})")
                    return via
        finally:
            if via == "registry":
                _wv2_policy(exe, None)
        log(f"{name}: port açılmadı ({via})")
    return None


# ---------------- Ayar yardımcıları ----------------

def since_ms(spec):
    """'days:N' (N gün öncesinin 00:00'ı) ya da 'date:YYYY-MM-DD' -> epoch ms; None = sınırsız."""
    if not spec:
        return None
    try:
        kind, val = spec.split(":", 1)
        if kind == "days":
            d = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=int(val))
        else:
            d = datetime.datetime.strptime(val, "%Y-%m-%d")
        return int(d.timestamp() * 1000)
    except (ValueError, OSError):
        return None


def since_label(spec):
    for label, v in SINCE_CHOICES:
        if v == spec:
            return label
    if spec and spec.startswith("date:"):
        try:
            return datetime.datetime.strptime(spec[5:], "%Y-%m-%d").strftime("%d.%m.%Y") + " ve sonrası"
        except ValueError:
            pass
    return "Sınırsız"


def pretty_stamp(stamp):
    """'2026-10-28 10.07.12' -> '28.10.2026 10:07'"""
    try:
        return datetime.datetime.strptime(stamp, "%Y-%m-%d %H.%M.%S").strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return stamp


# ---------------- Uygulama ----------------

def main():
    if sys.platform != "win32":
        sys.exit("Bu dosya Windows içindir.")
    update_result = updater.auto_update()  # yeni sürüm varsa kurar ve yeniden açar
    set_dpi_aware()
    if not single_instance():
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, f"ScrollShot zaten çalışıyor.\n\nTepsi simgesi gizli olabilir (görev çubuğunda ^ okuna tıkla). Kısayol: {HOTKEY_LABEL}", "ScrollShot", 0x40)
        return

    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk

    import pystray
    from PIL import Image, ImageDraw

    import cdp

    common.LIB = os.path.join(known_folder("{33E28130-4E1E-4676-835A-98395C3BC3BB}", os.path.expanduser("~\\Pictures")), "ScrollShot")
    default_dir = known_folder("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", os.path.expanduser("~\\Desktop"))
    common.HIST = os.path.join(common.LIB, "history.json")
    settings = load_settings()
    common.DESKTOP = settings.get("save_dir") or default_dir  # dışa aktarma hedef klasörü
    q = queue.Queue()
    state = {"busy": False, "frames": 0, "what": ""}

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
        log(f"bildirim: {title}: {msg}")
        try:
            icon.notify(msg, title)
        except Exception:
            pass

    def alert(msg, error=False):
        """Görünür kalsın diye gerçek pencere (tepsi bildirimleri Windows'ta sık gizleniyor)."""
        log(f"uyarı: {msg}")
        (messagebox.showerror if error else messagebox.showinfo)("ScrollShot", msg)

    status_win = {"w": None, "l": None}

    def show_status(text):
        if text is None:
            if status_win["w"]:
                status_win["w"].destroy()
                status_win["w"] = status_win["l"] = None
            return
        if status_win["w"] is None:
            w = tk.Toplevel(root)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            lab = tk.Label(w, text=text, bg="#1e6ee6", fg="white", font=("Segoe UI", 11), padx=16, pady=10)
            lab.pack()
            w.update_idletasks()
            w.geometry(f"+{w.winfo_screenwidth() - w.winfo_width() - 24}+{w.winfo_screenheight() - w.winfo_height() - 80}")
            status_win["w"], status_win["l"] = w, lab
        else:
            status_win["l"].config(text=text)

    def toast(text, secs=4000):
        show_status(text)
        root.after(secs, lambda: show_status(None) if not state["busy"] else None)

    def reveal(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    def export_and_show(entry, fmt):
        try:
            path = common.export(entry, fmt)
            reveal(path)
            return path
        except Exception as e:
            log(f"dışa aktarma hatası: {e}")
            messagebox.showerror("ScrollShot", f"Kaydedilemedi: {e}")

    # --- menü ---
    def set_setting(key, value):
        settings[key] = value
        save_settings(settings)
        if key == "save_dir":
            common.DESKTOP = value or default_dir
        icon.update_menu()

    def short_dir():
        p = common.DESKTOP
        return p if len(p) <= 38 else p[:15] + "…" + p[-22:]

    def radio_menu(choices, key, default):
        return pystray.Menu(*[
            pystray.MenuItem(label, (lambda v: lambda: q.put(("set", key, v)))(val),
                             checked=(lambda v: lambda item: settings.get(key, default) == v)(val), radio=True)
            for label, val in choices])

    since_menu_items = [
        pystray.MenuItem(label, (lambda v: lambda: q.put(("set", "since", v)))(val),
                         checked=(lambda v: lambda item: settings.get("since") == v)(val), radio=True)
        for label, val in SINCE_CHOICES]
    since_menu_items.append(pystray.MenuItem(
        "Belirli bir tarihten itibaren...", lambda: q.put(("custom_date",)),
        checked=lambda item: bool((settings.get("since") or "").startswith("date:")), radio=True))

    menu = pystray.Menu(
        pystray.MenuItem(f"Öndeki pencereyi yakala   {HOTKEY_LABEL}", lambda: q.put(("capture",)), default=True),
        pystray.MenuItem("Pano...", lambda: q.put(("panel",))),
        pystray.MenuItem(lambda item: f"Tarih sınırı: {since_label(settings.get('since'))}", pystray.Menu(*since_menu_items)),
        pystray.MenuItem(lambda item: f"Kaydetme klasörü: {short_dir()}", lambda: q.put(("pick_dir",))),
        pystray.MenuItem("Biçim", radio_menu(FORMAT_CHOICES, "format", "ask")),
        pystray.MenuItem("Dosya ekle...", lambda: q.put(("import",))),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Windows ile başlat", lambda: q.put(("autostart",)), checked=lambda _: autostart_enabled()),
        pystray.MenuItem("Günlüğü aç", lambda: os.startfile(LOG) if os.path.exists(LOG) else None),
        pystray.MenuItem(f"Sürüm {_version.BUILD}", lambda: None, enabled=False),
        pystray.MenuItem("Çıkış", lambda: q.put(("quit",))),
    )
    icon = pystray.Icon("ScrollShot", img, "ScrollShot", menu)
    icon.run_detached()
    threading.Thread(target=hotkey_loop, args=(q,), daemon=True).start()

    # --- ayar pencereleri ---
    def pick_dir():
        p = filedialog.askdirectory(title="Yakalamaların kaydedileceği klasör", initialdir=common.DESKTOP, parent=root)
        if p:
            set_setting("save_dir", os.path.normpath(p))
            toast("Kaydetme klasörü: " + short_dir())
            return True
        return False

    def custom_date():
        txt = simpledialog.askstring("Tarih sınırı", "Bu tarihten itibaren yakala (GG.AA.YYYY):", parent=root)
        if not txt:
            return
        try:
            dt = datetime.datetime.strptime(txt.strip(), "%d.%m.%Y")
        except ValueError:
            alert("Tarih anlaşılamadı. Örnek: 21.10.2026", error=True)
            return
        set_setting("since", "date:" + dt.strftime("%Y-%m-%d"))

    # --- yakalama sonrası: sor ya da otomatik kaydet ---
    def ask_format(entry, info):
        top = tk.Toplevel(root)
        top.title("ScrollShot")
        top.attributes("-topmost", True)
        top.resizable(False, False)
        tk.Label(top, text="Yakalandı", font=("Segoe UI", 13, "bold")).pack(padx=24, pady=(16, 2))
        tk.Label(top, text=entry["title"][:70], wraplength=360).pack(padx=24)
        w, h = entry["size"]
        tk.Label(top, text=f"{w}×{h} px  ·  {common.stop_text(info)}", fg="gray", wraplength=360).pack(padx=24, pady=(2, 10))
        tk.Label(top, text="Hangi biçimde kaydedeyim?\n" + short_dir()).pack()
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

    def after_capture(entry, info):
        fmt = settings.get("format", "ask")
        if fmt == "ask":
            ask_format(entry, info)
            return
        paths = []
        for f in fmt.split("+"):
            try:
                paths.append(common.export(entry, f))
            except Exception as e:
                log(f"otomatik kaydetme hatası ({f}): {e}")
                alert(f"Kaydedilemedi ({f}): {e}", error=True)
        if paths:
            toast("Kaydedildi: " + ", ".join(os.path.basename(p) for p in paths)[:90], 6000)
            reveal(paths[0])

    # --- pano ---
    panel = {"w": None}

    def panel_window():
        if panel["w"] is not None:
            try:
                panel["w"].deiconify()
                panel["w"].lift()
                return
            except tk.TclError:
                panel["w"] = None
        top = tk.Toplevel(root)
        panel["w"] = top
        top.title("ScrollShot Pano")
        top.geometry("900x520")
        top.protocol("WM_DELETE_WINDOW", lambda: (top.destroy(), panel.update(w=None)))
        info = tk.Label(top, anchor="w", fg="gray")
        info.pack(fill="x", padx=10, pady=(8, 0))
        cols = ("tarih", "baslik", "uygulama", "boyut")
        frame = tk.Frame(top)
        frame.pack(fill="both", expand=True, padx=10, pady=8)
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        for c, label, wd in (("tarih", "Tarih", 130), ("baslik", "Başlık", 470), ("uygulama", "Uygulama", 110), ("boyut", "Boyut", 100)):
            tree.heading(c, text=label)
            tree.column(c, width=wd, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        items = []

        def refresh():
            items[:] = common.load_history()[::-1]
            tree.delete(*tree.get_children())
            for i, e in enumerate(items):
                w, h = e["size"]
                tree.insert("", "end", iid=str(i), values=(pretty_stamp(e["stamp"]), e["title"], e.get("app", ""), f"{w}×{h}"))
            info.config(text=f"{len(items)} kayıt  ·  Kaydetme klasörü: {common.DESKTOP}")

        def selected():
            return [items[int(i)] for i in tree.selection()]

        def convert(fmt):
            sel = selected()
            if not sel:
                messagebox.showinfo("ScrollShot", "Önce listeden bir veya birkaç kayıt seç.", parent=top)
                return
            last = None
            for e in sel:
                try:
                    last = common.export(e, fmt)
                except Exception as err:
                    messagebox.showerror("ScrollShot", f"{e['title'][:40]}: {err}", parent=top)
            if last:
                toast(f"{len(sel)} kayıt {fmt.upper()} olarak kaydedildi")
                reveal(last)

        def save_as():
            sel = selected()
            if len(sel) != 1:
                messagebox.showinfo("ScrollShot", "Farklı kaydet için tek bir kayıt seç.", parent=top)
                return
            e = sel[0]
            p = filedialog.asksaveasfilename(parent=top, title="Farklı kaydet", initialdir=common.DESKTOP,
                                             initialfile=common.safe_name(e["title"]), defaultextension=".pdf",
                                             filetypes=[("PDF", "*.pdf"), ("PNG", "*.png"), ("JPG", "*.jpg")])
            if p:
                try:
                    reveal(common.export_as(e, p))
                except Exception as err:
                    messagebox.showerror("ScrollShot", str(err), parent=top)

        def open_sel(_=None):
            for e in selected()[:1]:
                os.startfile(os.path.join(common.LIB, e["file"]))

        def reveal_sel():
            for e in selected()[:1]:
                reveal(os.path.join(common.LIB, e["file"]))

        def delete_sel():
            sel = selected()
            if sel and messagebox.askyesno("ScrollShot", f"{len(sel)} kayıt silinsin mi?\n(Masaüstüne/klasöre daha önce kaydedilen dosyalar silinmez.)", parent=top):
                for e in sel:
                    common.delete_entry(e)
                refresh()

        def add_files():
            paths = filedialog.askopenfilenames(parent=top, title="Panoya dosya ekle",
                                                filetypes=[("Resim / PDF", "*.png *.jpg *.jpeg *.webp *.bmp *.pdf")])
            errs = []
            for p in paths:
                try:
                    common.import_file(p)
                except Exception as err:
                    errs.append(f"{os.path.basename(p)}: {err}")
            if errs:
                messagebox.showerror("ScrollShot", "\n".join(errs), parent=top)
            refresh()

        def change_dir():
            if pick_dir():
                refresh()

        row = tk.Frame(top)
        row.pack(fill="x", padx=10, pady=(0, 10))
        for label, fn in (("PNG'ye çevir", lambda: convert("png")), ("PDF'e çevir", lambda: convert("pdf")),
                          ("JPG'ye çevir", lambda: convert("jpg")), ("Farklı kaydet...", save_as)):
            tk.Button(row, text=label, command=fn).pack(side="left", padx=3)
        tk.Button(row, text="Dosya ekle...", command=add_files).pack(side="left", padx=(16, 3))
        tk.Button(row, text="Klasör değiştir...", command=change_dir).pack(side="left", padx=3)
        for label, fn in (("Sil", delete_sel), ("Klasörde göster", reveal_sel), ("Aç", open_sel)):
            tk.Button(row, text=label, command=fn).pack(side="right", padx=3)
        tree.bind("<Double-1>", open_sel)
        top.refresh = refresh
        refresh()

    def import_dialog():
        paths = filedialog.askopenfilenames(parent=root, title="Panoya dosya ekle",
                                            filetypes=[("Resim / PDF", "*.png *.jpg *.jpeg *.webp *.bmp *.pdf")])
        errs = []
        for p in paths:
            try:
                common.import_file(p)
            except Exception as err:
                errs.append(f"{os.path.basename(p)}: {err}")
        if errs:
            alert("\n".join(errs), error=True)
        elif paths:
            toast(f"{len(paths)} dosya panoya eklendi")
        panel_window()

    # --- yakalama ---
    def finish(result):
        state["busy"] = False
        show_status(None)
        icon.title = "ScrollShot"
        if result.get("error"):
            log("yakalama hatası: " + result["error"])
            alert("Yakalanamadı:\n\n" + result["error"] + f"\n\nAyrıntı: {LOG}", error=True)
            return
        after_capture(result["entry"], result["info"])
        if panel["w"] is not None:
            try:
                panel["w"].refresh()
            except Exception:
                pass

    def run_capture(fg, mode, app_name, port, since):
        result = {}
        try:
            def progress(n, _h):
                state["frames"] = n
                icon.title = f"ScrollShot: {n} kare"
                if mode == "cdp":
                    q.put(("status", f"ScrollShot: yakalanıyor, {n} kare…"))
            if mode == "cdp":
                ws, _t = cdp.pick_target(port, title_hint=fg["title"])
                img, scale, info = cdp.capture_target(ws, progress, since_ms=since)
            else:
                while modifiers_down():
                    time.sleep(0.05)
                time.sleep(0.3)
                img, scale, info = ss.capture_window(fg["rect"], progress)
                if since:
                    info["note"] = "tarih sınırı yalnız Teams/Slack içeriden modunda çalışır"
            result["entry"] = common.store(img, scale, fg["title"], app_name or fg["exe"])
            result["info"] = info
            log(f"yakalama [{mode}] '{fg['title']}': {common.stop_text(info)}")
            for line in info["log"][-60:]:
                log("   " + line)
        except Exception as e:
            import traceback
            log(traceback.format_exc())
            result["error"] = f"{type(e).__name__}: {e}"
            if mode == "cdp":
                try:
                    log("hedefler: " + json.dumps([t.get("title") for t in cdp.list_targets(port)], ensure_ascii=False))
                except Exception as e2:
                    log(f"hedefler okunamadı: {e2}")
        q.put(("done", result))

    def relaunch_worker(name, port, path):
        via = relaunch_in_capture_mode(name, port, path)
        if via:
            q.put(("alert", f"{name} yakalama modunda açıldı ({via}).\n\nSohbeti aç, sonra {HOTKEY_LABEL} tuşlarına bas."))
        else:
            settings.setdefault("no_cdp", []).append(name)
            save_settings(settings)
            q.put(("alert", f"{name} yakalama modunu kabul etmedi (iki yöntem de denendi).\n\nBundan sonra görüntüden birleştirme yöntemiyle devam edeceğim: {name}'te sohbeti aç, {HOTKEY_LABEL} tuşlarına bas ve yakalama bitene kadar fareye dokunma."))
        q.put(("relaunched",))

    def on_capture():
        if state["busy"]:
            alert(f"ScrollShot şu an meşgul: {state['what'] or 'bir işlem sürüyor'}.")
            return
        fg = foreground()
        log(f"kısayol: ön pencere exe={fg and fg['exe']} başlık={fg and fg['title']!r}")
        if not fg or fg["exe"] in ("", "scrollshot.exe"):
            alert("Yakalanacak pencere bulunamadı. Pencereyi öne alıp tekrar dene.")
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
                state["busy"], state["what"] = True, f"{app_name} yeniden başlatılıyor (1-2 dakika sürebilir)"
                show_status(f"ScrollShot: {app_name} yeniden başlatılıyor, bekle…")
                threading.Thread(target=relaunch_worker, args=(app_name, port, fg["path"]), daemon=True).start()
                return
        state["busy"], state["what"] = True, "yakalama sürüyor"
        since = since_ms(settings.get("since"))
        log(f"mod={mode} uygulama={app_name} tarih_siniri={settings.get('since')}")
        if mode == "cdp":
            show_status(f"ScrollShot: {app_name} yakalanıyor…" + (f" ({since_label(settings.get('since'))})" if since else ""))
        else:
            notify(f"{fg['title'][:60]}: yakalanıyor (fareye dokunma)")
        threading.Thread(target=run_capture, args=(fg, mode, app_name, port, since), daemon=True).start()

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
                    show_status(None)
                elif kind == "alert":
                    show_status(None)
                    alert(ev[1])
                elif kind == "status":
                    if state["busy"]:
                        show_status(ev[1])
                elif kind == "notify":
                    notify(ev[1])
                elif kind == "set":
                    set_setting(ev[1], ev[2])
                elif kind == "custom_date":
                    custom_date()
                elif kind == "pick_dir":
                    pick_dir()
                elif kind == "panel":
                    panel_window()
                elif kind == "import":
                    import_dialog()
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
        q.put(("alert", "Yeni sürüm için uygulamayı yeniden indirmen gerekiyor:\n" + update_result[1]))
    elif not settings.get("welcomed"):
        settings["welcomed"] = True
        save_settings(settings)
        q.put(("alert", f"ScrollShot çalışıyor.\n\nYakalamak istediğin pencerede {HOTKEY_LABEL} tuşlarına bas.\nTepsi simgesi görev çubuğunun sağında; gizliyse ^ okuna tıkla.\n\nTeams ve Slack'te ilk seferde yakalama modunda yeniden başlatmak için izin ister."))
    log(f"başladı (sürüm {_version.BUILD})")
    root.after(150, pump)
    root.mainloop()


if __name__ == "__main__":
    main()
