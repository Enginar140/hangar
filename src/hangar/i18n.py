"""Tiny translation table. English is the source language; add a column to
add a language. ``language = "auto"`` follows $LANG."""

from __future__ import annotations

import os

_lang = "en"

TR: dict[str, str] = {
    # app / bindings
    "Start": "Başlat",
    "Stop": "Durdur",
    "Add": "Ekle",
    "Edit": "Düzenle",
    "Remove": "Çıkar",
    "Save": "Kaydet",
    "Open": "Aç",
    "Quit": "Çık",
    "Help": "Yardım",
    "Doctor": "Kontrol",
    "Cancel": "Vazgeç",
    "OK": "Tamam",
    # tabs
    "Vehicle": "Araç",
    "Logs": "Günlük",
    "Ports": "Portlar",
    "Scenario": "Senaryo",
    # states
    "idle": "hazır",
    "starting": "başlıyor",
    "running": "çalışıyor",
    "stopping": "duruyor",
    "error": "hata",
    "stopped": "durdu",
    "crashed": "çöktü",
    "online": "çevrimiçi",
    "silent": "sessiz",
    "booting": "açılıyor",
    "planned": "planlı",
    # fleet columns
    "Name": "Ad",
    "Model": "Model",
    "Status": "Durum",
    "Mode": "Mod",
    "Armed": "Arm",
    "Ready": "Hazır",
    "Alt": "İrtifa",
    "Speed": "Hız",
    "Battery": "Batarya",
    "GPS": "GPS",
    "Heading": "Yön",
    "Position": "Konum",
    "Climb": "Tırmanma",
    "Throttle": "Gaz",
    # vehicle actions
    "Arm": "Arm",
    "Disarm": "Disarm",
    "Takeoff": "Kalkış",
    "RTL": "Eve dön",
    "Land": "İniş",
    "Camera": "Kamera",
    "Restart": "Yeniden başlat",
    "Set mode": "Mod seç",
    "command": "komut",
    "Command: arm, disarm, mode GUIDED, takeoff 30, rtl, land, param set NAME VALUE":
        "Komut: arm, disarm, mode GUIDED, takeoff 30, rtl, land, param set AD DEĞER",
    # scenario / editor
    "World": "Dünya",
    "none (built-in physics)": "yok (dahili fizik)",
    "Speed-up": "Hızlandırma",
    "Headless Gazebo": "Gazebo penceresiz",
    "Outputs for every vehicle": "Tüm araçlar için çıkışlar",
    "Add vehicle": "Araç ekle",
    "Edit vehicle": "Aracı düzenle",
    "Pose x y z yaw (m, deg)": "Konum x y z yaw (m, derece)",
    "Extra outputs (comma separated)": "Ek çıkışlar (virgülle)",
    "Camera to v4l2 device (optional)": "Kamerayı v4l2 cihazına aktar (opsiyonel)",
    "Camera (added to the model if it has none)": "Kamera (modelde yoksa eklenir)",
    "Parameters NAME=VALUE (comma separated)": "Parametreler AD=DEĞER (virgülle)",
    "Scenario file": "Senaryo dosyası",
    "Source": "Kaynak",
    "All": "Tümü",
    "Warnings only": "Sadece uyarılar",
    # messages
    "No vehicles yet. Press [b]a[/b] to add one.": "Henüz araç yok. Eklemek için [b]a[/b] tuşuna bas.",
    "Select a vehicle in the fleet table.": "Filo tablosundan bir araç seç.",
    "Stop the simulation to edit the scenario.": "Senaryoyu düzenlemek için simülasyonu durdur.",
    "Simulation is running. Stop it and quit?": "Simülasyon çalışıyor. Durdurup çıkılsın mı?",
    "Saved": "Kaydedildi",
    "Loaded": "Yüklendi",
    "Start failed": "Başlatılamadı",
    "Pick a model": "Bir model seç",
    "Ports are checked again on start.": "Portlar başlatırken yeniden kontrol edilir.",
    "Connect QGroundControl / Mission Planner to the udp outputs below.":
        "QGroundControl / Mission Planner'ı aşağıdaki udp çıkışlarına bağla.",
    "Problems": "Sorunlar",
    "All checks passed": "Tüm kontroller geçti",
    "Run directory": "Çalışma dizini",
    "Uptime": "Süre",
    "vehicles": "araç",
    "not running": "çalışmıyor",
}

# Long texts are keyed by an ID, so English needs a table too.
EN: dict[str, str] = {
    "CAMERA_OUTPUT_HELP": (
        "The IP address of the receiving device (find it there with 'hostname -I'). A multicast address "
        "such as 239.0.0.1 reaches every device on the network. {i} in the port is the vehicle number. "
        "On the receiver: QGroundControl > Video > UDP h.264 with this port, or the code in the Camera window. "
        "Its firewall must allow the port (Ubuntu: sudo ufw allow 5600/udp)."
    ),
    "MAVPROXY_OFFER": (
        "The map uses MAVProxy, which is not installed yet.\n\n"
        "Install it now? It goes into Hangar's own folder, needs no sudo, takes about a minute and "
        "about 900 MB (wxPython, OpenCV). License: {license}."
    ),
    "PORTS_HELP": (
        "[b]How to connect[/b]\n"
        "• [b]QGroundControl / Mission Planner[/b] listen on udp 14550. All vehicles may share it: "
        "each one shows up with its own system id.\n"
        "• [b]A script for one vehicle[/b]: use that vehicle's script port, e.g. for {first}: "
        "[b]mavutil.mavlink_connection('tcp:127.0.0.1:5762')[/b]. Only that vehicle is on it, nothing mixes.\n"
        "• [b]Another port[/b] (second GCS, competition server, companion computer): [b]Add output[/b]. "
        "{i} gives each vehicle its own port. Changes apply at once, even while running."
    ),
}

TR.update({
    "CAMERA_OUTPUT_HELP": (
        "Alıcı cihazın IP adresi (o cihazda 'hostname -I' ile bulunur). 239.0.0.1 gibi bir multicast adres "
        "ağdaki tüm cihazlara ulaşır. Porttaki {i} araç numarasıdır. Alıcıda: QGroundControl > Video > "
        "UDP h.264 ve bu port, ya da Kamera penceresindeki kod. Alıcının güvenlik duvarı bu porta izin "
        "vermeli (Ubuntu: sudo ufw allow 5600/udp)."
    ),
    "MAVPROXY_OFFER": (
        "Harita MAVProxy kullanıyor ve henüz kurulu değil.\n\n"
        "Şimdi kurulsun mu? Hangar'ın kendi klasörüne kurulur, sudo gerekmez. Kurulum yaklaşık bir "
        "dakika sürer ve yaklaşık 900 MB yer kaplar (wxPython, OpenCV). Lisans: {license}."
    ),
    "PORTS_HELP": (
        "[b]Nasıl bağlanılır[/b]\n"
        "• [b]QGroundControl / Mission Planner[/b] udp 14550'yi dinler. Tüm araçlar bu portu paylaşabilir: "
        "her araç kendi sistem ID'siyle ayrı görünür.\n"
        "• [b]Tek araçla konuşan script[/b]: o aracın script portunu kullan, örn. {first} için "
        "[b]mavutil.mavlink_connection('tcp:127.0.0.1:5762')[/b]. Orada sadece o araç var, karışmaz.\n"
        "• [b]Ek port[/b] (ikinci yer istasyonu, yarışma sunucusu, companion bilgisayar): [b]Çıkış ekle[/b]. "
        "{i} her araca kendi portunu verir. Değişiklik simülasyon çalışırken de anında uygulanır."
    ),
    "Quality": "Kalite",
    "Stream": "Yayın",
    "via Hangar: this computer + every address below": "Hangar üzerinden: bu bilgisayar + aşağıdaki adresler",
    "direct: the camera sends to one address only": "doğrudan: kamera sadece tek adrese gönderir",
    "Also send to (host:port, comma separated)": "Ayrıca gönder (adres:port, virgülle)",
    "direct, not to this computer": "doğrudan, bu bilgisayara değil",
    "direct to": "doğrudan:",
    "Direct mode: the camera sends only to that address. Run the code below on that device.":
        "Doğrudan mod: kamera sadece o adrese gönderir. Aşağıdaki kodu o cihazda çalıştır.",
    "The other addresses get the same stream; the code works there with their port.":
        "Diğer adresler aynı yayını alır; kod oralarda kendi portuyla çalışır.",
    "Camera outputs": "Görüntü çıkışları",
    "Send to a device": "Cihaza gönder",
    "Goes to": "Gidiyor",
    "this computer": "bu bilgisayar",
    "Send a camera to another device": "Kamerayı başka bir cihaza gönder",
    "Camera of": "Hangi aracın kamerası",
    "No camera streams through Hangar. Edit a vehicle (e), switch its camera on.":
        "Hangar üzerinden yayın yapan kamera yok. Bir aracı düzenle (e) ve kamerasını aç.",
    "This computer always gets a relayed camera.": "Hangar üzerinden yayında bu bilgisayar her zaman alır.",
    "A direct camera needs its one address; edit the vehicle (e) to change it.":
        "Doğrudan moddaki kameranın tek adresi var; değiştirmek için aracı düzenle (e).",
    "on the next start": "bir sonraki başlatmada",
    "Map": "Harita",
    "Install": "Kur",
    "Close map": "Haritayı kapat",
    "Start the simulation first (s).": "Önce simülasyonu başlat (s).",
    "Map and console opened: every vehicle is on one map. '!command' in the command line goes to MAVProxy.":
        "Harita ve konsol açıldı: tüm araçlar tek haritada. Komut satırında '!komut' MAVProxy'ye gider.",
    "Map closed": "Harita kapatıldı",
    "Open the map first (m): MAVProxy runs with it.": "Önce haritayı aç (m): MAVProxy onunla çalışır.",
    "Installing MAVProxy, this takes about a minute...": "MAVProxy kuruluyor, yaklaşık bir dakika sürer...",
    "MAVProxy install failed": "MAVProxy kurulamadı",
    "MAVProxy installed.": "MAVProxy kuruldu.",
    "Command: arm, disarm, mode GUIDED, takeoff 30, rtl, land, param set NAME VALUE, !MAVPROXY COMMAND":
        "Komut: arm, disarm, mode GUIDED, takeoff 30, rtl, land, param set AD DEĞER, !MAVPROXY KOMUTU",
    "Open folder": "Klasörü aç",
    "lines": "satır",
    "Save the log shown (with the current filter)": "Görünen logu kaydet (mevcut filtreyle)",
    "No clipboard tool found: sudo apt install wl-clipboard xclip":
        "Pano aracı bulunamadı: sudo apt install wl-clipboard xclip",
    "Log file": "Log dosyası",
    "FOV°": "Açı°",
    "Looks": "Yön",
    "ahead": "ileri",
    "down": "aşağı",
    "as in YAML": "YAML'daki gibi",
    "Low 640×360": "Düşük 640×360",
    "Medium 1280×720": "Orta 1280×720",
    "High 1920×1080": "Yüksek 1920×1080",
    "Custom": "Özel",
    "MAVLink outputs": "MAVLink çıkışları",
    "Ports of each vehicle": "Araçların portları",
    "Add output": "Çıkış ekle",
    "Remove output": "Çıkışı sil",
    "For": "Kimin için",
    "every vehicle": "tüm araçlar",
    "Kind": "Tür",
    "Address": "Adres",
    "Port": "Port",
    "{i} is the vehicle number (0, 1, 2...), so {14560+i} gives every vehicle its own port.":
        "{i} araç numarasıdır (0, 1, 2...); {14560+i} her araca ayrı port verir.",
    "All these vehicles share one port; fine for QGroundControl and Mission Planner, use {i} for scripts.":
        "Bu araçların hepsi tek portu paylaşır; QGroundControl ve Mission Planner için sorun değil, "
        "script için {i} kullan.",
    "udp — send to a program that listens (QGroundControl, Mission Planner)":
        "udp — dinleyen bir programa gönder (QGroundControl, Mission Planner)",
    "udpin — listen; a program sends to us first (pymavlink 'udpout:')":
        "udpin — dinle; program önce bize yazar (pymavlink 'udpout:')",
    "tcpin — TCP server; programs connect to us (pymavlink 'tcp:')":
        "tcpin — TCP sunucu; programlar bize bağlanır (pymavlink 'tcp:')",
    "Hangar ↔ SITL": "Hangar ↔ SITL",
    "Scripts (this vehicle only)": "Script portu (sadece bu araç)",
    "Vehicle": "Araç",
    "Output": "Çıkış",
    "Shared": "Paylaşım",
    "Scripts": "Script",
    "only this vehicle": "sadece bu araç",
    "sends": "gönderir",
    "listens (udp)": "dinler (udp)",
    "listens (tcp)": "dinler (tcp)",
    "applied live": "anında uygulandı",
    "This vehicle has no camera. Edit it (e) and switch the camera on.":
        "Bu aracın kamerası yok. Aracı düzenle (e) ve kamerayı aç.",
    "Use it from": "Kullanım",
    "Python (numpy + OpenCV)": "Python (numpy + OpenCV)",
    "OpenCV with GStreamer": "GStreamer'lı OpenCV",
    "v4l2 device (webcam)": "v4l2 cihazı (webcam gibi)",
    "gst-launch (terminal)": "gst-launch (terminal)",
    "Save as .py": ".py olarak kaydet",
    "Copy": "Kopyala",
    "Viewer": "Görüntüle",
    "Close viewer": "Görüntüyü kapat",
    "Close": "Kapat",
    "Copied to the clipboard": "Panoya kopyalandı",
    "Sent to the terminal clipboard; if nothing was copied use 'Save as .py'":
        "Terminal panosuna gönderildi; kopyalanmadıysa '.py olarak kaydet'i kullan",
    "One program at a time can read the UDP port. With a v4l2 device the bridge reads it, "
    "so read the device instead; many programs can.":
        "UDP portunu aynı anda tek program okuyabilir. v4l2 cihazı açıkken portu köprü okur; "
        "sen cihazı oku, onu birden fazla program okuyabilir.",
    "One program at a time can read the UDP port. Need more? Set a v4l2 device for this camera.":
        "UDP portunu aynı anda tek program okuyabilir. Daha fazlası için bu kameraya bir v4l2 cihazı ver.",
})

_TABLES = {"tr": TR, "en": EN}


def set_language(lang: str) -> None:
    global _lang
    if lang in ("", "auto"):
        env = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
        lang = env[:2].lower()
    _lang = lang if lang in _TABLES else "en"


def language() -> str:
    return _lang


def t(text: str) -> str:
    table = _TABLES.get(_lang)
    return table.get(text, text) if table else text
