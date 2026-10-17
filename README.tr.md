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
2. İstersen ArduPilot SITL'i indirir ve derler.
3. İstersen Gazebo Harmonic'i, ArduPilot Gazebo eklentisini ve örnek modelleri kurar.
4. Sonunda `hangar doctor` ile kurulumu kontrol eder.

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
onay ister ve projeyi sabitlenmiş bir sürümde kendi reposundan indirir. Yani
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
```

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

### Port düzeni

| | araç 0 | araç 1 | araç *i* |
|---|---|---|---|
| MAVLink (SITL serial0) | tcp 5760 | tcp 5770 | 5760 + 10*i* |
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
```

Sonra model klasörünü ekle: `hangar config add catalog.model_paths /yol/models`.
