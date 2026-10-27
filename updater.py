"""Otomatik güncelleme: GitHub'daki son sürümden code.zip'i indirip kullanıcı klasörüne yazar."""
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import urllib.request
import zipfile
import io

import _version

REPO = "enessboz/scrollshot"
API = os.environ.get("SCROLLSHOT_API", f"https://api.github.com/repos/{REPO}/releases/latest")
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"


def _home():
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "ScrollShot")
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ScrollShot")
    return os.path.join(os.path.expanduser("~"), ".config", "ScrollShot")


HOME = os.environ.get("SCROLLSHOT_HOME") or _home()
CODE = os.path.join(HOME, "code")
NEEDED = {"app.py", "scrollshot.py", "updater.py", "_version.py"}


class NeedsFull(Exception):
    """Yeni sürüm .app kabuğunun yenilenmesini gerektiriyor."""


def _open(url, timeout):
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "ScrollShot-updater",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _bad_build():
    try:
        with open(os.path.join(HOME, "bad_build")) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return -1


def latest():
    """(build, code_zip_url) ya da None."""
    with _open(API, 6) as r:
        rel = json.load(r)
    m = re.search(r"(\d+)$", rel.get("tag_name", ""))
    url = next((a["browser_download_url"] for a in rel.get("assets", []) if a["name"] == "code.zip"), None)
    if not m or not url:
        return None
    return int(m.group(1)), url


def _int_var(text, name):
    m = re.search(rf"^{name}\s*=\s*(\d+)", text, re.M)
    return int(m.group(1)) if m else None


def install(data):
    """code.zip baytlarını doğrulayıp CODE klasörüne yazar."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        if not NEEDED <= names or any("/" in n or n.startswith(".") for n in names):
            raise ValueError("code.zip beklenen içerikte değil")
        files = {n: z.read(n) for n in NEEDED}
    shell = _int_var(files["_version.py"].decode("utf-8"), "SHELL")
    if shell is None or shell > _version.SHELL:
        raise NeedsFull()
    new, old = CODE + ".new", CODE + ".old"
    shutil.rmtree(new, ignore_errors=True)
    os.makedirs(new)
    for n, b in files.items():
        with open(os.path.join(new, n), "wb") as f:
            f.write(b)
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(CODE):
        os.rename(CODE, old)
    os.rename(new, CODE)
    shutil.rmtree(old, ignore_errors=True)


def relaunch():
    if not getattr(sys, "frozen", False):
        return
    exe = sys.executable
    if sys.platform == "darwin" and ".app/Contents/MacOS/" in exe:
        subprocess.Popen(["/usr/bin/open", "-n", exe.split("/Contents/MacOS/")[0]])
    else:
        subprocess.Popen([exe])
    sys.exit(0)


def auto_update():
    """None = güncelleme yok/başarısız (sessiz), ('updated', build) = kuruldu (yeniden başlatır), ('full', url) = elle indirme lazım."""
    try:
        rel = latest()
        if rel is None or rel[0] <= _version.BUILD or rel[0] <= _bad_build():
            return None
        with _open(rel[1], 30) as r:
            data = r.read()
        install(data)
    except NeedsFull:
        return ("full", RELEASES_PAGE)
    except Exception:
        return None
    relaunch()
    return ("updated", rel[0])
