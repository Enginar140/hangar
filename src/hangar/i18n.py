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

_TABLES = {"tr": TR}


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
