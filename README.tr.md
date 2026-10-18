# Hangar

**Tek terminalden çoklu araç ArduPilot SITL + Gazebo.**

Hangar istediğin sayıda ArduPilot aracını (uçak, multikopter, VTOL, kara aracı,
tekne) Gazebo'da ya da ArduPilot'un dahili fiziğiyle başlatır. Araçların
MAVLink trafiğini yer istasyonuna yönlendirir ve bütün filoyu tek bir terminal
arayüzünde gösterir. Tüm portlar tek bir port planından gelir, ekran da onlarca
pencereyle dolmaz.

[English README](README.md)

## Neden?

Alışılmış çoklu araç kurulumunda her araç için ayrı `sim_vehicle.py` ve ayrı
MAVProxy çalışır, harita için de ayrı bir pencere açılır. Bu düzende yollar
koda gömülüdür, araya `sleep`'ler serpiştirilir, portlar da script'lere
dağılmıştır. Hangar bunların yerine şunları koyar:

- **Tek senaryo dosyası**: dünya, araçlar, konumlar, parametreler ve çıkışlar tek bir YAML'da.
- **Tek port planı**: SITL, Gazebo FDM, kamera ve GCS çıkışlarının hepsi tek yerde hesaplanır. Bir şey başlamadan önce çakışma kontrolü yapılır. Planı görmek için: `hangar ports`.
- **Tek süreç**: SITL'ler arka planda çalışır ve logları dosyaya yazılır. Araç başına MAVProxy yerine dahili bir MAVLink router kullanılır. Durdurunca her şey temizlenir, arkada SITL kalmaz.
- **Tek terminal arayüzü**: her şey başlarken bile donmaz.

## Kurulum

```bash
curl -fsSL https://raw.githubusercontent.com/Enginar140/hangar/main/install.sh | bash
```

Kurulum her adımdan önce sorar:

1. [uv](https://docs.astral.sh/uv/) ile Hangar'ı kullanıcı dizinine kurar. sudo gerekmez, sistem Python'una dokunulmaz.
2. ArduPilot'un **resmi hazır derlenmiş SITL**'ini indirir: arducopter, arduplane ve ardurover 4.7.2, toplam yaklaşık 21 MB. Birkaç saniye sürer, derleyici gerekmez. Kaynaktan derlemek istersen `--build-ardupilot` seçeneğini kullan.
3. Kopyala butonları için pano aracını (wl-clipboard, xclip) kurar.
4. İstersen Gazebo Harmonic'i, ArduPilot Gazebo eklentisini ve örnek modelleri kurar.
5. Sonunda `hangar doctor` ile kurulumu kontrol eder.

ArduPilot zaten kuruluysa Hangar'a onun yerini göstermen yeterli:

```bash
uv tool install git+https://github.com/Enginar140/hangar
hangar config set ardupilot.dir ~/ardupilot
hangar config add catalog.model_paths ~/SITL_Models/Gazebo/models
hangar config add catalog.world_paths ~/SITL_Models/Gazebo/worlds
hangar config add gazebo.plugin_paths ~/ardupilot_gazebo/build
hangar doctor
```

### İndirilen projelerin lisansları

Hangar MIT lisanslıdır ve içinde sadece kendi kodu vardır. ArduPilot (GPL-3.0),
ardupilot_gazebo (LGPL-3.0) ve SITL_Models (lisans belirtilmemiş) Hangar'ın
içinde **dağıtılmaz**. `hangar sources fetch` her projenin lisansını gösterir,
onay ister ve projeyi sabitlenmiş bir sürümde kendi sunucusundan indirir (hazır SITL için firmware.ardupilot.org, diğerleri için GitHub). Yani
kodu doğrudan kaynağından almış olursun. Hangi commit'lerin indirildiği
`~/.local/share/hangar/sources/lock.json` dosyasında kayıtlıdır.

## Kullanım

```bash
hangar                         # terminal arayüzü (son senaryonu geri yükler)
hangar init                    # quickstart.yaml yazar (Gazebo gerektirmeyen iki araç)
hangar quickstart.yaml         # senaryoyu arayüzde açar
hangar up quickstart.yaml      # arayüzsüz çalıştırır, Ctrl+C ile durur
hangar ports quickstart.yaml   # port planını ve ön kontrolleri gösterir
hangar models / hangar worlds  # başlatılabilecek araçlar ve dünyalar
hangar doctor                  # neyin kurulu olduğunu, neyin eksik olduğunu ve çözümünü gösterir
hangar logs                    # son çalıştırmanın logu (-f canlı izle, -w sadece uyarılar, --list çalıştırmalar)
```

### Loglar

Her çalıştırma, arayüzde görünen her şeyi tek bir dosyaya yazar:
`~/.local/state/hangar/runs/<çalıştırma>/logs/hangar.log`. Buna Hangar'ın
olayları ve araçların tüm MAVLink mesajları (PreArm hataları, mod
değişiklikleri vb.) dahildir. Her SITL'in ve Gazebo'nun ham logu da aynı
klasörde ayrı dosyalarda durur. `runs/latest` her zaman en son çalıştırmayı
gösterir. Günlük sekmesindeki butonlarla görünen logu mevcut filtreyle
**kaydedebilir**, **kopyalayabilir** ya da çalıştırma **klasörünü açabilirsin**.
Terminalden: `hangar logs -f` (canlı), `hangar logs -w` (sadece uyarılar),
`hangar logs --file plane/sitl.log`.

### Arayüz tuşları

| Tuş | İş | Tuş | İş |
|---|---|---|---|
| `s` | başlat | `x` | durdur |
| `a` | araç ekle | `e` / `d` | aracı düzenle / çıkar |
| `1`–`4` | Araç / Günlük / Portlar / Senaryo sekmeleri | `:` | seçili araç için komut satırı |
| `Esc` | filo tablosuna dön | `F2` | sistem kontrolü |
| `ctrl+s` / `ctrl+o` | senaryoyu kaydet / aç | `ctrl+p` | komut paleti (temalar dahil) |
| `q` | çık (simülasyonu durdurur) | | |

Araç komutları (butonlardan ya da komut satırından): `arm [force]`, `disarm`,
`mode GUIDED` (ya da sadece `GUIDED`), `takeoff 30`, `rtl`, `land`,
`param set AD DEĞER`.

Arayüz Türkçe ve İngilizce destekler ve dili `$LANG`'a göre seçer. Elle
ayarlamak için: `hangar config set language tr`.

## Senaryo dosyası

```yaml
name: iki-ucak
world: skywalker_x8_runway        # sadece dahili fizik kullanacaksan yazma
outputs:                          # her araca uygulanır
  - udp:127.0.0.1:14550           # QGroundControl / Mission Planner
  - udp:127.0.0.1:{15000+i}       # araç başına port; {i} = sıra, {sysid} = MAVLink id
vehicles:
  - model: skywalker_x8
    pose: [0, 0, 0.3, 0, 0, 90]   # x y z roll pitch yaw (m, derece)
    camera:
      v4l2_device: /dev/video2    # burun kamerası -> cv2.VideoCapture('/dev/video2')
  - model: sitl:copter            # dahili fizik, Gazebo gerekmez
    name: scout
    pose: [40, 0, 0]
    params: {SIM_WIND_SPD: 3}
    outputs: ["tcpin:0.0.0.0:{6000+i}"]   # örn. companion bilgisayar buraya bağlanır
```

### Kameralar

Modelinde kamera olmasa bile her Gazebo aracına kamera takılabilir. `camera: true`
yazınca Hangar o çalıştırmanın model kopyasına bir kamera ekler (orijinal
dosyalar değişmez). Görüntü RTP/UDP üzerinden H.264 olarak yayınlanır.
Ayarların hepsi opsiyoneldir:

```yaml
    camera:
      pose: [0, 0, -0.05, 0, 90, 0]   # gövde üzerinde, m/derece; pitch 90 aşağı bakar (varsayılan: burun, ileri)
      width: 1280
      height: 720
      fov: 60                         # yatay görüş açısı, derece
      fps: 30
      v4l2_device: /dev/video2        # opsiyonel: OpenCV/ROS webcam gibi okur
```

Kendi kamerası olan model onu kullanmaya devam eder. Kapatmak için `camera: false` yaz.
Arayüzde araç penceresinden kalite (düşük/orta/yüksek/özel çözünürlük), FPS,
görüş açısı ve yön ayarlanır. Video bit hızı ArduPilot'un kamera eklentisinde
800 kbit/s olarak sabit.

**Kamerayı Python'da kullanmak.** Kamera butonu, o kameranın portuyla
doldurulmuş hazır kod içeren bir pencere açar. Seçenekler: numpy/OpenCV ile
Python, GStreamer'lı OpenCV, v4l2 cihazı ve `gst-launch` komutu. Kodu `.py`
dosyası olarak da kaydedebilirsin. Bağımsız bir örnek:
[`examples/camera_opencv.py`](examples/camera_opencv.py)

```python
from camera_opencv import frames            # examples/camera_opencv.py
for frame in frames(port=5600, width=1280, height=720):
    ...                                      # frame: BGR numpy dizisi, cv2 ile aynı
```

Bu yöntem pip'teki `opencv-python` ile de çalışır; o paketin kendi GStreamer
desteği olmasa bile. UDP portunu aynı anda yalnızca bir program okuyabilir.
Aynı kamerayı birden fazla program okuyacaksa kameraya bir `v4l2_device` ver
ve o cihazı `cv2.VideoCapture('/dev/video2')` ile aç.

### Portlar: yer istasyonu ve script bağlamak

Tüm port ayarları **Portlar** sekmesinde. **Çıkış ekle** ve **Çıkışı sil**
simülasyon çalışırken de kullanılabilir; değişiklik anında uygulanır. Ekleme
penceresi her araç için çıkacak portu önceden gösterir ve çakışan bir çıkışı
eklemene izin vermez.

- **QGroundControl / Mission Planner** udp 14550'yi dinler. Varsayılan olarak
  tüm araçlar bu portu paylaşır; bu programlar her aracı kendi sistem ID'siyle
  ayrı gösterir.
- **Tek araçla konuşan script** o aracın kendi script portuna bağlanır:
  `tcp:127.0.0.1:5762 + 10*i`. O porttan başka araç konuşmaz, araçlar karışmaz.
  Örnek: [`examples/mavlink_script.py`](examples/mavlink_script.py)
- **Ek port** (ikinci yer istasyonu, yarışma sunucusu, companion bilgisayar):
  çıkış ekle. `{i}` her araca ayrı port verir, örn. `udp:127.0.0.1:{14560+i}`.

### Port düzeni

| | araç 0 | araç 1 | araç *i* |
|---|---|---|---|
| MAVLink (SITL serial0) | tcp 5760 | tcp 5770 | 5760 + 10*i* |
| Script portları (sadece o araç) | tcp 5762, 5763 | tcp 5772, 5773 | 5762 + 10*i*, 5763 + 10*i* |
| Gazebo ↔ SITL | udp 9002 | udp 9012 | 9002 + 10*i* |
| Kamera (RTP H.264) | udp 5600 | udp 5601 | 5600 + *i* |
| MAVLink sistem id | 1 | 2 | *i* + 1 |

## Kendi aracını eklemek

`model.sdf` dosyasında `ArduPilotPlugin` bulunan her Gazebo modeli
`hangar models` listesinde görünür. Hangar'a modelin ne olduğunu söylemek için
modelin yanına bir `hangar.yaml` koy:

```yaml
type: plane                 # copter | plane | rover | boat
params: [benim_ucagim.param]
camera_pose: [0.5, 0, 0.05, 0, 0, 0]   # opsiyonel: `camera: true` kamerayı buraya takar
```

Sonra model klasörünü ekle: `hangar config add catalog.model_paths /yol/models`.
