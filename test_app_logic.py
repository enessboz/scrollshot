"""app.py'nin ekrandan bağımsız mantığı (dosya adı, kütüphane, dışa aktarma)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image  # noqa: E402

import app  # noqa: E402

tmp = tempfile.mkdtemp()
app.LIB, app.DESKTOP = os.path.join(tmp, "lib"), os.path.join(tmp, "desk")
app.HIST = os.path.join(app.LIB, "history.json")
os.makedirs(app.DESKTOP)

assert app.safe_name("Chat | Ali Veli | Microsoft Teams") == "Chat - Ali Veli", app.safe_name("Chat | Ali Veli | Microsoft Teams")
assert app.safe_name("a/b:c?") == "a - b - c"
assert app.safe_name("") == "ScrollShot"

img = Image.new("RGB", (400, 30000), "white")
e = app.store(img, 2.0, "Chat | Ali | Microsoft Teams", "Teams")
assert os.path.exists(os.path.join(app.LIB, e["file"]))
assert app.load_history()[-1]["title"] == e["title"]
png, pdf, jpg = (app.export(e, f) for f in ("png", "pdf", "jpg"))
assert all(os.path.getsize(p) > 0 for p in (png, pdf)), "boş çıktı"
assert os.path.basename(png).startswith("Chat - Ali ") and png.endswith(".png")
assert app.export(e, "png") != png  # çakışmada (2) eklenir
print("PDF sayfa sayısı için dosya boyutu:", os.path.getsize(pdf))

# başka klasöre kaydet
other = os.path.join(tmp, "baska", "klasor")
assert os.path.dirname(app.export(e, "png", dest=other)) == other

# dışarıdan resim ekle -> geçmişe girer, başka biçime çevrilebilir
ext = os.path.join(tmp, "dis.png")
Image.new("RGB", (300, 900), "white").save(ext)
ie = app.import_file(ext)
assert ie["title"] == "dis" and ie["app"] == "İçe aktarılan"
assert os.path.getsize(app.export(ie, "pdf")) > 0
try:
    app.import_file(os.path.join(tmp, "x.docx"))
    raise SystemExit("desteklenmeyen tür kabul edildi")
except ValueError:
    pass
# PDF içe aktarma: PyMuPDF yoksa anlaşılır hata; varsa sayfalar tek görüntü olur
try:
    import fitz  # noqa: F401
    ipdf = app.import_file(pdf)
    assert ipdf["size"][1] > 0
    print("PDF içe aktarma: PyMuPDF ile", ipdf["size"])
except ImportError:
    try:
        app.import_file(pdf)
        raise SystemExit("PyMuPDF yokken PDF kabul edildi")
    except ValueError as err:
        print("PDF içe aktarma (PyMuPDF yok):", err)

# tam yola yaz (farklı kaydet)
custom = os.path.join(tmp, "ozel ad.pdf")
assert os.path.getsize(app.export_as(e, custom)) > 0
try:
    app.export_as(e, os.path.join(tmp, "x.txt"))
    raise SystemExit("geçersiz uzantı kabul edildi")
except ValueError:
    pass

# silme: geçmişten ve diskten kalkar
n0 = len(app.load_history())
app.delete_entry(ie)
assert len(app.load_history()) == n0 - 1 and not os.path.exists(os.path.join(app.LIB, ie["file"]))
print("app mantık testleri geçti.")
