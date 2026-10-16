"""removal.py: `removed_flatpak_ids` / `affected_flatpak_apps`
karşılaştırmasının, mimariye-nitelikli (çift kapsam) kimliklerle
tutarlı çalışması.

Gerçek `flatpak` HİÇ çalıştırılmaz - `_installed_flatpak_ids()` ve
`_clean_unused_flatpak_data()` tamamen taklit edilir. `RemovalWindow`
gerçek bir sınıf olarak kurulur; arka plan iş parçacığı senkron
çalışacak şekilde taklit edilir, `GLib.idle_add` yalnızca ÇAĞRI
ARGÜMANLARINI yakalamak için taklit edilir (gerçekten planlanmaz).

Çalıştırma (GTK gerektiği için ana sistemde):
    flatpak-spawn --host env PYTHONPATH=/mnt/D/packwarden/src \
        python3 -m unittest tests.test_removal_flatpak_scope_mismatch -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from bulkuninstaller.backends.base import Package, RemoveResult  # noqa: E402
from bulkuninstaller import removal  # noqa: E402


def _pkg(source, name, pkg_id=None):
    return Package(
        id=pkg_id or name, name=name, version="1.0", size=0,
        description="", source=source,
    )


class _Item:
    def __init__(self, pkg):
        self.pkg = pkg


class _ImmediateThread:
    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


class _FakeFlatpakBackend:
    id = "flatpak"

    def remove(self, ids):
        return RemoveResult(ok=True, output="")


def _make_main_window():
    win = Gtk.Window()
    win._backends = [_FakeFlatpakBackend()]
    win._launcher_map = {}
    return win


class FlatpakScopeMismatchTests(unittest.TestCase):
    def setUp(self):
        self.close_running_patcher = mock.patch(
            "bulkuninstaller.removal.usage.close_running", return_value=False
        )
        self.close_running_patcher.start()

        self.thread_patcher = mock.patch(
            "bulkuninstaller.removal.threading.Thread",
            side_effect=lambda target=None, daemon=None: _ImmediateThread(target),
        )
        self.thread_patcher.start()

        self.idle_add_patcher = mock.patch(
            "bulkuninstaller.removal.GLib.idle_add"
        )
        self.idle_add_mock = self.idle_add_patcher.start()

        self.clean_unused_patcher = mock.patch(
            "bulkuninstaller.removal._clean_unused_flatpak_data"
        )
        self.clean_unused_patcher.start()

    def tearDown(self):
        self.close_running_patcher.stop()
        self.thread_patcher.stop()
        self.idle_add_patcher.stop()
        self.clean_unused_patcher.stop()

    def _affected_apps(self):
        """_on_done'a GLib.idle_add ile planlanan affected_flatpak_apps
        argümanını (son pozisyonel argüman) döner."""
        self.assertTrue(self.idle_add_mock.called)
        args = self.idle_add_mock.call_args[0]
        return args[-1]

    def test_removing_both_scopes_of_the_same_app_is_not_a_false_positive(self):
        # Kullanıcı AYNI uygulamanın hem sistem hem kullanıcı kopyasını
        # BİLEREK seçip kaldırıyor - ikisi de gerçekten kayboluyor.
        pkg_system = _pkg("flatpak", "Firefox", "org.mozilla.firefox:system")
        pkg_user = _pkg("flatpak", "Firefox", "org.mozilla.firefox:user")
        main_win = _make_main_window()
        win = removal.RemovalWindow(main_win, [_Item(pkg_system), _Item(pkg_user)])

        with mock.patch(
            "bulkuninstaller.removal._installed_flatpak_ids",
            side_effect=[
                {"org.mozilla.firefox", "org.bar.Other"},  # before
                {"org.bar.Other"},                          # after
            ],
        ):
            win._start(backup_base=None)

        self.assertEqual(self._affected_apps(), set())

    def test_removing_one_scope_while_other_remains_is_not_affected(self):
        pkg_system = _pkg("flatpak", "Firefox", "org.mozilla.firefox:system")
        main_win = _make_main_window()
        win = removal.RemovalWindow(main_win, [_Item(pkg_system)])

        with mock.patch(
            "bulkuninstaller.removal._installed_flatpak_ids",
            side_effect=[
                {"org.mozilla.firefox", "org.bar.Other"},  # before
                {"org.mozilla.firefox", "org.bar.Other"},  # after (user kopyası hala var)
            ],
        ):
            win._start(backup_base=None)

        self.assertEqual(self._affected_apps(), set())

    def test_genuine_side_effect_on_an_unrelated_app_is_still_reported(self):
        # Gerçek bir yan etki: kullanıcı yalnızca "org.mozilla.firefox"u
        # (etiketsiz) kaldırıyor, ama --unused/prune YAN ETKİ olarak
        # TAMAMEN ALAKASIZ bir uygulamayı da götürüyor - bu HÂLÂ
        # raporlanmalı, düzeltme bunu gizlememeli.
        pkg = _pkg("flatpak", "Firefox", "org.mozilla.firefox")
        main_win = _make_main_window()
        win = removal.RemovalWindow(main_win, [_Item(pkg)])

        with mock.patch(
            "bulkuninstaller.removal._installed_flatpak_ids",
            side_effect=[
                {"org.mozilla.firefox", "org.unrelated.Thing", "org.bar.Other"},
                {"org.bar.Other"},  # firefox VE unrelated.Thing ikisi de gitti
            ],
        ):
            win._start(backup_base=None)

        self.assertEqual(self._affected_apps(), {"org.unrelated.Thing"})

    def test_untagged_removal_still_works_as_before(self):
        # Gerileme kontrolü: hiç etiketlenmemiş, sıradan tek-kapsamlı
        # bir kaldırma - davranış öncekiyle birebir aynı kalmalı.
        pkg = _pkg("flatpak", "Spotify", "com.spotify.Client")
        main_win = _make_main_window()
        win = removal.RemovalWindow(main_win, [_Item(pkg)])

        with mock.patch(
            "bulkuninstaller.removal._installed_flatpak_ids",
            side_effect=[
                {"com.spotify.Client", "org.bar.Other"},
                {"org.bar.Other"},
            ],
        ):
            win._start(backup_base=None)

        self.assertEqual(self._affected_apps(), set())


if __name__ == "__main__":
    unittest.main()
