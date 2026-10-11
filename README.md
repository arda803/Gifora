# Gifora

Gifora, GIF, animasyonlu WebP ve APNG görsellerini masaüstünüzde oynatan bir Windows ve Linux uygulamasıdır. Görselleri ekran kenarlarına veya özel konumlara yerleştirebilir; boyut, saydamlık ve görünürlüklerini ayrı ayrı ayarlayabilirsiniz.

## Özellikler

- GIF, animasyonlu WebP ve APNG desteği
- Dosya seçici ve sürükleyip bırakmayla görsel ekleme
- Sol, sağ, köşe ve özel konumlar; çoklu ekran desteği
- Görsel başına görünürlük ve oynatma; tümünü aç/kapat kontrolleri
- Koleksiyon arama, düzen profilleri ve başlangıçta görünür görsellerin otomatik yüklenmesi
- Tıklamaları alttaki pencereye geçirme, konum kilidi ve tam ekran algılama
- Windows bildirim alanına küçültme; Gifora gizliyken animasyonlar çalışmaya devam eder

## Windows'ta çalıştırma

Python 3.10+ ve Tkinter kurulu olmalı.

~~~powershell
python -m pip install -r requirements.txt
python src/gifora.py
~~~

## Windows EXE oluşturma

~~~powershell
python -m pip install -r requirements-build.txt
.\packaging\build_windows.ps1
~~~

EXE ve ZIP paketi dist klasöründe oluşturulur. Hazır uygulama paketleri kaynak deposuna eklenmez; GitHub Releases üzerinden paylaşılabilir.

## Pardus / Linux

Python 3, Tkinter, Pillow ve X11 gerekir.

~~~bash
python3 -m pip install -r requirements.txt
python3 src/gifora.py
~~~

X11; saydam kontur, tıklama geçişi ve tam ekran algılama için önerilir. Wayland bazı pencere kontrollerini kısıtlayabilir.

## Testler

~~~bash
python -m unittest discover -s tests -v
~~~

## Gizlilik

Gifora görselleri yerel olarak işler ve sunucuya yüklemez. Ayarlar Windows'ta %APPDATA%\\Gifora, Linux'ta ~/.config/Gifora içinde saklanır.

## Geliştirici

- LinkedIn: https://www.linkedin.com/in/arda-talha-tekinel-882176351/
- GitHub: https://github.com/arda803
