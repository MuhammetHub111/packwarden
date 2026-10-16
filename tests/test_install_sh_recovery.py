"""install.sh: `$BIN/packwarden` başlatıcısının, PackWarden'ın kendi
Python kodu HENÜZ hiç çalışmadan önce yaptığı kurtarma mantığı.

Önceki tur `_reconcile_install_and_backup()`'un (updater.py, Python)
INSTALL_DIR eksikken ASLA çalışamayacağını kesin olarak doğruladı -
çünkü o kod da INSTALL_DIR'ın İÇİNDE. Bu yüzden kurtarma, INSTALL_DIR'ın
DIŞINDA yaşayan tek şeye (saf POSIX shell wrapper'ına) taşındı.

Bu testler install.sh'ın GERÇEK, o anki heredoc içeriğini (dosyadan
doğrudan ayrıştırılarak) gerçek bir `sh` ile genişletip üretilen
wrapper'ı GERÇEKTEN çalıştırır - sahte/kopyalanmış bir kod parçası
değil. Gerçek `python3` çağrılmaz (bir "sahte python3" PATH'e
eklenerek gölgelenir) - bu yüzden gerçek uygulama hiç başlamaz, gerçek
kullanıcı verisine hiç dokunulmaz. Tüm dizinler geçicidir
(tempfile.TemporaryDirectory), testten sonra silinir.

Çalıştırma (GTK gerekmez, bare sandbox'ta çalışır):
    PYTHONPATH=src python3 -m unittest tests.test_install_sh_recovery -v
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "install.sh"


def _extract_wrapper_heredoc() -> str:
    """install.sh'taki `$BIN/packwarden` heredoc'unun GERÇEK, o anki
    gövdesini (genişletilmemiş hâliyle) doğrudan dosyadan ayrıştırır -
    testin kendisi, dosyada ne varsa onu test eder."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    marker = 'cat > "$BIN/packwarden" << LAUNCHER\n'
    start = text.index(marker) + len(marker)
    end = text.index("\nLAUNCHER\n", start)
    return text[start:end]


def _generate_wrapper(dir_value: str, bin_dir: str) -> str:
    """install.sh'ın KENDİSİNİN yapacağı gibi, gerçek bir `sh` ile
    heredoc'u DIR=<dir_value> ortamında genişletip wrapper dosyasını
    üretir - install.sh'ın kendisi hiç çalıştırılmaz (indirme/kurulum
    adımları atlanır), SADECE bu bir heredoc gövdesi."""
    os.makedirs(bin_dir, exist_ok=True)
    body = _extract_wrapper_heredoc()
    driver = (
        f'DIR="{dir_value}"\n'
        f'BIN="{bin_dir}"\n'
        'cat > "$BIN/packwarden" << LAUNCHER\n'
        + body + "\nLAUNCHER\n"
    )
    result = subprocess.run(
        ["sh", "-c", driver], capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(f"heredoc üretimi başarısız: {result.stderr}")
    wrapper_path = os.path.join(bin_dir, "packwarden")
    os.chmod(wrapper_path, 0o755)
    return wrapper_path


def _make_install_dir(path: str, marker: str) -> None:
    os.makedirs(os.path.join(path, "src", "bulkuninstaller"), exist_ok=True)
    with open(os.path.join(path, "install.sh"), "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\n")
    with open(
        os.path.join(path, "src", "bulkuninstaller", "__init__.py"),
        "w", encoding="utf-8",
    ) as f:
        f.write(f'VERSION = "{marker}"\n')


def _read_version_marker(path: str) -> str:
    with open(
        os.path.join(path, "src", "bulkuninstaller", "__init__.py"),
        encoding="utf-8",
    ) as f:
        return f.read()


def _fake_python_dir(tmp: str):
    """Gerçek python3'ü PATH'te GÖLGELEYEN sahte bir ikili - gerçek
    uygulamayı hiç başlatmaz, sadece KENDİSİNİN çağrıldığını ve hangi
    PYTHONPATH/argümanlarla çağrıldığını bir işaret dosyasına yazar.
    Bu, wrapper'ın "exec" satırına GERÇEKTEN ulaştığını ve doğru,
    kurtarılmış yolu kullandığını kanıtlamanın yolu."""
    fake_bin = os.path.join(tmp, "fakepath")
    os.makedirs(fake_bin, exist_ok=True)
    marker_file = os.path.join(tmp, "python_invoked.txt")
    script_path = os.path.join(fake_bin, "python3")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(
            "#!/bin/sh\n"
            f'printf "PYTHONPATH=%s ARGS=%s\\n" "$PYTHONPATH" "$*" '
            f'> "{marker_file}"\n'
            "exit 0\n"
        )
    os.chmod(script_path, 0o755)
    return fake_bin, marker_file


def _run_wrapper(wrapper_path: str, fake_python_dir: str, extra_env=None):
    env = dict(os.environ)
    env["PATH"] = fake_python_dir + os.pathsep + env.get("PATH", "")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [wrapper_path], capture_output=True, text=True, env=env, timeout=10,
    )


class HealthyInstallIsNeverTouchedTests(unittest.TestCase):
    def test_healthy_install_reaches_python_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = os.path.join(tmp, "packwarden")
            _make_install_dir(dir_path, "current")
            wrapper = _generate_wrapper(dir_path, os.path.join(tmp, "bin"))
            fake_python, marker_file = _fake_python_dir(tmp)

            result = _run_wrapper(wrapper, fake_python)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr.strip(), "")
            with open(marker_file, encoding="utf-8") as f:
                self.assertIn(f"PYTHONPATH={dir_path}/src", f.read())
            self.assertIn("current", _read_version_marker(dir_path))
            self.assertFalse(os.path.exists(dir_path + ".update-backup"))

    def test_healthy_install_leaves_no_quarantine_directories_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = os.path.join(tmp, "packwarden")
            _make_install_dir(dir_path, "current")
            wrapper = _generate_wrapper(dir_path, os.path.join(tmp, "bin"))
            fake_python, _marker = _fake_python_dir(tmp)
            _run_wrapper(wrapper, fake_python)
            leftovers = [p for p in os.listdir(tmp) if "broken" in p]
            self.assertEqual(leftovers, [])


class InterruptedUpdateRecoveryTests(unittest.TestCase):
    """İki `os.rename` ARASINDA kesilmiş bir güncellemeyi simüle eder:
    DIR eksik/bozuk, DIR.update-backup sağlam."""

    def test_missing_install_dir_is_restored_from_backup_before_python_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = os.path.join(tmp, "packwarden")
            backup_path = dir_path + ".update-backup"
            _make_install_dir(backup_path, "restored-from-backup")
            # dir_path hiç yok - gerçek kesinti senaryosu.
            wrapper = _generate_wrapper(dir_path, os.path.join(tmp, "bin"))
            fake_python, marker_file = _fake_python_dir(tmp)

            result = _run_wrapper(wrapper, fake_python)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("restored", result.stderr.lower())
            self.assertIn("restored-from-backup", _read_version_marker(dir_path))
            self.assertFalse(os.path.exists(backup_path))
            with open(marker_file, encoding="utf-8") as f:
                self.assertIn(f"PYTHONPATH={dir_path}/src", f.read())

    def test_broken_target_is_quarantined_not_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = os.path.join(tmp, "packwarden")
            os.makedirs(os.path.join(dir_path, "src"))  # install.sh YOK -> gecersiz
            with open(
                os.path.join(dir_path, "some-leftover-file"), "w", encoding="utf-8"
            ) as f:
                f.write("forensically interesting data")
            backup_path = dir_path + ".update-backup"
            _make_install_dir(backup_path, "restored")

            wrapper = _generate_wrapper(dir_path, os.path.join(tmp, "bin"))
            fake_python, _marker = _fake_python_dir(tmp)

            result = _run_wrapper(wrapper, fake_python)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("restored", _read_version_marker(dir_path))
            quarantines = [
                p for p in os.listdir(tmp) if p.startswith("packwarden.broken-")
            ]
            self.assertEqual(len(quarantines), 1)
            with open(
                os.path.join(tmp, quarantines[0], "some-leftover-file"),
                encoding="utf-8",
            ) as f:
                self.assertEqual(f.read(), "forensically interesting data")

    def test_dangling_symlink_target_is_replaced_by_the_restored_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = os.path.join(tmp, "packwarden")
            os.symlink(os.path.join(tmp, "nowhere-at-all"), dir_path)
            self.assertTrue(os.path.islink(dir_path))
            backup_path = dir_path + ".update-backup"
            _make_install_dir(backup_path, "restored-over-symlink")

            wrapper = _generate_wrapper(dir_path, os.path.join(tmp, "bin"))
            fake_python, marker_file = _fake_python_dir(tmp)

            result = _run_wrapper(wrapper, fake_python)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(os.path.islink(dir_path))
            self.assertIn(
                "restored-over-symlink", _read_version_marker(dir_path)
            )
            with open(marker_file, encoding="utf-8") as f:
                self.assertIn(f"PYTHONPATH={dir_path}/src", f.read())


class NoUsableBackupFailsCleanlyTests(unittest.TestCase):
    def test_missing_install_and_no_backup_touches_nothing_and_never_calls_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = os.path.join(tmp, "packwarden")
            wrapper = _generate_wrapper(dir_path, os.path.join(tmp, "bin"))
            fake_python, marker_file = _fake_python_dir(tmp)

            result = _run_wrapper(wrapper, fake_python)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing or broken", result.stderr)
            self.assertFalse(os.path.exists(dir_path))
            self.assertFalse(os.path.exists(marker_file))

    def test_broken_target_and_broken_backup_leaves_both_exactly_as_they_were(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = os.path.join(tmp, "packwarden")
            os.makedirs(dir_path)
            with open(
                os.path.join(dir_path, "marker-original"), "w", encoding="utf-8"
            ) as f:
                f.write("original-broken-target")
            backup_path = dir_path + ".update-backup"
            os.makedirs(backup_path)
            with open(
                os.path.join(backup_path, "marker-backup"), "w", encoding="utf-8"
            ) as f:
                f.write("backup-also-broken")

            wrapper = _generate_wrapper(dir_path, os.path.join(tmp, "bin"))
            fake_python, marker_file = _fake_python_dir(tmp)

            result = _run_wrapper(wrapper, fake_python)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing or broken", result.stderr)
            self.assertFalse(os.path.exists(marker_file))
            self.assertTrue(
                os.path.isfile(os.path.join(dir_path, "marker-original"))
            )
            self.assertTrue(
                os.path.isfile(os.path.join(backup_path, "marker-backup"))
            )


class RestoreFailureLeavesASafeRollbackPathTests(unittest.TestCase):
    """`mv` ikinci (geri yükleme) çağrısında KASITLI olarak başarısız
    olacak şekilde PATH'e sahte bir `mv` yerleştirilir - ilk çağrı
    (karantinaya alma) GERÇEK mv ile başarılı olur, ikincisi başarısız
    olur. Bu, "kurtarma başarısız olursa geri alma imkânını koru"
    gereksinimini tam olarak izole eder."""

    def _fake_mv_dir(self, tmp: str, fail_on_call: int):
        fake_bin = os.path.join(tmp, "fakemv")
        os.makedirs(fake_bin, exist_ok=True)
        counter_file = os.path.join(tmp, "mv_call_count")
        script_path = os.path.join(fake_bin, "mv")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(
                "#!/bin/sh\n"
                f'count=$(cat "{counter_file}" 2>/dev/null || echo 0)\n'
                "count=$((count + 1))\n"
                f'echo "$count" > "{counter_file}"\n'
                f'if [ "$count" -eq {fail_on_call} ]; then\n'
                "    exit 1\n"
                "fi\n"
                'exec /usr/bin/mv "$@"\n'
            )
        os.chmod(script_path, 0o755)
        return fake_bin, counter_file

    def test_restore_mv_failure_restores_the_quarantined_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = os.path.join(tmp, "packwarden")
            os.makedirs(os.path.join(dir_path, "src"))  # gecersiz (install.sh yok)
            with open(
                os.path.join(dir_path, "original-marker"), "w", encoding="utf-8"
            ) as f:
                f.write("this must survive")
            backup_path = dir_path + ".update-backup"
            _make_install_dir(backup_path, "would-have-been-restored")

            wrapper = _generate_wrapper(dir_path, os.path.join(tmp, "bin"))
            fake_python, marker_file = _fake_python_dir(tmp)
            # 1. mv cagrisi = karantina (basarili), 2. mv cagrisi =
            # yedegi geri yukleme (KASITLI basarisiz).
            fake_mv, _counter = self._fake_mv_dir(tmp, fail_on_call=2)

            env = dict(os.environ)
            env["PATH"] = (
                fake_python + os.pathsep + fake_mv + os.pathsep
                + env.get("PATH", "")
            )
            result = subprocess.run(
                [wrapper], capture_output=True, text=True, env=env, timeout=10,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("could not restore", result.stderr)
            self.assertFalse(os.path.exists(marker_file))  # python3 hic cagrilmadi
            # Karantinaya alinan ORIJINAL geri getirildi - kaybolmadi.
            self.assertTrue(os.path.isdir(dir_path))
            with open(
                os.path.join(dir_path, "original-marker"), encoding="utf-8"
            ) as f:
                self.assertEqual(f.read(), "this must survive")
            # Yedek de hala yerinde - o da kaybolmadi.
            self.assertIn(
                "would-have-been-restored", _read_version_marker(backup_path)
            )
            quarantines = [
                p for p in os.listdir(tmp) if p.startswith("packwarden.broken-")
            ]
            self.assertEqual(quarantines, [])  # geri alindigi icin artik yok


if __name__ == "__main__":
    unittest.main()
