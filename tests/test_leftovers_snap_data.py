"""leftovers.py: Snap kullanıcı verisi (~/snap/<ad>/) tespiti.

Gerçek paket kaldırma veya gerçek kullanıcı verisi silme YAPILMAZ.
`os.path.expanduser` sahte bir ev dizinine yönlendirilir (gerçek
`~/snap`'e asla dokunulmaz); yıkıcı `remove_leftovers()` çağrıları da
yalnızca bu sahte dizindeki dosyalarla, gerçek sistemi etkilemeden
test edilir.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import os
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller import leftovers  # noqa: E402
from bulkuninstaller.backends.base import Package  # noqa: E402
from bulkuninstaller import backup as backup_mod  # noqa: E402

_real_expanduser = os.path.expanduser


def _fake_expanduser(fake_home):
    def expand(path):
        if path == "~":
            return fake_home
        if path.startswith("~/"):
            return os.path.join(fake_home, path[2:])
        return _real_expanduser(path)
    return expand


def _snap_pkg(snap_id):
    return Package(
        id=snap_id, name=snap_id, version="1.0", size=0,
        description="", source="snap",
    )


class SnapDataDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.patcher = mock.patch(
            "os.path.expanduser", side_effect=_fake_expanduser(self.home)
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def _make_snap_data(self, name, filename="data.bin", content=b"x" * 100):
        path = os.path.join(self.home, "snap", name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, filename), "wb") as f:
            f.write(content)
        return path

    def test_existing_snap_data_is_found(self):
        self._make_snap_data("gimp")
        groups = leftovers.find_package_leftovers([_snap_pkg("gimp")])
        categories = dict(groups)
        self.assertIn("Snap data", categories)
        found_paths = [item.path for item in categories["Snap data"]]
        self.assertEqual(
            found_paths, [os.path.join(self.home, "snap", "gimp")]
        )

    def test_missing_snap_data_path_returns_nothing(self):
        # ~/snap hiç oluşturulmadı - paket için hiçbir veri yok.
        groups = leftovers.find_package_leftovers([_snap_pkg("gimp")])
        categories = dict(groups)
        self.assertNotIn("Snap data", categories)

    def test_other_snaps_data_not_mistakenly_matched(self):
        # gimp VE vlc'nin ikisi de kurulu, ama yalnızca gimp
        # kaldırılıyor - vlc'nin verisi hiç dönmemeli.
        self._make_snap_data("gimp")
        self._make_snap_data("vlc")
        groups = leftovers.find_package_leftovers([_snap_pkg("gimp")])
        categories = dict(groups)
        found_paths = [item.path for item in categories["Snap data"]]
        self.assertEqual(len(found_paths), 1)
        self.assertTrue(found_paths[0].endswith(os.sep + "gimp"))
        self.assertFalse(any("vlc" in p for p in found_paths))

    def test_both_selected_snaps_each_get_own_data_only(self):
        self._make_snap_data("gimp")
        self._make_snap_data("vlc")
        groups = leftovers.find_package_leftovers(
            [_snap_pkg("gimp"), _snap_pkg("vlc")]
        )
        categories = dict(groups)
        found_paths = {item.path for item in categories["Snap data"]}
        self.assertEqual(
            found_paths,
            {
                os.path.join(self.home, "snap", "gimp"),
                os.path.join(self.home, "snap", "vlc"),
            },
        )

    def test_snap_root_itself_is_never_returned_as_a_leftover(self):
        # pkg.id boş string olsaydı os.path.join(snap_base, "") tam
        # olarak ~/snap'in KENDİSİNE eşit olurdu - bu asla bir
        # "kalıntı" olarak dönmemeli.
        os.makedirs(os.path.join(self.home, "snap"), exist_ok=True)
        groups = leftovers.find_package_leftovers([_snap_pkg("")])
        categories = dict(groups)
        self.assertNotIn("Snap data", categories)

    def test_unsafe_traversal_in_package_id_is_rejected(self):
        # Paket kimliği kullanıcı girdisi gibi ele alınıyor - ".."
        # içerse bile ~/snap dışına (ör. ~/dışarı) çıkamamalı. Gerçek
        # snap adları bunu zaten içeremez ama savunma amaçlı test.
        outside = os.path.join(self.home, "disari")
        os.makedirs(outside, exist_ok=True)
        with open(os.path.join(outside, "sentinel.txt"), "w") as f:
            f.write("dokunma")
        groups = leftovers.find_package_leftovers(
            [_snap_pkg("../disari")]
        )
        categories = dict(groups)
        self.assertNotIn("Snap data", categories)
        # victim dosyasi hala duruyor olmali (zaten bulunamadi, silinmedi).
        self.assertTrue(os.path.isfile(os.path.join(outside, "sentinel.txt")))


class SnapDataBackupTests(unittest.TestCase):
    """Snap verisinin YALNIZCA listede görünmesi değil, gerçekten
    yedek arşivine dahil edildiği doğrulanır."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.patcher = mock.patch(
            "os.path.expanduser", side_effect=_fake_expanduser(self.home)
        )
        self.patcher.start()
        self.backup_dest = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        self.backup_dest.cleanup()

    def test_snap_data_is_actually_included_in_the_backup_archive(self):
        snap_dir = os.path.join(self.home, "snap", "gimp")
        os.makedirs(snap_dir, exist_ok=True)
        marker_path = os.path.join(snap_dir, "gimprc")
        with open(marker_path, "w", encoding="utf-8") as f:
            f.write("kullanici ayarlari")

        groups = leftovers.find_package_leftovers([_snap_pkg("gimp")])
        all_leftovers = [item for _cat, items in groups for item in items]
        self.assertTrue(
            any(item.path == snap_dir for item in all_leftovers),
            "Snap verisi bulunan kalıntılar arasında olmalı",
        )

        dest = backup_mod.create_backup(
            [_snap_pkg("gimp")], all_leftovers, base=self.backup_dest.name
        )
        archive_path = os.path.join(dest, "dosyalar.tar.gz")
        self.assertTrue(
            os.path.isfile(archive_path), "Yedek arşivi oluşturulmalıydı"
        )
        with tarfile.open(archive_path, "r:gz") as tar:
            names = tar.getnames()
        self.assertTrue(
            any(name.endswith("gimprc") for name in names),
            f"gimprc arşivde bulunamadı, içerik: {names}",
        )


class RemoveLeftoversSnapSafetyTests(unittest.TestCase):
    """remove_leftovers() GERÇEKTEN çağrılır ama yalnızca sahte ev
    dizinindeki dosyalara - gerçek sistem asla etkilenmez."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.patcher = mock.patch(
            "os.path.expanduser", side_effect=_fake_expanduser(self.home)
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_snap_root_cannot_be_deleted_even_if_passed_directly(self):
        snap_root = os.path.join(self.home, "snap")
        os.makedirs(snap_root, exist_ok=True)
        with open(os.path.join(snap_root, "sentinel.txt"), "w") as f:
            f.write("tum snap'lerin ortak kokü")
        item = leftovers.Leftover(path=snap_root, size=0)
        errors = leftovers.remove_leftovers([item])
        self.assertTrue(errors, "~/snap kökü reddedilmeli")
        self.assertTrue(os.path.isdir(snap_root))
        self.assertTrue(os.path.isfile(os.path.join(snap_root, "sentinel.txt")))

    def test_normal_snap_app_data_can_still_be_removed(self):
        # Gerileme kontrolü: gerçek, tek bir uygulamanın verisi hâlâ
        # normal şekilde (çöp kutusuna) kaldırılabiliyor mu?
        with mock.patch(
            "bulkuninstaller.leftovers.prefs.get", return_value="trash"
        ), mock.patch(
            "bulkuninstaller.leftovers.host.run"
        ) as run_mock:
            run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            snap_dir = os.path.join(self.home, "snap", "gimp")
            os.makedirs(snap_dir, exist_ok=True)
            item = leftovers.Leftover(path=snap_dir, size=0)
            errors = leftovers.remove_leftovers([item])
        self.assertEqual(errors, [])
        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args[0][0], ["gio", "trash", snap_dir])


if __name__ == "__main__":
    unittest.main()
