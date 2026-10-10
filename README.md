# Gifora

Gifora, animasyonlu GIF, WebP ve APNG görsellerini masaüstünde konumlandırıp oynatır. Bu depo, uygulamanın kaynak kodunu ve Windows/Pardus paketleme dosyalarını içerir.

## Özellikler

- Birden fazla masaüstü görselini bağımsız pencerelerde oynatma
- GIF, animasyonlu WebP ve APNG desteği
- Dosya listesinden veya Dosya Gezgini'nden sürükle-bırak ile ekleme
- Sol, sağ, köşe ve özel konumlar; çoklu ekran desteği
- En-boy oranını koruyarak boyutlandırma, saydamlık ve görünürlük ayarları
- Koleksiyonlar ve düzen profilleri
- Tıklama geçişi, konum kilidi ve tam ekranda duraklatma seçenekleri
- Windows bildirim alanına küçültme; görseller uygulama penceresi gizliyken de oynar
- GIF şeffaf kenarlarında renk saçaklarını azaltan Windows iyileştirmesi

## Hızlı başlangıç (Windows)

1. Python 3.10 veya üstünü ve Tkinter bileşenini kurun.
2. Depo klasöründe PowerShell açın.
3. Bağımlılıkları yükleyin: python -m pip install -r requirements.txt
4. Uygulamayı başlatın: python src/gifora.py

Sürükle-bırak özelliği depoya eklenmiş tkinterdnd2 bileşenini kullanır. Windows'ta Tkinter, Python kurulumundaki Tcl/Tk ile gelir.

## Windows EXE üretme

1. Derleme bağımlılıklarını yükleyin: python -m pip install -r requirements-build.txt
2. PowerShell'de .\packaging\build_windows.ps1 çalıştırın.
3. EXE ve paylaşım ZIP'i dist klasörüne oluşturulur.

EXE'yi kaynak depoya eklemeyin. Hazır Windows EXE ve ZIP dosyaları release-assets klasöründe durur ve gitignore ile kaynak commit'lerinin dışında bırakılır. GitHub'da v1.2.2 etiketiyle bir Release oluşturup bu klasördeki iki dosyayı Release dosyası (asset) olarak ekleyin. Kullanıcılar kaynak kodu ve çalıştırılabilir Windows paketini ayrı indirir.

## Pardus / Linux

Pardus'ta Python 3, Tkinter, Pillow ve X11 sistem paketleri gerekir. Kaynak koddan başlatmak için:

~~~bash
python3 -m pip install -r requirements.txt
python3 src/gifora.py
~~~

Pardus .deb paketi oluşturmak için Linux/Pardus bilgisayarında python3 packaging/build_linux_deb.py çalıştırın. Paket dist/Gifora-Pardus.deb konumuna yazılır. Saydam kontur, tıklama geçişi ve tam ekran algılama için X11 oturumu gerekir; Wayland'de masaüstü kısıtları geçerlidir.

## Proje yapısı

~~~text
Gifora-GitHub/
├── .github/                 # Issue şablonları
├── assets/                  # Uygulama simgesi ve mağaza kapağı
├── packaging/               # Windows/Linux derleme ve mağaza metinleri
├── release-assets/          # Hazır Windows dosyaları; git tarafından yok sayılır
├── src/                     # Uygulama kaynak kodu ve gerekli vendored dosyalar
├── tests/                   # Otomatik testler
├── .gitignore
├── CHANGELOG.md
├── CONTRIBUTING.md
├── pyproject.toml
├── requirements.txt
└── requirements-build.txt
~~~

## Gizlilik

Gifora dosyaları yerel olarak işler; görseller sunucuya yüklenmez. Tercihler Windows'ta %APPDATA%\Gifora, Linux'ta ~/.config/Gifora altında tutulur.

## Geliştirici

- LinkedIn: https://www.linkedin.com/in/arda-talha-tekinel-882176351/
- GitHub: https://github.com/arda803

## Lisans

Gifora için açık kaynak lisansı henüz seçilmedi. Depoyu herkese açık yayımlamadan önce kodun kopyalanmasına, değiştirilmesine ve yeniden dağıtılmasına hangi koşullarda izin verileceğine karar verin. src/vendor/THIRD_PARTY_LICENSES içindeki lisans yalnızca tkinterdnd2 bağımlılığına aittir.
