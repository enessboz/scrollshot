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
print("app mantık testleri geçti.")
