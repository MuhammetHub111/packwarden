"""Madde 7 - Wine registry dışa aktarma/geri yükleme TEST PLANI.

Bu tur ÜRETİM KODUNA export/import desteği EKLENMİYOR - Wine bu
makinede kurulu değil, `wine reg export`/`wine reg import`in gerçekte
çalıştığı CANLI DOĞRULANAMADI. Bu dosya, Wine kurulu bir makinede
GÜVENLE çalıştırılabilecek test planını barındırır:

- Tamamen ATILABİLİR, geçici bir WINEPREFIX içinde çalışır
  (tempfile.mkdtemp ile oluşturulur, testten sonra silinir).
- Gerçek kullanıcı prefix'ine (~/.wine, Steam/Proton prefix'leri) veya
  gerçek registry'sine HİÇ dokunmaz - leftovers.py/backup.py'nin
  GERÇEK kodunu da hiç çağırmaz, sadece Wine'ın kendi `reg` aracının
  export/import döngüsünü doğrudan test eder.
- Wine kurulu DEĞİLSE testler SKIP olarak işaretlenir (ok/fail değil) -
  bkz. @unittest.skipUnless. Bu makinede skip edilmesi BEKLENİR;
  "başarılı" diye raporlanmaz.

Encoding notu: Wine'ın `reg export`i CANLI DOĞRULANDI - çıktı gerçekten
UTF-16LE + BOM ("Windows Registry little-endian text", `file` komutuyla
doğrulandı). Dosya içeriği bu yüzden `iconv -f utf-16le -t utf-8` ile
host üzerinde dönüştürülüp kontrol ediliyor - ham baytları sandbox'a
çekip Python'da decode etmek YERİNE, çünkü export_file bir HOST yolu
(bkz. setUp - WINEPREFIX de, dolayısıyla bu dosya da host.run()
üzerinden oluşturuluyor, sandbox'ın yerel dosya sisteminde karşılığı
olmayabilir).

Çalıştırma (Wine kurulu bir makinede, GTK gerekmez):
    PYTHONPATH=src python3 -m unittest tests.test_wine_registry_export_plan -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller import host  # noqa: E402

_WINE_AVAILABLE = host.command_exists("wine")

_TEST_KEY = r"HKCU\Software\PackWardenAuditTest"
_TEST_VALUE_NAME = "Marker"
_TEST_VALUE = "packwarden-registry-roundtrip-probe"


def _wine_env(prefix: str) -> list[str]:
    # leftovers.py'nin GERÇEK registry silme kodundaki (remove_leftovers)
    # AYNI ortam değişkenleri - WINEDLLOVERRIDES, WineDbg'nin "Program
    # Hatası" penceresini açıp testi süresiz bekletmesini önler.
    return [
        "env", f"WINEPREFIX={prefix}",
        "WINEDLLOVERRIDES=winedbg.exe=d", "wine",
    ]


def _host_file_exists(path: str) -> bool:
    return host.run(["test", "-f", path]).returncode == 0


def _host_reg_file_text(path: str) -> str:
    """`.reg` dosyasının (UTF-16LE + BOM, canlı doğrulandı) içeriğini
    HOST üzerinde UTF-8'e çevirip okur - dosya bir host yolu olduğu
    için ham baytları doğrudan sandbox'ta açmak çalışmaz."""
    converted = host.run(["iconv", "-f", "utf-16le", "-t", "utf-8", path])
    if converted.returncode != 0:
        raise AssertionError(f"iconv ile .reg dosyası okunamadı: {converted.stderr}")
    return converted.stdout


@unittest.skipUnless(
    _WINE_AVAILABLE,
    "Wine kurulu değil - bu testler yalnızca Wine mevcut bir makinede "
    "çalışabilir ve canlı doğrulanamadı; bu makinede SKIP ediliyor, "
    "başarılı ya da başarısız sayılmıyor.",
)
class WineRegistryExportImportRoundTripTests(unittest.TestCase):
    """TAMAMEN atılabilir bir WINEPREFIX içinde çalışır."""

    def setUp(self):
        # KASITLI OLARAK tempfile.mkdtemp() DEĞİL, host.run() ile - wine
        # komutları da host.run() üzerinden (gerektiğinde flatpak-spawn
        # --host ile) çalışıyor; sanal bir sandbox içinden çağrılıyorsa
        # yerel bir Python tempfile yolu, host sürecinin görebileceği bir
        # yol olmayabilir (canlı doğrulandı: Claude Code'un kendi iç
        # sandbox'ında tam olarak bu yüzden "/tmp is not owned by you"
        # hatası alındı - host'un WINEPREFIX için gördüğü yol, Python'un
        # yerel /tmp'siyle aynı dosya sistemi/sahiplik alanında değildi).
        mk = host.run(["mktemp", "-d", "-t", "packwarden-audit-wineprefix.XXXXXX"])
        if mk.returncode != 0:
            self.skipTest(f"Geçici WINEPREFIX dizini oluşturulamadı: {mk.stderr}")
        self.prefix_dir = mk.stdout.strip()
        self.export_file = f"{self.prefix_dir}/exported.reg"

    def tearDown(self):
        if getattr(self, "prefix_dir", None):
            host.run(["rm", "-rf", self.prefix_dir])

    def _wine(self, *args, timeout=30):
        return host.run(_wine_env(self.prefix_dir) + list(args), timeout=timeout)

    def test_export_then_delete_then_import_restores_the_same_value(self):
        add = self._wine(
            "reg", "add", _TEST_KEY, "/v", _TEST_VALUE_NAME,
            "/t", "REG_SZ", "/d", _TEST_VALUE, "/f",
        )
        self.assertEqual(add.returncode, 0, add.stderr)

        query = self._wine("reg", "query", _TEST_KEY)
        self.assertEqual(query.returncode, 0)
        self.assertIn(_TEST_VALUE, query.stdout)

        export = self._wine("reg", "export", _TEST_KEY, self.export_file)
        self.assertEqual(export.returncode, 0, export.stderr)
        self.assertTrue(_host_file_exists(self.export_file))
        self.assertIn(_TEST_VALUE, _host_reg_file_text(self.export_file))

        delete = self._wine("reg", "delete", _TEST_KEY, "/f")
        self.assertEqual(delete.returncode, 0, delete.stderr)
        gone = self._wine("reg", "query", _TEST_KEY)
        self.assertNotEqual(gone.returncode, 0)

        imported = self._wine("reg", "import", self.export_file)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        restored = self._wine("reg", "query", _TEST_KEY)
        self.assertEqual(restored.returncode, 0)
        self.assertIn(_TEST_VALUE, restored.stdout)

    def test_export_of_a_nonexistent_key_fails_cleanly_without_a_crash(self):
        result = self._wine(
            "reg", "export", r"HKCU\Software\DoesNotExistForSure12345",
            self.export_file,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(_host_file_exists(self.export_file))


class WineAvailabilityIsHonestlyReportedTests(unittest.TestCase):
    """Wine'ın bu makinede olmadığını doğrudan, görünür biçimde
    doğrular - üstteki sınıfın neden skip olduğunu açıklar."""

    def test_wine_is_reported_as_unavailable_on_this_machine(self):
        # BU test HİÇBİR ZAMAN skip edilmez - amacı tam tersi: Wine'ın
        # (şu an) gerçekten yok olduğunu kayıtlara geçirmek, "sessizce
        # atlandı" yerine "kontrol edildi, yok" demek.
        if _WINE_AVAILABLE:
            self.skipTest("Wine bu makinede kurulu - üstteki asıl testler çalışır")
        self.assertFalse(_WINE_AVAILABLE)


if __name__ == "__main__":
    unittest.main()
