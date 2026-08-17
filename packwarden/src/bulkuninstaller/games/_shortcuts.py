"""Oyun kaldırılınca menü/masaüstünde kalan .desktop kısayollarını temizler.

Steam ve Heroic, bir oyunu kütüphaneye eklerken isteğe bağlı olarak
.desktop kısayolu oluşturabiliyor ama kaldırırken bunu hiç silmiyor —
oyun tamamen kaldırılsa bile ikonu/adı görünmeye devam ediyor, tıklanınca
başlatıcıyı "yükle" ekranına yönlendiriyor (gerçek dosyalarla doğrulandı).
"""

import os

SHORTCUT_DIRS = (
    "~/.local/share/applications",
    "~/Desktop",
    "~/Masaüstü",
)


def remove_matching_shortcuts(needle: str) -> None:
    """Exec= veya Icon= satırında needle geçen her .desktop dosyasını
    siler. Sadece bu iki teknik alana bakılır (Name=/Comment= gibi
    serbest metin alanlarına değil) — kısa bir oyun kod adı (ör. "Eel")
    rastgele bir açıklama metninin içinde tesadüfen geçebilir ama bir
    dosya yolu/URI parametresi içinde geçmesi neredeyse hep kasıtlıdır.
    """
    for base in SHORTCUT_DIRS:
        base_dir = os.path.expanduser(base)
        try:
            entries = os.listdir(base_dir)
        except OSError:
            continue
        for entry in entries:
            if not entry.endswith(".desktop"):
                continue
            path = os.path.join(base_dir, entry)
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for line in text.splitlines():
                if line.startswith(("Exec=", "Icon=")) and needle in line:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    break
