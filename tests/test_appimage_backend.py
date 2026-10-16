"""backends/appimage.py: yol-tabanlı kimlik, kaldırma ve tarama.

Gerçek sistem dosyaları HİÇ silinmez/değiştirilmez - yalnızca
`tempfile` ile oluşturulan, test içinde yaratılıp test sonunda
temizlenen geçici dosyalar kullanılır. İzin hataları GERÇEK dosya
izinlerine (ör. testi çalıştıran kullanıcının root olup olmaması)
bağımlı olmadan `os.remove`'u taklit ederek üretilir.

Bu makinede hiç gerçek .AppImage dosyası yok - canlı bir AppImage'e
karşı doğrulanmadı, yalnızca sahte/geçici dosyalarla test edildi.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller.backends.appimage import AppImageBackend  # noqa: E402


class AppImageTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_appimage(self, dirname, filename, content=b"fake appimage bytes"):
        folder = os.path.join(self.tmp.name, dirname)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        with open(path, "wb") as f:
            f.write(content)
        return path

    def _list_with_scan_dirs(self, scan_dirs: tuple[str, ...]) -> list:
        backend = AppImageBackend()
        with mock.patch(
            "bulkuninstaller.backends.appimage.SCAN_DIRS", scan_dirs
        ):
            return backend.list_packages()

    def _scan_with_scan_dirs(self, scan_dirs: tuple[str, ...]) -> list:
        backend = AppImageBackend()
        with mock.patch(
            "bulkuninstaller.backends.appimage.SCAN_DIRS", scan_dirs
        ):
            return backend._scan()


class PackageIdentityTests(AppImageTestBase):
    def test_id_is_the_full_file_path(self):
        path = self._make_appimage("apps1", "Foo.AppImage")
        pkgs = self._list_with_scan_dirs((f"{self.tmp.name}/apps1",))
        self.assertEqual(len(pkgs), 1)
        self.assertEqual(pkgs[0].id, path)
        self.assertEqual(pkgs[0].install_path, path)
        # Ad, uzantısı atılmış dosya adı olmalı - tam yol DEĞİL.
        self.assertEqual(pkgs[0].name, "Foo")

    def test_same_display_name_in_two_folders_gets_distinct_ids(self):
        path1 = self._make_appimage("apps1", "Foo.AppImage")
        path2 = self._make_appimage("apps2", "Foo.AppImage")
        pkgs = self._list_with_scan_dirs((
            f"{self.tmp.name}/apps1", f"{self.tmp.name}/apps2",
        ))
        ids = sorted(p.id for p in pkgs)
        self.assertEqual(ids, sorted([path1, path2]))
        self.assertEqual(len(set(ids)), 2)
        # İkisinin de GÖRÜNÜR adı aynı olabilir - bu bir hata değil,
        # kimlikleri (tam yol) farklı olduğu için kaldırma hedefi
        # yine de belirsiz değil.
        names = {p.name for p in pkgs}
        self.assertEqual(names, {"Foo"})

    def test_no_appimage_files_returns_empty_list(self):
        os.makedirs(os.path.join(self.tmp.name, "empty_dir"), exist_ok=True)
        pkgs = self._list_with_scan_dirs((f"{self.tmp.name}/empty_dir",))
        self.assertEqual(pkgs, [])

    def test_nonexistent_scan_dir_is_skipped_without_crashing(self):
        pkgs = self._list_with_scan_dirs((f"{self.tmp.name}/does_not_exist",))
        self.assertEqual(pkgs, [])

    def test_non_appimage_files_are_ignored(self):
        self._make_appimage("apps1", "notes.txt")
        pkgs = self._list_with_scan_dirs((f"{self.tmp.name}/apps1",))
        self.assertEqual(pkgs, [])

    def test_case_insensitive_extension_is_recognized(self):
        path = self._make_appimage("apps1", "Bar.appimage")
        pkgs = self._list_with_scan_dirs((f"{self.tmp.name}/apps1",))
        self.assertEqual([p.id for p in pkgs], [path])
        self.assertEqual(pkgs[0].name, "Bar")

    def test_has_desktop_entry_is_never_claimed(self):
        # Package.has_desktop_entry: "backend belirlediyse True/False,
        # aksi None". AppImage bunu hiç belirlemiyor (None kalıyor) -
        # yani bu backend hiçbir zaman bir masaüstü girdisi/kısayolu
        # VAR OLDUĞUNU iddia etmiyor. _ctx_desktop_shortcut'ın KENDİSİ
        # (window.py, GTK) burada test edilmiyor - yalnızca AppImage'in
        # kendi genel sözleşmesi doğrulanıyor: pkg.id/pkg.name tam bir
        # dosya yolu ya da çıplak ad olduğu için, .desktop dosyalarını
        # kısa "desktop-id" dizeleriyle eşleştiren herhangi bir arama
        # yapısal olarak bunu bulamaz.
        self._make_appimage("apps1", "Foo.AppImage")
        pkg = self._list_with_scan_dirs((f"{self.tmp.name}/apps1",))[0]
        self.assertIsNone(pkg.has_desktop_entry)


class ScanBehaviorTests(AppImageTestBase):
    def test_scan_returns_sorted_full_paths(self):
        path_b = self._make_appimage("apps1", "B.AppImage")
        path_a = self._make_appimage("apps1", "A.AppImage")
        found = self._scan_with_scan_dirs((f"{self.tmp.name}/apps1",))
        self.assertEqual(found, sorted([path_a, path_b]))

    def test_is_available_false_when_nothing_found(self):
        backend = AppImageBackend()
        with mock.patch(
            "bulkuninstaller.backends.appimage.SCAN_DIRS",
            (f"{self.tmp.name}/empty",),
        ):
            self.assertFalse(backend.is_available())

    def test_is_available_true_when_something_found(self):
        self._make_appimage("apps1", "Foo.AppImage")
        backend = AppImageBackend()
        with mock.patch(
            "bulkuninstaller.backends.appimage.SCAN_DIRS",
            (f"{self.tmp.name}/apps1",),
        ):
            self.assertTrue(backend.is_available())


class RemoveTests(AppImageTestBase):
    """`remove()` GERÇEKTEN çağrılır ama yalnızca bu test sınıfının
    kendi oluşturup temizlediği geçici dosyalarla - gerçek sistem
    dosyalarına asla dokunulmaz."""

    def test_successful_removal_deletes_only_the_selected_file(self):
        target = self._make_appimage("apps1", "ToRemove.AppImage")
        keep = self._make_appimage("apps1", "KeepMe.AppImage")
        backend = AppImageBackend()
        result = backend.remove([target])
        self.assertTrue(result.ok)
        self.assertEqual(result.failed_ids, [])
        self.assertFalse(os.path.exists(target), "Hedef dosya silinmeliydi")
        self.assertTrue(
            os.path.exists(keep), "Seçilmeyen dosyaya dokunulmamalıydı"
        )

    def test_missing_file_returns_error_result_without_crashing(self):
        backend = AppImageBackend()
        missing_path = os.path.join(self.tmp.name, "nonexistent.AppImage")
        result = backend.remove([missing_path])  # exception sızdırmamalı
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_ids, [missing_path])
        self.assertIn(missing_path, result.output)

    def test_permission_error_is_handled_gracefully(self):
        # Gerçek dosya izinlerine (testi çalıştıran kullanıcının root
        # olup olmamasına) bağımlı olmamak için os.remove GÜVENİLİR
        # biçimde taklit edilir.
        target = self._make_appimage("apps1", "Locked.AppImage")
        backend = AppImageBackend()
        with mock.patch(
            "bulkuninstaller.backends.appimage.os.remove",
            side_effect=PermissionError("İzin reddedildi"),
        ):
            result = backend.remove([target])  # exception sızdırmamalı
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_ids, [target])
        self.assertTrue(os.path.exists(target), "Dosya silinmemiş olmalı")

    def test_removing_a_symlink_preserves_its_target(self):
        real_target = self._make_appimage("real", "RealApp.AppImage")
        link_path = os.path.join(self.tmp.name, "LinkToApp.AppImage")
        os.symlink(real_target, link_path)
        backend = AppImageBackend()
        result = backend.remove([link_path])
        self.assertTrue(result.ok)
        self.assertFalse(
            os.path.lexists(link_path), "Sembolik bağın kendisi silinmeliydi"
        )
        self.assertTrue(
            os.path.isfile(real_target),
            "Bağın İŞARET ETTİĞİ gerçek dosyaya dokunulmamalıydı",
        )

    def test_multiple_ids_each_report_their_own_outcome(self):
        good = self._make_appimage("apps1", "Good.AppImage")
        missing = os.path.join(self.tmp.name, "apps1", "Missing.AppImage")
        backend = AppImageBackend()
        result = backend.remove([good, missing])
        self.assertFalse(result.ok)  # bir tanesi başarısız oldu
        self.assertEqual(result.failed_ids, [missing])
        self.assertFalse(os.path.exists(good))  # yine de silinmişti


if __name__ == "__main__":
    unittest.main()
