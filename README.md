# ScrollShot

Kaydırmalı pencerelerin (Teams thread, Slack, Chrome sayfası...) tek parça ekran görüntüsünü alır. macOS (Apple Silicon) ve Windows. Buluta bir şey göndermez.

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

## Windows
1. Releases sayfasından `ScrollShot-win.zip` indir, klasöre çıkar, `ScrollShot.exe`'yi çalıştır (SmartScreen uyarırsa: Daha fazla bilgi → Yine de çalıştır).
2. Sağ alttaki tepsi simgesinde (kamera) durur. Yakalamak istediğin pencerede **Ctrl+Alt+S**.
3. **Teams ve Slack:** ilk kullanımda uygulama onları "yakalama modunda" yeniden başlatmak için izin ister. Sonra içeriden (eklentiler gibi) tam sayfa yakalar: kare birleştirme hatası yok, fare gerekmez. Diğer uygulamalarda görüntüden birleştirme kullanılır.
4. Bittiğinde PNG / PDF / JPG sorar, dosya adı pencere başlığıdır. Geçmiş: tepsi menüsü → Geçmiş.

Ayrıntı günlüğü: `%APPDATA%\ScrollShot\ScrollShot.log` (tepsi menüsü → Günlüğü aç).

## Teknik
`cdp.py`: Chrome DevTools Protocol ile kayan alanı DOM'dan bulur, kaydırmayı kodla yapar, kareleri bilinen kaydırma miktarına göre birleştirir (`python test_cdp.py` gerçek Chromium'a karşı piksel piksel doğrular).
