"""install.sh: `remove` dalının ~/.config/bulkuninstaller/ (settings.json,
usage.json) temizliği.

Onaylanan A seçeneği: önce `gio trash` ile çöp kutusuna taşımayı dene;
"gio" yoksa veya taşıma başarısız olursa dizine HİÇ dokunma, yalnızca
nerede olduğunu kullanıcıya bildiren bir satır yazdır. Asla kalıcı
(`rm -rf`) silme yok.

Bu testler install.sh'ın GERÇEK, o anki `remove` blok içeriğini
(dosyadan doğrudan ayrıştırılarak) gerçek bir `sh` ile çalıştırır -
sahte/kopyalanmış bir kod parçası değil. `systemctl`, `kbuildsycoca6`,
`update-desktop-database` ve `gio` PATH'te sahte ikililerle gölgelenir
- bu yüzden gerçek systemd servisi hiç etkinleştirilmez/durdurulmaz,
gerçek masaüstü veritabanı hiç güncellenmez. Tüm dizinler geçicidir
(tempfile.TemporaryDirectory), testten sonra silinir - gerçek
~/.config/bulkuninstaller/'a asla dokunulmaz.

Çalıştırma (GTK gerekmez, bare sandbox'ta çalışır):
    PYTHONPATH=src python3 -m unittest tests.test_install_sh_remove_cleanup -v
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


def _extract_remove_block() -> str:
    """install.sh'taki `remove` dalının GERÇEK, o anki gövdesini
    doğrudan dosyadan ayrıştırır - testin kendisi, dosyada ne varsa
    onu test eder. İçindeki iç içe `gio trash` if/fi'si dahil, en
    dıştaki `fi`'ye kadar her şeyi alır."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index('if [ "$1" = "remove"')
    marker_end = text.index('echo "Installing PackWarden...')
    block = text[start:marker_end].rstrip("\n")
    assert block.endswith("fi"), block[-80:]
    return block


def _fake_bin_dir(tmp: str, extra_scripts: dict) -> str:
    """Her biri çağrıldığını bir işaret dosyasına yazan sahte ikililer
    içeren bir dizin üretir - gerçek systemctl/gio/vb. hiç çağrılmaz."""
    fake_bin = os.path.join(tmp, "fakebin")
    os.makedirs(fake_bin, exist_ok=True)
    for name, body in extra_scripts.items():
        path = os.path.join(fake_bin, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(path, 0o755)
    return fake_bin


def _record_script(marker_file: str, exit_code: int = 0) -> str:
    return (
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{marker_file}"\n'
        f"exit {exit_code}\n"
    )


def _gio_trash_success_script(marker_file: str) -> str:
    """Gerçek `gio trash`'in davranışını taklit eder: çağrıldığını
    kaydeder VE hedefi (son argüman) gerçekten kaldırır - böylece test,
    "install.sh dizinin artık yerinde olmadığını" gerçekten doğrulayabilir."""
    return (
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{marker_file}"\n'
        'rm -rf "$2"\n'
        "exit 0\n"
    )


def _run_remove_block(
    tmp: str, config_dir_exists: bool, gio_script=None, isolate_path=False,
):
    """`remove` bloğunu gerçek bir `sh` ile çalıştırır. DIR/BIN/APPS/
    SYSTEMD_USER/CONFIG_DIR hepsi geçici dizinlere ayarlanır - gerçek
    kullanıcı sistemine hiç dokunulmaz.

    isolate_path=True: PATH'i SADECE sahte ikililer + gerçek `rm`'in
    bir sembolik bağlantısıyla sınırlar (gerçek sistem PATH'i hiç
    eklenmez) - bu, host'ta gerçekten `gio` kurulu olsa bile "gio
    kurulu değil" senaryosunu kesin olarak garanti eder."""
    dir_path = os.path.join(tmp, "packwarden")
    bin_dir = os.path.join(tmp, "bin")
    apps_dir = os.path.join(tmp, "apps")
    systemd_user = os.path.join(tmp, "systemd-user")
    config_dir = os.path.join(tmp, "config", "bulkuninstaller")
    os.makedirs(dir_path, exist_ok=True)
    os.makedirs(bin_dir, exist_ok=True)
    os.makedirs(apps_dir, exist_ok=True)
    os.makedirs(systemd_user, exist_ok=True)
    if config_dir_exists:
        os.makedirs(config_dir, exist_ok=True)
        with open(
            os.path.join(config_dir, "settings.json"), "w", encoding="utf-8"
        ) as f:
            f.write('{"marker": "real user data"}')

    systemctl_marker = os.path.join(tmp, "systemctl_calls.txt")
    scripts = {
        "systemctl": _record_script(systemctl_marker),
        "kbuildsycoca6": _record_script(os.path.join(tmp, "kbuild_calls.txt")),
        "update-desktop-database": _record_script(
            os.path.join(tmp, "update_db_calls.txt")
        ),
    }
    if gio_script is not None:
        scripts["gio"] = gio_script
    fake_bin = _fake_bin_dir(tmp, scripts)

    block = _extract_remove_block()
    driver = (
        f'DIR="{dir_path}"\n'
        f'BIN="{bin_dir}"\n'
        f'APPS="{apps_dir}"\n'
        f'SYSTEMD_USER="{systemd_user}"\n'
        'USAGE_SERVICE="packwarden-usage-daemon.service"\n'
        f'CONFIG_DIR="{config_dir}"\n'
        "desktop_dir() { echo \"$HOME/Desktop-does-not-exist\"; }\n"
        + block + "\n"
    )
    env = dict(os.environ)
    if isolate_path:
        import shutil as _shutil
        for _tool in ("rm", "sh"):
            os.symlink(_shutil.which(_tool), os.path.join(fake_bin, _tool))
        env["PATH"] = fake_bin
    else:
        env["PATH"] = fake_bin + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        ["sh", "-c", driver, "packwarden", "remove"],
        capture_output=True, text=True, env=env, timeout=10,
    )
    return result, config_dir, systemctl_marker


class ConfigDirTrashedWhenGioSucceedsTests(unittest.TestCase):
    def test_config_dir_is_trashed_via_gio_and_removed_from_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            gio_marker = os.path.join(tmp, "gio_calls.txt")
            gio_script = _gio_trash_success_script(gio_marker)
            result, config_dir, _systemctl = _run_remove_block(
                tmp, config_dir_exists=True, gio_script=gio_script,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(os.path.exists(config_dir))
            with open(gio_marker, encoding="utf-8") as f:
                self.assertIn(f"trash {config_dir}", f.read())
            # Kullanıcıya "hala orada" notu basılmamalı - başarıyla taşındı.
            self.assertNotIn("Note:", result.stdout)

    def test_missing_config_dir_is_a_silent_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            gio_marker = os.path.join(tmp, "gio_calls.txt")
            gio_script = _record_script(gio_marker, exit_code=0)
            result, config_dir, _systemctl = _run_remove_block(
                tmp, config_dir_exists=False, gio_script=gio_script,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(os.path.exists(gio_marker))
            self.assertNotIn("Note:", result.stdout)


class ConfigDirLeftUntouchedWhenTrashFailsTests(unittest.TestCase):
    def test_config_dir_survives_and_a_note_is_printed_when_gio_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            gio_marker = os.path.join(tmp, "gio_calls.txt")
            gio_script = _record_script(gio_marker, exit_code=1)
            result, config_dir, _systemctl = _run_remove_block(
                tmp, config_dir_exists=True, gio_script=gio_script,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            # Kalıcı silme YOK - veri hala tam olarak yerinde.
            self.assertTrue(os.path.isdir(config_dir))
            with open(
                os.path.join(config_dir, "settings.json"), encoding="utf-8"
            ) as f:
                self.assertEqual(f.read(), '{"marker": "real user data"}')
            self.assertIn(config_dir, result.stdout)

    def test_config_dir_survives_and_a_note_is_printed_when_gio_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, config_dir, _systemctl = _run_remove_block(
                tmp, config_dir_exists=True, gio_script=None,
                isolate_path=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.path.isdir(config_dir))
            with open(
                os.path.join(config_dir, "settings.json"), encoding="utf-8"
            ) as f:
                self.assertEqual(f.read(), '{"marker": "real user data"}')
            self.assertIn(config_dir, result.stdout)


class OtherRemoveStepsStillRunTests(unittest.TestCase):
    def test_systemd_unit_is_still_disabled_and_the_rest_of_removal_still_happens(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _config_dir, systemctl_marker = _run_remove_block(
                tmp, config_dir_exists=True,
                gio_script=_record_script(os.path.join(tmp, "g.txt")),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(systemctl_marker, encoding="utf-8") as f:
                calls = f.read()
            self.assertIn("disable --now packwarden-usage-daemon.service", calls)
            self.assertIn("daemon-reload", calls)
            self.assertIn("PackWarden removed.", result.stdout)


if __name__ == "__main__":
    unittest.main()
