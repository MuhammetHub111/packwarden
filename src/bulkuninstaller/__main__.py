import os
import sys

# GTK/libadwaita hazır bileşenlerinin (ör. Adw.AboutDialog'un "Website",
# "Details" gibi kendi metinleri) hangi dilde göründüğü PackWarden'ın
# kendi _() çeviri sistemine hiç bakmıyor — bunlar gettext üzerinden
# doğrudan İŞLETİM SİSTEMİ diline (LANG/LANGUAGE) göre seçiliyor. Sonuç:
# Ayarlar'dan "English" seçsen bile, sistem dilin Türkçeyse hazır GTK
# metinleri Türkçe kalıyordu — PackWarden'ın kendi metinleriyle tutarsız.
#
# Kullanıcı "Otomatik (sistem)" seçtiyse buraya hiç dokunulmuyor, GTK
# zaten olması gerektiği gibi sistem diline uyuyor. Yalnızca kullanıcı
# Ayarlar'dan bilerek "tr"/"en" seçtiyse, GTK'nin göreceği dili de AYNI
# seçime zorluyoruz — bu, gi/Gtk hiç import edilmeden ÖNCE yapılmalı,
# aksi halde gettext kataloğu zaten sistem diliyle yüklenmiş olur.
def _apply_language_override():
    from . import prefs
    chosen = prefs.get("language")
    if chosen in ("tr", "en"):
        os.environ["LANGUAGE"] = chosen


_apply_language_override()

from .application import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv))
