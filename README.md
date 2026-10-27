# ScrollShot

Kaydırmalı alanların (Teams thread, Slack, Chrome sayfası...) tek parça ekran görüntüsünü alır. PNG / PDF / JPG olarak masaüstüne kaydeder; buluta bir şey göndermez. macOS (Apple Silicon) için.

## Kurulum
1. [Releases](../../releases/latest) sayfasından `ScrollShot-mac.zip` indir, aç.
2. `ScrollShot`'ı Uygulamalar'a sürükle. İlk açılışta sağ tık → Aç.
3. Ekran Kaydı ve Erişilebilirlik izinlerini ver, uygulamayı kapatıp yeniden aç.

## Güncelleme
Uygulama her açılışta yeni sürümü kontrol eder, bulursa kendini günceller ve yeniden açılır. Elle bir şey yapman gerekmez.
Nadiren (yeni bileşen eklenirse) uygulama "yeniden indir" der; o zaman yukarıdaki kurulumu tekrarla.

## Geliştirme
`python test_stitch.py` ve `python test_updater.py` ekran gerektirmez. Her `main` push'u macOS'ta derlenip yeni release yayınlar.
