"""backends/flatpak.py: kurulum kapsamı (system/user/özel) ayrımı.

Gerçek `flatpak` HİÇ çalıştırılmaz - `host.run` taklit edilir;
`_license`/`_install_date`/`_install_path`/`developers_for` da taklit
edilir (gerçek dosya/ağ erişimi yok). "installation" sütununun varlığı
ve "system" değeri bu makinede GERÇEK, kurulu bir flatpak ile canlı
doğrulandı (`flatpak list --columns=installation` çıktısı "system"
verdi) - ama bu makinede hiç kullanıcı-kapsamlı kurulum olmadığı için
"user" değerinin birebir bu dizeyle geldiği VE aynı uygulamanın iki
kapsamda aynı anda bulunduğu senaryo canlı doğrulanamadı, yalnızca
resmi Flatpak/Flathub belgelerine dayanıyor.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller.backends.flatpak import FlatpakBackend  # noqa: E402


def _row(app_id, name, installation, version="1.0", size="10 MB", desc="d", origin="flathub"):
    return "\t".join([app_id, name, version, size, desc, origin, installation])


class ListPackagesScopeTests(unittest.TestCase):
    def _run_with_list_output(self, text: str) -> list:
        def fake_run(argv, timeout=None):
            if argv[2:4] == ["flatpak", "list"]:
                return mock.Mock(returncode=0, stdout=text)
            if argv[2:4] == ["flatpak", "info"]:
                return mock.Mock(returncode=1, stdout="")  # lisans yok
            return mock.Mock(returncode=1, stdout="")

        backend = FlatpakBackend()
        with mock.patch(
            "bulkuninstaller.backends.flatpak.host.run", side_effect=fake_run
        ), mock.patch(
            "bulkuninstaller.backends.flatpak.developers_for", return_value={}
        ), mock.patch(
            "bulkuninstaller.backends.flatpak.licenses_for", return_value={}
        ), mock.patch.object(
            FlatpakBackend, "_install_date", staticmethod(lambda app_id: None)
        ), mock.patch.object(
            FlatpakBackend, "_install_path", staticmethod(lambda app_id: "")
        ):
            return backend.list_packages()

    def test_system_only_installation_id_unchanged(self):
        text = _row("org.mozilla.firefox", "Firefox", "system")
        pkgs = self._run_with_list_output(text)
        self.assertEqual(len(pkgs), 1)
        self.assertEqual(pkgs[0].id, "org.mozilla.firefox")

    def test_user_only_installation_id_unchanged(self):
        text = _row("org.mozilla.firefox", "Firefox", "user")
        pkgs = self._run_with_list_output(text)
        self.assertEqual(len(pkgs), 1)
        self.assertEqual(pkgs[0].id, "org.mozilla.firefox")

    def test_same_app_in_both_system_and_user_gets_distinct_ids(self):
        text = "\n".join([
            _row("org.mozilla.firefox", "Firefox", "system"),
            _row("org.mozilla.firefox", "Firefox", "user"),
        ])
        pkgs = self._run_with_list_output(text)
        ids = sorted(p.id for p in pkgs)
        self.assertEqual(
            ids, ["org.mozilla.firefox:system", "org.mozilla.firefox:user"]
        )
        self.assertEqual(len(set(ids)), 2)

    def test_same_app_in_custom_named_installation_also_distinguished(self):
        # /etc/flatpak/installations.d altında tanımlı özel bir
        # sistem kurulumu (ör. "work") - belgelenmiş ama bu makinede
        # canlı doğrulanamadı.
        text = "\n".join([
            _row("org.mozilla.firefox", "Firefox", "system"),
            _row("org.mozilla.firefox", "Firefox", "work"),
        ])
        pkgs = self._run_with_list_output(text)
        ids = sorted(p.id for p in pkgs)
        self.assertEqual(
            ids, ["org.mozilla.firefox:system", "org.mozilla.firefox:work"]
        )

    def test_unrelated_apps_different_ids_not_merged(self):
        text = "\n".join([
            _row("org.mozilla.firefox", "Firefox", "system"),
            _row("com.spotify.Client", "Spotify", "system"),
        ])
        pkgs = self._run_with_list_output(text)
        ids = sorted(p.id for p in pkgs)
        self.assertEqual(ids, ["com.spotify.Client", "org.mozilla.firefox"])

    def test_missing_installation_field_on_collision_falls_back_to_bare_id(self):
        text = "\n".join([
            "org.mozilla.firefox\tFirefox\t1.0\t10 MB\td\tflathub\t",  # bos installation
            _row("org.mozilla.firefox", "Firefox", "user"),
        ])
        pkgs = self._run_with_list_output(text)
        ids = sorted(p.id for p in pkgs)
        self.assertIn("org.mozilla.firefox:user", ids)
        self.assertIn("org.mozilla.firefox", ids)

    def test_both_colliding_entries_missing_installation_stay_both_bare(self):
        text = "\n".join([
            "org.mozilla.firefox\tFirefox\t1.0\t10 MB\td\tflathub\t",
            "org.mozilla.firefox\tFirefox\t1.0\t10 MB\td\tflathub\t",
        ])
        pkgs = self._run_with_list_output(text)
        ids = [p.id for p in pkgs]
        self.assertEqual(ids, ["org.mozilla.firefox", "org.mozilla.firefox"])

    def test_empty_output_returns_no_packages(self):
        self.assertEqual(self._run_with_list_output(""), [])

    def test_list_failure_returns_empty_list(self):
        backend = FlatpakBackend()
        with mock.patch("bulkuninstaller.backends.flatpak.host.run") as run_mock:
            run_mock.return_value = mock.Mock(returncode=1, stdout="")
            self.assertEqual(backend.list_packages(), [])


class RemoveArgvScopeTests(unittest.TestCase):
    """remove_argv()'nin etiketli id'leri doğru bayrağa çevirdiğini,
    etiketsiz/karışık durumlarda yanlış bayrak DAYATMADIĞINI doğrular.
    "flatpak uninstall" gerçekten çalıştırılmaz."""

    def test_plain_single_scope_id_gets_no_flag(self):
        # Geriye dönük uyumluluk: çakışmayan sıradan bir uygulama -
        # düzeltmeden ÖNCEki argv ile birebir aynı olmalı.
        backend = FlatpakBackend()
        argv = backend.remove_argv(["org.mozilla.firefox"])
        self.assertEqual(
            argv, ["flatpak", "uninstall", "-y", "--noninteractive", "org.mozilla.firefox"]
        )

    def test_system_tagged_id_gets_system_flag(self):
        backend = FlatpakBackend()
        argv = backend.remove_argv(["org.mozilla.firefox:system"])
        self.assertIn("--system", argv)
        self.assertIn("org.mozilla.firefox", argv)
        self.assertNotIn("org.mozilla.firefox:system", argv)  # etiket sizilmeli

    def test_user_tagged_id_gets_user_flag(self):
        backend = FlatpakBackend()
        argv = backend.remove_argv(["org.mozilla.firefox:user"])
        self.assertIn("--user", argv)
        self.assertIn("org.mozilla.firefox", argv)

    def test_custom_named_installation_uses_installation_flag(self):
        backend = FlatpakBackend()
        argv = backend.remove_argv(["org.mozilla.firefox:work"])
        self.assertIn("--installation=work", argv)

    def test_selecting_system_copy_does_not_mention_user_flag(self):
        backend = FlatpakBackend()
        argv = backend.remove_argv(["org.mozilla.firefox:system"])
        self.assertNotIn("--user", argv)

    def test_selecting_user_copy_does_not_mention_system_flag(self):
        backend = FlatpakBackend()
        argv = backend.remove_argv(["org.mozilla.firefox:user"])
        self.assertNotIn("--system", argv)

    def test_two_ids_same_scope_share_one_flag(self):
        backend = FlatpakBackend()
        argv = backend.remove_argv(
            ["org.mozilla.firefox:system", "com.spotify.Client:system"]
        )
        self.assertEqual(argv.count("--system"), 1)
        self.assertIn("org.mozilla.firefox", argv)
        self.assertIn("com.spotify.Client", argv)

    def test_mixed_scopes_adds_no_flag_rather_than_guess_wrong(self):
        # Iki FARKLI kapsam ayni cagriya karisirsa - "flatpak uninstall"
        # tek cagrida tek kapsamla calisir, hangisini secersek digerini
        # yanlis hedefleyebilirdik - bu yuzden HICBIR bayrak eklenmez,
        # flatpak'in kendi "birden fazla kurulumda varsa hata ver"
        # guvenlik onlemine dusulur.
        backend = FlatpakBackend()
        argv = backend.remove_argv(
            ["org.mozilla.firefox:system", "com.spotify.Client:user"]
        )
        self.assertNotIn("--system", argv)
        self.assertNotIn("--user", argv)
        self.assertIn("org.mozilla.firefox", argv)
        self.assertIn("com.spotify.Client", argv)

    def test_tagged_and_untagged_mixed_adds_no_flag(self):
        # Etiketli bir id, etiketsiz (sıradan) biriyle AYNI çağrıya
        # karışırsa, etiketsiz olana yanlışlıkla bir kapsam
        # DAYATILMAMALI - bayrak hiç eklenmemeli.
        backend = FlatpakBackend()
        argv = backend.remove_argv(
            ["org.mozilla.firefox:system", "com.spotify.Client"]
        )
        self.assertNotIn("--system", argv)
        self.assertNotIn("--user", argv)

    def test_ids_are_not_shell_joined(self):
        backend = FlatpakBackend()
        argv = backend.remove_argv(["pkg; rm -rf ~"])
        self.assertIn("pkg; rm -rf ~", argv)
        self.assertEqual(argv[-1], "pkg; rm -rf ~")

    def test_delete_data_flag_is_never_added(self):
        # leftovers.py kendi genel artık-dosya akışını kullanıyor -
        # flatpak'in kendi --delete-data'sı kasıtlı olarak eklenmiyor.
        backend = FlatpakBackend()
        argv = backend.remove_argv(["org.mozilla.firefox:system"])
        self.assertNotIn("--delete-data", argv)

    def test_needs_root_is_false(self):
        self.assertFalse(FlatpakBackend.needs_root)


if __name__ == "__main__":
    unittest.main()
