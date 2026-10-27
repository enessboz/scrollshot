"""ScrollShot giriş noktası. Kullanıcı klasöründe daha yeni kod varsa onu, yoksa uygulamanın içindekini çalıştırır."""
import importlib.util
import os
import re
import shutil
import sys
import subprocess
import traceback
import datetime

import _version as bundled


def _home():
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "ScrollShot")
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ScrollShot")
    return os.path.join(os.path.expanduser("~"), ".config", "ScrollShot")


HOME = os.environ.get("SCROLLSHOT_HOME") or _home()
CODE = os.path.join(HOME, "code")
NAMES = ("_version", "scrollshot", "updater", "app")


def _build_of(path):
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r"^BUILD\s*=\s*(\d+)", f.read(), re.M)
        return int(m.group(1)) if m else -1
    except OSError:
        return -1


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _mark_bad(build):
    try:
        os.makedirs(HOME, exist_ok=True)
        with open(os.path.join(HOME, "bad_build"), "w") as f:
            f.write(str(build))
    except OSError:
        pass


def main():
    newer = _build_of(os.path.join(CODE, "_version.py")) > bundled.BUILD
    if newer:
        build = _build_of(os.path.join(CODE, "_version.py"))
        try:
            for n in NAMES:
                _load(n, os.path.join(CODE, n + ".py"))
        except Exception:
            traceback.print_exc()
            shutil.rmtree(CODE, ignore_errors=True)
            _mark_bad(build)
            for n in NAMES:
                sys.modules.pop(n, None)
            newer = False
        else:
            try:
                sys.modules["app"].main()
            except Exception:
                traceback.print_exc()
                shutil.rmtree(CODE, ignore_errors=True)
                _mark_bad(build)
                raise
            return
    import app  # uygulamanın içindeki sürüm
    app.main()


LOG = os.path.join(os.path.expanduser("~"), "Library", "Logs", "ScrollShot.log")


def _log(text):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except OSError:
        pass


if __name__ == "__main__":
    if getattr(sys, "frozen", False) and sys.platform == "darwin":
        try:  # pencere yok: hata çıktıları günlüğe gitsin
            sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)
        except OSError:
            pass
    _log(f"başlıyor (sürüm {bundled.BUILD})")
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        err = traceback.format_exc()
        _log(err)
        short = err.strip().splitlines()[-1].replace('"', "'").replace("\\", " ")[:200]
        try:
            subprocess.run(["osascript", "-e",
                            f'display dialog "ScrollShot başlatılamadı.\n\n{short}\n\nAyrıntı: ~/Library/Logs/ScrollShot.log" '
                            'buttons {"Tamam"} with icon stop'], timeout=60)
        except Exception:
            pass
        raise
