"""Chrome DevTools Protocol ile kaydırmalı alan yakalama (WebView2 / Electron / Chromium uygulamaları: Teams, Slack...).

Eklentilerin yaptığı gibi içeriden çalışır: kayan alanı DOM'dan bulur, kaydırmayı kodla yapar (fare/ekran gerekmez),
her adımda alanın görüntüsünü alır, kareleri bilinen kaydırma miktarına göre birleştirir (görüntü eşleştirmesi yalnız doğrulama).
"""
import base64
import io
import json
import time
import urllib.request

from PIL import Image

import scrollshot as ss


class CDPError(Exception):
    pass


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # yerel adres: proxy kullanma


def list_targets(port, timeout=1.0):
    """Hata ayıklama portundaki sayfa hedefleri; port kapalıysa istisna fırlatır."""
    with _OPENER.open(f"http://127.0.0.1:{port}/json", timeout=timeout) as r:
        data = json.load(r)
    return [t for t in data if t.get("type") == "page" and t.get("webSocketDebuggerUrl")
            and not t.get("url", "").startswith("devtools://")]


def port_open(port):
    try:
        list_targets(port, 0.6)
        return True
    except Exception:
        return False


class CDP:
    def __init__(self, ws_url):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.n = 0

    def call(self, method, **params):
        self.n += 1
        i = self.n
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == i:
                if "error" in msg:
                    raise CDPError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise CDPError("JS hatası: " + json.dumps(r["exceptionDetails"])[:300])
        return r["result"].get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# Sayfadaki ana kaydırılan alanı bulur: en büyük görünür alan x kaydırılabilir içerik oranı. mark=true ise işaretler.
FIND_JS = """((mark) => {
  const vw = innerWidth, vh = innerHeight, doc = document.scrollingElement;
  let best = null, bestScore = 0;
  const consider = (el) => {
    const isDoc = el === doc;
    const ch = isDoc ? vh : el.clientHeight, sh = el.scrollHeight;
    if (sh <= ch + 40) return;
    if (!isDoc) { const o = getComputedStyle(el).overflowY; if (!/(auto|scroll|overlay)/.test(o)) return; }
    const r = isDoc ? {left: 0, top: 0, right: vw, bottom: vh} : el.getBoundingClientRect();
    const w = Math.min(r.right, vw) - Math.max(r.left, 0), h = Math.min(r.bottom, vh) - Math.max(r.top, 0);
    if (w < 150 || h < 150) return;
    const score = w * h * Math.min(sh / ch, 20);
    if (score > bestScore) { bestScore = score; best = el; }
  };
  if (doc) consider(doc);
  document.querySelectorAll('*').forEach(el => { if (el !== doc) consider(el); });
  if (!best) return {found: false};
  if (mark) {
    document.querySelectorAll('[data-ss-target]').forEach(e => e.removeAttribute('data-ss-target'));
    best.setAttribute('data-ss-target', '1');
  }
  return {found: true, score: bestScore, desc: best.tagName + '.' + String(best.className).slice(0, 60),
          scrollHeight: best.scrollHeight, clientHeight: best.clientHeight};
})(%s)"""

STATE_JS = """(() => {
  const el = document.querySelector('[data-ss-target]');
  if (!el) return null;
  const isDoc = el === document.scrollingElement;
  const r = isDoc ? {left: 0, top: 0} : el.getBoundingClientRect();
  const cx = isDoc ? 0 : el.clientLeft, cy = isDoc ? 0 : el.clientTop;
  const de = document.documentElement;
  const x = Math.max(r.left + cx, 0), y = Math.max(r.top + cy, 0);
  const w = Math.min(isDoc ? de.clientWidth : el.clientWidth, innerWidth - x);   // pencerenin dışına taşan kısım yakalanamaz
  const h = Math.min(isDoc ? de.clientHeight : el.clientHeight, innerHeight - y);
  return {top: el.scrollTop, height: el.scrollHeight, client: h, x: x, y: y, w: w, h: h,
          px: window.pageXOffset, py: window.pageYOffset, dpr: window.devicePixelRatio};
})()"""

# Kayan alanın üstünde duran küçük yapışkan/sabit öğeleri (tarih etiketi, 'aşağı git' düğmesi...) gizler.
HIDE_JS = """(() => {
  const el = document.querySelector('[data-ss-target]');
  const R = el.getBoundingClientRect();
  let n = 0;
  document.querySelectorAll('*').forEach(c => {
    if (c === el || c.contains(el)) return;
    const p = getComputedStyle(c).position;
    if (p !== 'sticky' && p !== 'fixed') return;
    const r = c.getBoundingClientRect();
    if (r.width === 0 || r.height === 0 || r.height > 0.4 * R.height) return;
    if (r.bottom < R.top || r.top > R.bottom || r.right < R.left || r.left > R.right) return;
    c.setAttribute('data-ss-hide', c.style.visibility || '-');
    c.style.setProperty('visibility', 'hidden', 'important');
    n++;
  });
  return n;
})()"""

RESTORE_JS = """(() => {
  document.querySelectorAll('[data-ss-hide]').forEach(c => {
    const v = c.getAttribute('data-ss-hide');
    c.style.removeProperty('visibility');
    if (v && v !== '-') c.style.visibility = v;
    c.removeAttribute('data-ss-hide');
  });
  document.querySelectorAll('[data-ss-target]').forEach(e => e.removeAttribute('data-ss-target'));
})()"""


def _state(c):
    st = c.eval(STATE_JS)
    if st is None:
        raise CDPError("Kayan alan kayboldu (sayfa değişmiş olabilir)")
    return st


def _settle(c, quiet=0.2, maxwait=2.5):
    """scrollTop ve scrollHeight bir süre değişmeyene kadar bekler (yumuşak kaydırma, tembel yükleme)."""
    t0 = time.time()
    last, since = None, time.time()
    while time.time() - t0 < maxwait:
        st = _state(c)
        key = (st["top"], st["height"])
        if key != last:
            last, since = key, time.time()
        elif time.time() - since >= quiet:
            return st
        time.sleep(0.05)
    return _state(c)


def _scroll_to(c, y):
    c.eval(f"document.querySelector('[data-ss-target]').scrollTo({{top: {int(y)}, behavior: 'instant'}})")
    return _settle(c)


def _shot(c, st):
    r = c.call("Page.captureScreenshot", format="png", fromSurface=True, captureBeyondViewport=False,
               clip={"x": st["x"] + st["px"], "y": st["y"] + st["py"], "width": st["w"], "height": st["h"],
                     "scale": 1})
    return Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")


def pick_target(port, title_hint=""):
    """Birden çok sayfa hedefi arasından en çok kaydırılabilir içeriği olanı seçer. (ws_url, başlık) döner."""
    best, best_score = None, -1
    for t in list_targets(port, 2.0):
        try:
            c = CDP(t["webSocketDebuggerUrl"])
        except Exception:
            continue
        try:
            r = c.eval(FIND_JS % "false")
        except Exception:
            r = {"found": False}
        finally:
            c.close()
        if not r or not r.get("found"):
            continue
        score = r["score"] * (2 if title_hint and title_hint.lower() in (t.get("title") or "").lower() else 1)
        if score > best_score:
            best, best_score = (t["webSocketDebuggerUrl"], t.get("title", "")), score
    if best is None:
        raise CDPError("Bu uygulamada kaydırılabilir bir alan bulunamadı")
    return best


def capture_target(ws_url, progress=None, max_steps=800, max_height_px=90000, preload_seconds=240):
    """Seçilen sayfadaki kayan alanı önce en üste çıkıp (tüm içerik yüklensin), sonra yukarıdan aşağıya yakalar. (img, piksel/css ölçeği, info) döner."""
    c = CDP(ws_url)
    log = []
    try:
        found = c.eval(FIND_JS % "true")
        if not found or not found.get("found"):
            raise CDPError("Kaydırılabilir alan bulunamadı")
        log.append(f"alan={found['desc']} scrollHeight={found['scrollHeight']} clientHeight={found['clientHeight']}")
        hidden = c.eval(HIDE_JS)
        log.append(f"gizlenen yapışkan/sabit öğe={hidden}")

        # 1) En üste çık: eski içerik tembel yükleniyorsa tamamı yüklenene dek bekle (yukarı giderken görünüm sıçrayabilir,
        #    o yüzden yakalamayı yüklemeden sonra, yukarıdan aşağıya yapıyoruz).
        st = _scroll_to(c, 0)
        last_h, stable, t0 = st["height"], 0, time.time()
        while time.time() - t0 < preload_seconds:
            _scroll_to(c, 80)  # küçük bir kıpırdatma: yükleme tetikleyicilerini çalıştırır
            _scroll_to(c, 0)
            time.sleep(1.0)
            st = _settle(c)
            if st["top"] <= 0.5 and st["height"] == last_h:
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
            last_h = st["height"]
        log.append(f"üste çıkış: {time.time() - t0:.0f} sn, yükseklik={st['height']}, kararlı={stable >= 2}")

        # 2) Yukarıdan aşağıya yakala.
        img = _shot(c, st)
        ppc = img.width / st["w"]  # piksel / css px
        stitch = ss.Stitcher("down")
        stitch.add(img)
        frames, stop, step = 1, "max", max(80, int(st["client"] * 0.75))
        log.append(f"başlangıç top={st['top']} height={st['height']} client={st['client']} ppc={ppc:.2f}")
        for _ in range(max_steps):
            old = st
            if old["top"] + old["client"] >= old["height"] - 1:  # en alt: yeni içerik geldi mi?
                time.sleep(0.8)
                st = _settle(c)
                if st["height"] <= old["height"] + 1:
                    stop = "end"
                    break
                continue
            st = _scroll_to(c, old["top"] + step)
            moved = st["top"] - old["top"]
            hints = [round(moved * ppc)] if moved > 0 else []
            res = stitch.add(_shot(c, st), hints=hints)
            log.append(f"top {old['top']:.0f}->{st['top']:.0f} height {old['height']}->{st['height']} ipucu={hints} sonuç={res}")
            if res == "end":
                if moved <= 0:
                    stop = "end"
                    break
                continue
            if res == "fail":
                stop = "fail"
                break
            frames += 1
            if progress:
                progress(frames, stitch.height())
            if stitch.height() > max_height_px:
                stop = "max"
                break
        return stitch.result(), ppc, {"frames": frames, "stop": stop, "log": log}
    finally:
        try:
            c.eval(RESTORE_JS)
        except Exception:
            pass
        c.close()
