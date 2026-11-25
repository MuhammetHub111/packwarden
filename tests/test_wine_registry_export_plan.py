"""Madde 7 - Wine registry dışa aktarma/geri yükleme testleri.

Bu dosya, Wine kurulu bir makinede GÜVENLE çalıştırılabilecek testleri
barındırır (PackWarden 0.15.3 itibarıyla geliştirme makinesinde Wine
kurulu - bu testler burada SKIP değil, GERÇEKTEN çalışıp geçiyor):

- Tamamen ATILABİLİR, geçici bir WINEPREFIX içinde çalışır
  (tempfile.mkdtemp ile oluşturulur, testten sonra silinir).
- Gerçek kullanıcı prefix'ine (~/.wine, Steam/Proton prefix'leri) veya
  gerçek registry'sine HİÇ dokunmaz - leftovers.py/backup.py'nin
  GERÇEK kodunu da hiç çağırmaz, sadece Wine'ın kendi `reg` aracının
  export/import döngüsünü doğrudan test eder.
- Wine kurulu DEĞİLSE testler SKIP olarak işaretlenir (ok/fail değil) -
  bkz. @unittest.skipUnless.

Wine kurulu OLMAYAN bir sistemdeki davranış (WineAvailabilityIsHonestly
ReportedTests) ayrı bir makineye/CI'a ihtiyaç duymadan, bu makinede de
doğrulanıyor - `PATH` ortam değişkeni sadece o testin SÜRECİ içinde,
gerçek `wine` ikilisini GÖRMEYECEK şekilde geçici olarak daraltılıyor
(symlink'lerden oluşan ayrı bir dizin); gerçek /usr/bin/wine veya
~/.wine'a hiçbir şekilde dokunulmuyor, silinmiyor, değiştirilmiyor. Bu
sayede o test artık Wine kurulu bir makinede de SKIP EDİLMEDEN,
gerçekten çalışıp geçiyor - `_WINE_AVAILABLE` SADECE üstteki
WineRegistryExportImportRoundTripTests'in skip kararı için kullanılıyor
(gerçek `wine reg` komutlarını ÇALIŞTIRABİLMEK, PATH'te gizleyerek
taklit edilemeyecek bir şey - o yüzden o sınıfın skip'i hâlâ gerçek
makine durumuna bakıyor).

Encoding notu: Wine'ın `reg export`i CANLI DOĞRULANDI - çıktı gerçekten
UTF-16LE + BOM ("Windows Registry little-endian text", `file` komutuyla
doğrulandı). Dosya içeriği bu yüzden `iconv -f utf-16le -t utf-8` ile
host üzerinde dönüştürülüp kontrol ediliyor - ham baytları sandbox'a
çekip Python'da decode etmek YERİNE, çünkü export_file bir HOST yolu
(bkz. setUp - WINEPREFIX de, dolayısıyla bu dosya da host.run()
üzerinden oluşturuluyor, sandbox'ın yerel dosya sisteminde karşılığı
olmayabilir).

Çalıştırma (GTK gerekmez):
    PYTHONPATH=src python3 -m unittest tests.test_wine_registry_export_plan -v
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller import host  # noqa: E402

_WINE_AVAILABLE = host.command_exists("wine")

_TEST_KEY = r"HKCU\Software\PackWardenAuditTest"
_TEST_VALUE_NAME = "Marker"
_TEST_VALUE = "packwarden-registry-roundtrip-probe"


def _build_wine_free_path_dir() -> str:
    """Gerçek PATH'teki her dizindeki her ikili için, adı "wine" ile
    BAŞLAYANLAR HARİÇ, geçici bir dizine symlink kurar - sonuç, "wine"
    hiç kurulu değilmiş gibi davranan ama gerçek Wine ikilisine/
    kurulumuna (/usr/bin/wine, ~/.wine) HİÇ DOKUNMAYAN bir PATH'tir.
    Çağıran, işini bitirince bu dizini kendisi silmeli (bkz. tearDown)."""
    fake_dir = tempfile.mkdtemp(prefix="packwarden-no-wine-path-")
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not os.path.isdir(directory):
            continue
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        for name in entries:
            if name.startswith("wine"):
                continue
            link = os.path.join(fake_dir, name)
            if os.path.exists(link):
                continue
            try:
                os.symlink(os.path.join(directory, name), link)
            except OSError:
                pass
    return fake_dir


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
    """Wine'ın "kurulu değil" olarak doğru raporlandığını doğrular -
    bu makinede Wine GERÇEKTEN kurulu olsa bile, bu test artık asla
    skip edilmiyor: `PATH` ortam değişkeni SADECE bu testlerin sürdüğü
    süre boyunca, `wine` ikilisini içermeyecek bir geçici dizine
    ayarlanıyor (bkz. _build_wine_free_path_dir) - gerçek /usr/bin/wine
    ve ~/.wine testten önce de sonra da aynen yerinde kalıyor, hiçbir
    dosya silinmiyor/taşınmıyor, sadece bu sürecin PATH'i geçici olarak
    değişip tearDown'da hemen eski hâline dönüyor.

    Önceki tasarımda bu test, gerçek `_WINE_AVAILABLE`'a bakıp Wine
    kuruluyken kendini skipTest() ile atlıyordu ("üstteki asıl testler
    zaten çalışıyor" gerekçesiyle) - bu, Wine'ın kurulu OLDUĞU her
    geliştirme makinesinde kalıcı, beklenen bir skip'e yol açıyordu.
    PATH izolasyonu sayesinde artık hem Wine kurulu hem kurulu olmayan
    makinelerde aynı şekilde, skip edilmeden çalışıp geçiyor."""

    def setUp(self):
        self._fake_path_dir = _build_wine_free_path_dir()
        self._original_path = os.environ.get("PATH", "")

    def tearDown(self):
        os.environ["PATH"] = self._original_path
        shutil.rmtree(self._fake_path_dir, ignore_errors=True)

    def test_wine_is_reported_as_unavailable_when_hidden_from_path(self):
        os.environ["PATH"] = self._fake_path_dir
        self.assertFalse(
            host.command_exists("wine"),
            "wine ikilisi PATH'ten gizlenince command_exists gerçekten "
            "False dönmeli - WineRegistryExportImportRoundTripTests'in "
            "skipUnless kararının dayandığı tam da bu fonksiyon.",
        )

    def test_the_real_wine_installation_is_left_completely_untouched(self):
        real_wine_path_before = shutil.which("wine")
        if real_wine_path_before is None:
            self.skipTest(
                "Bu makinede gerçek bir Wine kurulumu yok - doğrulanacak "
                "bir 'gerçek kurulum' bulunmadığı için bu test anlamsız."
            )

        os.environ["PATH"] = self._fake_path_dir
        host.command_exists("wine")  # yukarıdaki testle aynı etkiyi tetikle
        os.environ["PATH"] = self._original_path

        self.assertEqual(
            real_wine_path_before, shutil.which("wine"),
            "Gerçek Wine kurulumunun yolu bu test yüzünden değişmemeli",
        )


if __name__ == "__main__":
    unittest.main()
