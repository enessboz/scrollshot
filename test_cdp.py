"""CDP yakalama çekirdeğini gerçek bir Chromium'a karşı test eder (Teams/Slack'in kullandığı motor).

Sayfa: sohbet benzeri kayan liste; en altta açılır, yukarı kaydırınca eski mesajlar tembel yüklenir (başa eklenir),
listenin üstünde yapışkan 'tarih etiketi', köşede sabit düğme var. Beklenen: tüm liste, etiketsiz, kusursuz birleşmiş.
Chrome bulunamazsa atlanır. Çalıştır: python test_cdp.py
"""
import base64
import glob
import http.server
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp  # noqa: E402

N = 120
CSS = """
body{margin:0;font:16px sans-serif;background:#fff}
#wrap{position:absolute;left:50px;top:40px}
#list{width:600px;height:600px;overflow-y:auto;scrollbar-width:none;border:0}
.full #list{height:auto;overflow:visible}
.full #fab,.full #bar{display:none}
.msg{box-sizing:border-box;margin:0;padding:8px 12px;border-bottom:1px solid #ccc}
.pill{position:sticky;top:0;height:24px;margin-bottom:-24px;z-index:2;background:#555;color:#fff;width:120px;text-align:center}
#fab{position:fixed;right:200px;bottom:120px;width:40px;height:40px;background:#c00;z-index:5}
#bar{position:fixed;left:0;right:0;bottom:0;height:30px;background:#06c}
"""
JS = """
const N=%d, FULL=%s;
const list=document.getElementById('list');
function mk(i){const d=document.createElement('div');d.className='msg';d.style.height=(40+(i*37%%60))+'px';
 d.style.background='hsl('+(i*53%%360)+',70%%92%%)';d.textContent='Mesaj '+i+' - '+'x'.repeat(i%%40);\n d.setAttribute('data-tid','chat-pane-message');\n const t=document.createElement('time');t.setAttribute('datetime',new Date(Date.UTC(2026,0,1,i)).toISOString());t.style.display='none';d.appendChild(t);return d;}
let first=FULL?0:N-30;
for(let i=first;i<N;i++) list.appendChild(mk(i));
if(!FULL){
  const p=document.createElement('div');p.className='pill';p.textContent='Dun';list.prepend(p);
  list.scrollTop=list.scrollHeight;
  list.addEventListener('scroll',()=>{
    if(list.scrollTop<150&&first>0){const s=Math.max(0,first-30);const ref=p.nextSibling;
      for(let i=s;i<first;i++) list.insertBefore(mk(i),ref);
      first=s;}
  });
}
"""


def page(full):
    return (f"<!doctype html><html class='{'full' if full else ''}'><head><meta charset=utf-8><style>{CSS}</style></head>"
            f"<body><div id=wrap><div id=list></div></div><div id=fab></div><div id=bar></div>"
            f"<script>{JS % (N, 'true' if full else 'false')}</script></body></html>")


def find_chrome():
    cands = [os.environ.get("CHROME_PATH", "")]
    cands += glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")
    cands += [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
    cands += [shutil.which(x) or "" for x in ("google-chrome", "chromium", "chrome", "chromium-browser")]
    return next((c for c in cands if c and os.path.exists(c)), None)


def main():
    chrome = find_chrome()
    if not chrome:
        print("Chrome bulunamadı, CDP testi atlandı.")
        return

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = page("full=1" in self.path).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}/"
    port = 9339
    tmp = tempfile.mkdtemp()
    proc = subprocess.Popen([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", f"--remote-debugging-port={port}",
                             "--window-size=1000,1000", f"--user-data-dir={tmp}", f"--force-device-scale-factor={os.environ.get('DPR', '1')}", base],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if cdp.port_open(port):
                break
            time.sleep(0.1)
        else:
            raise SystemExit("Chrome hata ayıklama portu açılmadı")
        time.sleep(1.0)
        ws, title = cdp.pick_target(port)
        img, ppc, info = cdp.capture_target(ws)
        print(f"yakalama: {info['frames']} kare, {info['stop']}, {img.size}, ppc={ppc}")
        for line in info["log"][:40]:
            print("  ", line)

        # Gerçek: tüm mesajlar tek parça render edilmiş sayfadan
        c = cdp.CDP(ws)
        c.call("Page.navigate", url=base + "?full=1")
        time.sleep(1.0)
        r = c.eval("(()=>{const r=document.getElementById('list').getBoundingClientRect();"
                   "return {x:r.left,y:r.top,w:r.width,h:r.height}})()")
        shot = c.call("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                      clip={"x": r["x"], "y": r["y"], "width": r["w"], "height": r["h"], "scale": 1})
        truth = Image.open(io.BytesIO(base64.b64decode(shot["data"]))).convert("RGB")
        c.close()

        img.save("/tmp/stitched.png"); truth.save("/tmp/truth.png")
        assert info["stop"] == "end", f"üste kadar gidilmedi: {info['stop']}"
        full_info = c2 = None
        if not float(os.environ.get("DPR", "1")).is_integer():  # kesirli ölçek (%125): en fazla birkaç piksel yuvarlama farkı
            assert img.width == truth.width and abs(img.height - truth.height) <= 3, f"boyut {img.size} != {truth.size}"
            print("OK cdp (kesirli ölçek):", img.size, "gerçek:", truth.size)
            return
        assert img.size == truth.size, f"boyut {img.size} != {truth.size}"
        diff = np.abs(np.asarray(img, dtype=int) - np.asarray(truth, dtype=int))
        bad = int((diff.max(axis=2) > 8).sum())
        assert bad == 0, f"{bad} piksel farklı"
        print("OK cdp: tüm sohbet kusursuz yakalandı:", img.size)

        # Senaryo 2: tarih sınırı. 60. mesajdan itibaren (UTC 2026-01-01 + 60 saat)
        y60 = c_eval_y60 = None
        c = cdp.CDP(ws)
        y60 = c.eval("document.querySelectorAll('[data-tid]')[60].getBoundingClientRect().top - document.getElementById('list').getBoundingClientRect().top")
        c.call("Page.navigate", url=base)
        time.sleep(1.0)
        c.close()
        ws2, _t = cdp.pick_target(port)
        since = int(1767225600000 + 60 * 3600 * 1000)  # 2026-01-01T00:00Z + 60 saat
        img2, ppc2, info2 = cdp.capture_target(ws2, since_ms=since)
        print(f"tarih sınırı: {info2['frames']} kare, {info2['stop']}, {img2.size}, not={info2['note']!r}")
        exp = truth.crop((0, int(y60 * ppc2) - 4, truth.width, truth.height))
        assert info2["stop"] == "end", info2["stop"]
        assert img2.size == exp.size, f"sınırlı boyut {img2.size} != {exp.size}"
        bad2 = int((np.abs(np.asarray(img2, dtype=int) - np.asarray(exp, dtype=int)).max(axis=2) > 8).sum())
        assert bad2 == 0, f"sınırlı yakalamada {bad2} piksel farklı"
        print("OK cdp: tarih sınırı doğru başladı, kusursuz:", img2.size)
    finally:
        proc.terminate()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
