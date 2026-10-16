"""updater.py: güncelleme kurulumunun güvenli devreye alma/geri dönüş
mekanizması (_reconcile_install_and_backup, _activate_new_install,
_install_lock).

Gerçek PackWarden kurulumu HİÇ DEĞİŞTİRİLMEZ - `INSTALL_DIR` her testte
geçici bir dizine taklit edilir (`mock.patch`). Gerçek ağ isteği de
yapılmaz - `_fetch_latest_release`/`_fetch_checksum`/`urllib.request.
urlopen` uçtan uca testlerde taklit edilir.

ATOMİKLİK İDDİASI YOK: bu testler, iki `os.rename` arasında sürecin
GERÇEKTEN öldürülmesini (SIGKILL/güç kesintisi) simüle EDEMEZ - Python
kodu o anı hiç göremez. Test edilen şey, normal (yakalanabilir) hata
yollarında geri dönüşün çalıştığı ve bir SONRAKİ çağrının önceki
kesintiyi doğru şekilde bulup düzelttiğidir - gerçek kesinti anının
kendisi değil.

Çalıştırma (GTK gerekmez, bare sandbox'ta çalışır):
    PYTHONPATH=src python3 -m unittest tests.test_updater_safe_install -v
"""

import hashlib
import io
import os
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller import updater  # noqa: E402


def _make_install_dir(path: str, version_marker: str) -> None:
    os.makedirs(os.path.join(path, "src", "bulkuninstaller"), exist_ok=True)
    with open(os.path.join(path, "install.sh"), "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\n")
    with open(
        os.path.join(path, "src", "bulkuninstaller", "__init__.py"),
        "w", encoding="utf-8",
    ) as f:
        f.write(f'VERSION = "{version_marker}"\n')


def _read_version_marker(path: str) -> str:
    with open(
        os.path.join(path, "src", "bulkuninstaller", "__init__.py"),
        encoding="utf-8",
    ) as f:
        return f.read()


def _build_fake_release_tarball(version_marker: str) -> bytes:
    buf = io.BytesIO()
    root = "MuhammetHub111-packwarden-abc123"
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (
            (f"{root}/install.sh", b"#!/bin/sh\n"),
            (
                f"{root}/src/bulkuninstaller/__init__.py",
                f'VERSION = "{version_marker}"\n'.encode(),
            ),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _FakeHttpResponse:
    """urllib.request.urlopen()'ın tarball indirme çağrısı için yeterli
    taklit - gerçek kod sadece .headers.get(...) ve .read(n) kullanıyor."""

    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)
        self.headers = {"Content-Length": str(len(data))}

    def read(self, n):
        return self._buf.read(n)


class _InstallDirFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.install_dir = os.path.join(self.tmp.name, "packwarden")
        self.patcher = mock.patch(
            "bulkuninstaller.updater.INSTALL_DIR", self.install_dir
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        # Kilit bir testte alınıp hatayla serbest bırakılmazsa sonraki
        # testleri de bozmasın diye savunma amaçlı sıfırla.
        if updater._install_lock.locked():
            updater._install_lock.release()


class ActivateNewInstallTests(_InstallDirFixture):
    def _make_source(self, marker: str) -> str:
        source = os.path.join(self.tmp.name, "staging", "packwarden-new")
        _make_install_dir(source, marker)
        return source

    def test_fresh_install_with_no_previous_version(self):
        source = self._make_source("new")
        updater._activate_new_install(source)
        self.assertIn("new", _read_version_marker(self.install_dir))
        self.assertFalse(os.path.exists(updater._backup_dir()))

    def test_normal_update_replaces_old_version_and_cleans_up_backup(self):
        _make_install_dir(self.install_dir, "old")
        source = self._make_source("new")
        updater._activate_new_install(source)
        self.assertIn("new", _read_version_marker(self.install_dir))
        self.assertFalse(os.path.exists(updater._backup_dir()))

    def test_activation_failure_restores_the_previous_version(self):
        _make_install_dir(self.install_dir, "old")
        source = self._make_source("new")
        real_rename = os.rename

        def flaky_rename(src, dst):
            if src == source:
                raise OSError(28, "No space left on device")
            return real_rename(src, dst)

        with mock.patch(
            "bulkuninstaller.updater.os.rename", side_effect=flaky_rename
        ), mock.patch(
            "bulkuninstaller.updater.shutil.move",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(OSError):
                updater._activate_new_install(source)

        # Eski sürüm GERİ geldi - hem yarıda kalmadı hem kaybolmadı.
        self.assertTrue(os.path.isdir(self.install_dir))
        self.assertIn("old", _read_version_marker(self.install_dir))
        self.assertFalse(os.path.exists(updater._backup_dir()))

    def test_cross_filesystem_fallback_still_activates_successfully(self):
        # os.rename "cross-device" hatasıyla başarısız olursa,
        # shutil.move'un kopya+sil geri dönüşü hâlâ ÇALIŞMALI - bu,
        # staging dizini gerçekten farklı bir dosya sisteminde kaldığında
        # (bkz. _staging_parent'ın yazılamaz-üst-dizin durumu) olur.
        _make_install_dir(self.install_dir, "old")
        source = self._make_source("new")
        real_rename = os.rename

        def cross_device_rename(src, dst):
            if src == source:
                raise OSError(18, "Invalid cross-device link")
            return real_rename(src, dst)

        with mock.patch(
            "bulkuninstaller.updater.os.rename", side_effect=cross_device_rename
        ):
            updater._activate_new_install(source)

        self.assertIn("new", _read_version_marker(self.install_dir))
        self.assertFalse(os.path.exists(updater._backup_dir()))

    def test_pre_existing_backup_at_activation_time_is_not_blindly_overwritten_blind(self):
        # _reconcile_install_and_backup() normalde bunu önceden temizler;
        # savunma amaçlı _activate_new_install'ın KENDİSİ de eski bir
        # yedeği görürse önce onu temizleyip (mevcut kurulum sağlıklıysa)
        # devam ediyor - körü körüne üzerine os.rename denemiyor (ki bu
        # FileExistsError ile patlardı).
        _make_install_dir(self.install_dir, "old")
        _make_install_dir(updater._backup_dir(), "stale-debris")
        source = self._make_source("new")
        updater._activate_new_install(source)
        self.assertIn("new", _read_version_marker(self.install_dir))
        self.assertFalse(os.path.exists(updater._backup_dir()))


class ReconcileInstallAndBackupTests(_InstallDirFixture):
    def test_no_backup_present_is_a_no_op(self):
        _make_install_dir(self.install_dir, "current")
        logs = []
        updater._reconcile_install_and_backup(logs.append)
        self.assertEqual(logs, [])
        self.assertIn("current", _read_version_marker(self.install_dir))

    def test_healthy_install_with_stale_backup_cleans_up_the_backup(self):
        _make_install_dir(self.install_dir, "current")
        _make_install_dir(updater._backup_dir(), "previous-debris")
        updater._reconcile_install_and_backup(lambda m: None)
        self.assertFalse(os.path.exists(updater._backup_dir()))
        self.assertIn("current", _read_version_marker(self.install_dir))

    def test_missing_install_with_valid_backup_self_heals(self):
        _make_install_dir(updater._backup_dir(), "restored-from-backup")
        logs = []
        updater._reconcile_install_and_backup(logs.append)
        self.assertTrue(updater._is_valid_install(self.install_dir))
        self.assertIn("restored-from-backup", _read_version_marker(self.install_dir))
        self.assertFalse(os.path.exists(updater._backup_dir()))
        self.assertTrue(any("interrupted update" in m for m in logs))

    def test_both_install_and_backup_unhealthy_quarantines_without_deleting(self):
        os.makedirs(updater._backup_dir())
        with open(
            os.path.join(updater._backup_dir(), "some-leftover-file"),
            "w", encoding="utf-8",
        ) as f:
            f.write("mystery data")

        logs = []
        updater._reconcile_install_and_backup(logs.append)

        self.assertFalse(os.path.exists(self.install_dir))
        self.assertFalse(os.path.exists(updater._backup_dir()))
        prefix = os.path.basename(updater._backup_dir()) + ".unrecognized-"
        quarantined = [
            p for p in os.listdir(self.tmp.name) if p.startswith(prefix)
        ]
        self.assertEqual(len(quarantined), 1)
        with open(
            os.path.join(self.tmp.name, quarantined[0], "some-leftover-file"),
            encoding="utf-8",
        ) as f:
            self.assertEqual(f.read(), "mystery data")
        self.assertTrue(any("doesn't look valid" in m for m in logs))

    def test_self_heal_then_fresh_activation_composes_correctly(self):
        # Gerçek senaryo: önceki güncelleme iki rename ARASINDA kesildi
        # (install eksik, yedek sağlam) - bu çağrı önce kendini
        # iyileştirir, hemen ardından normal akışla yeni bir sürüm
        # devreye alınabilir.
        _make_install_dir(updater._backup_dir(), "restored-old")
        updater._reconcile_install_and_backup(lambda m: None)

        source = os.path.join(self.tmp.name, "staging-new")
        _make_install_dir(source, "brand-new")
        updater._activate_new_install(source)

        self.assertIn("brand-new", _read_version_marker(self.install_dir))
        self.assertFalse(os.path.exists(updater._backup_dir()))


class StagingParentTests(unittest.TestCase):
    def test_uses_install_dir_parent_when_writable(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_dir = os.path.join(tmp, "packwarden")
            with mock.patch("bulkuninstaller.updater.INSTALL_DIR", install_dir):
                self.assertEqual(updater._staging_parent(), tmp)

    def test_falls_back_to_tempdir_when_parent_does_not_exist(self):
        with mock.patch(
            "bulkuninstaller.updater.INSTALL_DIR",
            "/this/does/not/exist/packwarden",
        ):
            self.assertEqual(updater._staging_parent(), tempfile.gettempdir())


class DownloadAndInstallLockTests(_InstallDirFixture):
    def test_concurrent_call_is_rejected_without_touching_install_dir(self):
        _make_install_dir(self.install_dir, "old")
        updater._install_lock.acquire()  # ilk çağrı hâlâ sürüyormuş gibi
        try:
            with mock.patch(
                "bulkuninstaller.updater._fetch_latest_release"
            ) as fetch_mock:
                logs = []
                ok = updater.download_and_install(
                    progress=lambda *a: None,
                    log=logs.append,
                    cancelled=lambda: False,
                )
            self.assertFalse(ok)
            self.assertTrue(any("already in progress" in m for m in logs))
            fetch_mock.assert_not_called()
        finally:
            updater._install_lock.release()
        self.assertIn("old", _read_version_marker(self.install_dir))

    def test_lock_is_released_after_a_failed_attempt_so_a_retry_can_proceed(self):
        _make_install_dir(self.install_dir, "old")
        with mock.patch(
            "bulkuninstaller.updater._fetch_latest_release", return_value=None
        ):
            ok = updater.download_and_install(
                progress=lambda *a: None, log=lambda m: None,
                cancelled=lambda: False,
            )
        self.assertFalse(ok)
        self.assertTrue(updater._install_lock.acquire(blocking=False))
        updater._install_lock.release()


class DownloadAndInstallEndToEndTests(_InstallDirFixture):
    def _mock_release(self, tarball_bytes):
        checksum = hashlib.sha256(tarball_bytes).hexdigest()
        return (
            mock.patch(
                "bulkuninstaller.updater._fetch_latest_release",
                return_value={
                    "tag_name": "v9.9.9",
                    "tarball_url": "https://example.invalid/t.tar.gz",
                },
            ),
            mock.patch(
                "bulkuninstaller.updater._fetch_checksum", return_value=checksum
            ),
            mock.patch(
                "bulkuninstaller.updater.urllib.request.urlopen",
                return_value=_FakeHttpResponse(tarball_bytes),
            ),
        )

    def test_full_successful_update_replaces_install_and_releases_the_lock(self):
        _make_install_dir(self.install_dir, "old")
        tarball = _build_fake_release_tarball("9.9.9")
        patchers = self._mock_release(tarball)
        logs = []
        with patchers[0], patchers[1], patchers[2]:
            ok = updater.download_and_install(
                progress=lambda *a: None, log=logs.append,
                cancelled=lambda: False,
            )

        self.assertTrue(ok)
        self.assertIn("9.9.9", _read_version_marker(self.install_dir))
        self.assertFalse(os.path.exists(updater._backup_dir()))
        self.assertTrue(updater._install_lock.acquire(blocking=False))
        updater._install_lock.release()

    def test_checksum_mismatch_aborts_without_touching_install_dir(self):
        _make_install_dir(self.install_dir, "old")
        tarball = _build_fake_release_tarball("9.9.9")
        logs = []
        with mock.patch(
            "bulkuninstaller.updater._fetch_latest_release",
            return_value={
                "tag_name": "v9.9.9",
                "tarball_url": "https://example.invalid/t.tar.gz",
            },
        ), mock.patch(
            "bulkuninstaller.updater._fetch_checksum", return_value="0" * 64
        ), mock.patch(
            "bulkuninstaller.updater.urllib.request.urlopen",
            return_value=_FakeHttpResponse(tarball),
        ):
            ok = updater.download_and_install(
                progress=lambda *a: None, log=logs.append,
                cancelled=lambda: False,
            )

        self.assertFalse(ok)
        self.assertTrue(any("checksum mismatch" in m for m in logs))
        self.assertIn("old", _read_version_marker(self.install_dir))
        self.assertFalse(os.path.exists(updater._backup_dir()))
        self.assertTrue(updater._install_lock.acquire(blocking=False))
        updater._install_lock.release()

    def test_empty_archive_aborts_without_touching_install_dir(self):
        _make_install_dir(self.install_dir, "old")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz"):
            pass  # bos arsiv
        tarball = buf.getvalue()
        logs = []
        with mock.patch(
            "bulkuninstaller.updater._fetch_latest_release",
            return_value={
                "tag_name": "v9.9.9",
                "tarball_url": "https://example.invalid/t.tar.gz",
            },
        ), mock.patch(
            "bulkuninstaller.updater._fetch_checksum",
            return_value=hashlib.sha256(tarball).hexdigest(),
        ), mock.patch(
            "bulkuninstaller.updater.urllib.request.urlopen",
            return_value=_FakeHttpResponse(tarball),
        ):
            ok = updater.download_and_install(
                progress=lambda *a: None, log=logs.append,
                cancelled=lambda: False,
            )

        self.assertFalse(ok)
        self.assertTrue(any("archive is empty" in m for m in logs))
        self.assertIn("old", _read_version_marker(self.install_dir))
        self.assertTrue(updater._install_lock.acquire(blocking=False))
        updater._install_lock.release()

    def test_activation_failure_during_full_flow_restores_old_version_and_reports_error(self):
        # _activate_new_install() taklit EDİLMİYOR - gerçek fonksiyon
        # gerçek geçici dizinlerle çalışır, sadece son `os.rename`/
        # `shutil.move` adımı başarısız olacak şekilde zorlanıyor. Bu,
        # uçtan uca akışın GERÇEKTEN eski sürümü geri getirdiğini
        # doğrular (sadece try/except'in doğru dalına girdiğini değil).
        _make_install_dir(self.install_dir, "old")
        tarball = _build_fake_release_tarball("9.9.9")
        real_rename = os.rename

        def flaky_rename(src, dst):
            # Sadece YENİ sürümü devreye alma denemesi başarısız olsun -
            # geri dönüşün KENDİ `os.rename(backup, INSTALL_DIR)` çağrısı
            # (src == backup) buradan ETKİLENMEMELİ, yoksa kurtarmanın
            # kendisi de başarısız olur ve bu test hiçbir şey kanıtlamaz.
            if dst == self.install_dir and src != updater._backup_dir():
                raise OSError(28, "No space left on device")
            return real_rename(src, dst)

        logs = []
        with mock.patch(
            "bulkuninstaller.updater._fetch_latest_release",
            return_value={
                "tag_name": "v9.9.9",
                "tarball_url": "https://example.invalid/t.tar.gz",
            },
        ), mock.patch(
            "bulkuninstaller.updater._fetch_checksum",
            return_value=hashlib.sha256(tarball).hexdigest(),
        ), mock.patch(
            "bulkuninstaller.updater.urllib.request.urlopen",
            return_value=_FakeHttpResponse(tarball),
        ), mock.patch(
            "bulkuninstaller.updater.os.rename", side_effect=flaky_rename
        ), mock.patch(
            "bulkuninstaller.updater.shutil.move",
            side_effect=OSError("disk full"),
        ):
            ok = updater.download_and_install(
                progress=lambda *a: None, log=logs.append,
                cancelled=lambda: False,
            )

        self.assertFalse(ok)
        self.assertTrue(any("previous version was restored" in m for m in logs))
        self.assertTrue(os.path.isdir(self.install_dir))
        self.assertIn("old", _read_version_marker(self.install_dir))
        self.assertFalse(os.path.exists(updater._backup_dir()))
        self.assertTrue(updater._install_lock.acquire(blocking=False))
        updater._install_lock.release()


if __name__ == "__main__":
    unittest.main()
