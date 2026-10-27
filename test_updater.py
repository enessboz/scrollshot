"""Güncelleyiciyi yerel sahte 'GitHub' ile uçtan uca test eder. Çalıştır: python test_updater.py"""
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import zipfile

tmp = tempfile.mkdtemp()
os.environ["SCROLLSHOT_HOME"] = os.path.join(tmp, "home")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def code_zip(build, shell):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n in ("app.py", "scrollshot.py", "updater.py"):
            z.writestr(n, "# stub\n")
        z.writestr("_version.py", f"BUILD = {build}\nSHELL = {shell}\n")
    return b.getvalue()


state = {}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/release":
            body = json.dumps({"tag_name": f"build-{state['build']}", "assets": [
                {"name": "code.zip", "browser_download_url": f"http://127.0.0.1:{self.server.server_port}/code.zip"}]}).encode()
        else:
            body = code_zip(state["build"], state["shell"])
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)


srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["SCROLLSHOT_API"] = f"http://127.0.0.1:{srv.server_port}/release"

import _version  # noqa: E402
import updater  # noqa: E402

# 1) yeni sürüm kurulur
state.update(build=5, shell=1)
assert updater.auto_update() == ("updated", 5)
assert open(os.path.join(updater.CODE, "_version.py")).read().startswith("BUILD = 5")
# 2) aynı sürüm: bir şey yapmaz
_version.BUILD = 5
assert updater.auto_update() is None
# 3) kabuk uyumsuz: tam indirme istenir, kod değişmez
state.update(build=6, shell=2)
r = updater.auto_update()
assert r and r[0] == "full", r
assert open(os.path.join(updater.CODE, "_version.py")).read().startswith("BUILD = 5")
# 4) kötü build işaretliyse atlanır
os.makedirs(updater.HOME, exist_ok=True)
open(os.path.join(updater.HOME, "bad_build"), "w").write("7")
state.update(build=7, shell=1)
assert updater.auto_update() is None
# 5) ağ yok: sessizce None
os.environ["SCROLLSHOT_API"] = "http://127.0.0.1:1/x"
updater.API = os.environ["SCROLLSHOT_API"]
assert updater.auto_update() is None
print("Güncelleyici testleri geçti.")
