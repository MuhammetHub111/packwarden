"""Kalıcı kullanıcı ayarları.

Ayarlar ~/.config/bulkuninstaller/settings.json dosyasında tutulur.
Her set() çağrısı diske hemen yazar; dosya bozuksa varsayılanlara
dönülür (uygulama asla ayar yüzünden açılmamazlık etmez).
"""

import json
import os

CONFIG_DIR = os.path.expanduser("~/.config/bulkuninstaller")
CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.json")

DEFAULTS = {
    # Sadece .desktop girdisi olan gerçek uygulamaları listele;
    # kütüphaneleri ve sistem paketlerini gizle
    "apps_only": True,
    # Arayüz dili: "auto" (sistem dili), "tr" veya "en"
    "language": "auto",
    # Kritik sistem paketlerini silmeden önce ek uyarı göster
    "protect_system": True,
    # Açılışta arkaplanda güncelleme denetimi yapıp bulunca güncelleme
    # penceresini otomatik aç (yalnızca test/dev sürümünde kullanılabilir)
    "auto_update": False,
    # Ana liste sütunlarının kullanıcının sürükleyerek belirlediği sırası
    # (sütun kimlikleri listesi) — boşsa varsayılan sıra kullanılır
    "column_order": [],
    # Kullanılmayan uygulamalar penceresindeki eşik seçimi: 0=3 ay,
    # 1=6 ay, 2=1 yıl, 3=özel — pencere her açıldığında hatırlanır
    "unused_threshold_preset": 1,
    # "Özel" seçiliyken kullanılan gün sayısı
    "unused_threshold_custom_days": 180,
}

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        data = {}
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            pass
        _cache = {**DEFAULTS, **data}
    return _cache


def get(key: str):
    return _load().get(key, DEFAULTS.get(key))


def set(key: str, value) -> None:
    data = _load()
    data[key] = value
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass  # diske yazılamasa da oturum boyunca ayar geçerli kalır
