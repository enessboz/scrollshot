# ScrollShot

Kaydırmalı pencerelerin (Teams thread, Slack, Chrome sayfası...) tek parça ekran görüntüsünü alır. macOS (Apple Silicon). Buluta bir şey göndermez.

## Kullanım
1. [Releases](../../releases/latest) sayfasından `ScrollShot-mac.zip` indir, aç, `ScrollShot`'ı Uygulamalar'a sürükle, sağ tık → Aç.
2. Ekran Kaydı ve Erişilebilirlik izinlerini ver, uygulamayı kapatıp yeniden aç. Menü çubuğunda 📸 görünür.
3. Yakalamak istediğin pencerede **⌃⌥S** (Control + Option + S) tuşlarına bas. Fareye dokunma.
4. Bitince PNG mi PDF mi diye sorar; dosya adı pencere başlığıdır ve masaüstüne yazılır.

Her yakalama `~/Pictures/ScrollShot/` altında saklanır. 📸 → **Geçmiş** menüsünden eski bir yakalamayı başka biçime (PNG/PDF/JPG) çevirip masaüstüne yazabilirsin.

Alanı seçmen gerekmez: program pencereyi azıcık kaydırıp hangi kısmın kaydığını kendisi bulur (başlık, yan liste, yazma kutusu dışarıda kalır), en alta gider, yukarı doğru yakalar.

## Güncelleme
Uygulama her açılışta yeni sürümü kontrol eder, bulursa kendini günceller. Nadiren (yeni bileşen eklenirse) "yeniden indir" der.

## Geliştirme
`python test_stitch.py`, `python test_updater.py`, `python test_app_logic.py` ekran gerektirmez. Her `main` push'u macOS'ta derlenip yeni release yayınlar.
