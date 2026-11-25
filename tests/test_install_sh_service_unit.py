"""install.sh: arkaplan kullanım algılama servisinin (.service birimi +
başlatıcı script) kurulum sırasında yazılması.

PackWarden 0.12.2 döneminde (31 Ağustos 2026) bu birim dosyası hiç
yazılmıyordu - bu açık 2026-09-01'de a960a0b ("Fix install.sh missing
the background-usage-detection systemd unit") ile kapatıldı. Bu
testler o düzeltimin GERÇEK, o anki install.sh içeriğinde hâlâ doğru
çalıştığını - birim dosyasının doğru yola, doğru ExecStart/Restart/
WantedBy ile yazıldığını ve PYTHONPATH'in başlatıcı script üzerinden
doğru şekilde aktığını - doğrudan dosyadan ayrıştırıp gerçek bir `sh`
ile çalıştırarak doğrular. Servis asla etkinleştirilmez/başlatılmaz
(bkz. usage_service.py testleri) - burada sadece DOSYALARIN doğru
yazıldığı kontrol edilir, gerçek systemctl hiç çağrılmaz.

Çalıştırma (GTK gerekmez, bare sandbox'ta çalışır):
    PYTHONPATH=src python3 -m unittest tests.test_install_sh_service_unit -v
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


def _extract_service_install_block() -> str:
    """install.sh'taki, başlatıcı script'i VE .service birimini yazan
    GERÇEK, o anki blok - `$BIN/packwarden-usage-daemon` heredoc'unun
    başından, bu adımın `daemon-reload` çağrısına kadar."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index('cat > "$BIN/packwarden-usage-daemon"')
    marker_end = text.index('command -v systemctl', start)
    end = text.index("\n", marker_end)
    return text[start:end]


def _run_service_install(tmp: str):
    dir_path = os.path.join(tmp, "packwarden")
    bin_dir = os.path.join(tmp, "bin")
    systemd_user = os.path.join(tmp, "systemd-user")
    os.makedirs(dir_path, exist_ok=True)
    os.makedirs(bin_dir, exist_ok=True)
    # SYSTEMD_USER kasıtlı olarak ÖNCEDEN oluşturulmuyor - install.sh'ın
    # kendisi `mkdir -p` ile yarattığını doğrulamak için.

    systemctl_marker = os.path.join(tmp, "systemctl_calls.txt")
    fake_bin = os.path.join(tmp, "fakebin")
    os.makedirs(fake_bin, exist_ok=True)
    with open(os.path.join(fake_bin, "systemctl"), "w", encoding="utf-8") as f:
        f.write(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{systemctl_marker}"\n'
            "exit 0\n"
        )
    os.chmod(os.path.join(fake_bin, "systemctl"), 0o755)

    block = _extract_service_install_block()
    driver = (
        f'DIR="{dir_path}"\n'
        f'BIN="{bin_dir}"\n'
        f'SYSTEMD_USER="{systemd_user}"\n'
        'USAGE_SERVICE="packwarden-usage-daemon.service"\n'
        + block + "\n"
    )
    env = dict(os.environ)
    env["PATH"] = fake_bin + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        ["sh", "-c", driver], capture_output=True, text=True, env=env, timeout=10,
    )
    unit_path = os.path.join(systemd_user, "packwarden-usage-daemon.service")
    wrapper_path = os.path.join(bin_dir, "packwarden-usage-daemon")
    return result, dir_path, unit_path, wrapper_path, systemctl_marker


class ServiceUnitIsWrittenCorrectlyTests(unittest.TestCase):
    def test_systemd_user_dir_is_created_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _dir, unit_path, _wrapper, _sc = _run_service_install(tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.path.isdir(os.path.dirname(unit_path)))

    def test_unit_file_is_written_to_the_user_systemd_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _dir, unit_path, _wrapper, _sc = _run_service_install(tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.path.isfile(unit_path))

    def test_unit_file_execstart_points_at_the_wrapper_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _dir, unit_path, wrapper_path, _sc = _run_service_install(tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = Path(unit_path).read_text(encoding="utf-8")
            self.assertIn(f"ExecStart={wrapper_path}", content)

    def test_unit_file_has_restart_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _dir, unit_path, _wrapper, _sc = _run_service_install(tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = Path(unit_path).read_text(encoding="utf-8")
            self.assertIn("Restart=on-failure", content)

    def test_unit_file_is_wanted_by_default_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _dir, unit_path, _wrapper, _sc = _run_service_install(tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = Path(unit_path).read_text(encoding="utf-8")
            self.assertIn("[Install]", content)
            self.assertIn("WantedBy=default.target", content)

    def test_wrapper_script_sets_pythonpath_and_execs_the_daemon_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, dir_path, _unit, wrapper_path, _sc = _run_service_install(tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = Path(wrapper_path).read_text(encoding="utf-8")
            self.assertIn(f'PYTHONPATH="{dir_path}/src"', content)
            self.assertIn("python3 -m bulkuninstaller.usage_daemon", content)

    def test_wrapper_script_is_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _dir, _unit, wrapper_path, _sc = _run_service_install(tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.access(wrapper_path, os.X_OK))

    def test_service_is_never_enabled_or_started_by_install(self):
        # install.sh bu adımda SADECE daemon-reload yapar - enable/start
        # hiç çağrılmaz (bkz. usage_service.py: sadece Ayarlar'daki
        # anahtar üzerinden, kullanıcı bilerek açarsa devreye girer).
        with tempfile.TemporaryDirectory() as tmp:
            result, _dir, _unit, _wrapper, systemctl_marker = _run_service_install(tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(systemctl_marker, encoding="utf-8") as f:
                calls = f.read()
            self.assertIn("daemon-reload", calls)
            self.assertNotIn("enable", calls)
            self.assertNotIn("start", calls)


class ReinstallIsIdempotentTests(unittest.TestCase):
    def test_running_the_service_install_block_twice_leaves_identical_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            result1, _dir, unit_path, wrapper_path, _sc = _run_service_install(tmp)
            self.assertEqual(result1.returncode, 0, result1.stderr)
            unit_before = Path(unit_path).read_text(encoding="utf-8")
            wrapper_before = Path(wrapper_path).read_text(encoding="utf-8")

            result2, _dir2, unit_path2, wrapper_path2, _sc2 = _run_service_install(tmp)
            self.assertEqual(result2.returncode, 0, result2.stderr)
            self.assertEqual(unit_path, unit_path2)
            self.assertEqual(
                Path(unit_path2).read_text(encoding="utf-8"), unit_before
            )
            self.assertEqual(
                Path(wrapper_path2).read_text(encoding="utf-8"), wrapper_before
            )


if __name__ == "__main__":
    unittest.main()
