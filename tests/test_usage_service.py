"""usage_service.py: arkaplan kullanım algılama servisinin (systemd
--user) aç/kapa mantığı.

Gerçek `systemctl` ve gerçek dosya sistemi (UNIT_PATH) HİÇ kullanılmaz
- `host.run` tamamen taklit edilir, `is_installed()` ise gerçek
~/.config/systemd/user/ yerine her testin kendi `unittest.mock.patch`
ile verdiği geçici bir yol/duruma bakacak şekilde yamanır. Gerçek
kullanıcı sisteminde hiçbir systemd birimi etkinleştirilmez/durdurulmaz.

Çalıştırma (GTK gerekmez, bare sandbox'ta çalışır):
    PYTHONPATH=src python3 -m unittest tests.test_usage_service -v
"""

import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller import usage_service  # noqa: E402


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["systemctl"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class IsInstalledTests(unittest.TestCase):
    def test_true_when_the_unit_file_exists(self):
        with mock.patch(
            "bulkuninstaller.usage_service.os.path.isfile", return_value=True,
        ):
            self.assertTrue(usage_service.is_installed())

    def test_false_when_the_unit_file_is_missing(self):
        with mock.patch(
            "bulkuninstaller.usage_service.os.path.isfile", return_value=False,
        ):
            self.assertFalse(usage_service.is_installed())


class IsEnabledTests(unittest.TestCase):
    def test_true_when_systemctl_reports_enabled(self):
        with mock.patch(
            "bulkuninstaller.usage_service.host.run",
            return_value=_completed(returncode=0),
        ):
            self.assertTrue(usage_service.is_enabled())

    def test_false_when_systemctl_reports_disabled(self):
        with mock.patch(
            "bulkuninstaller.usage_service.host.run",
            return_value=_completed(returncode=1),
        ):
            self.assertFalse(usage_service.is_enabled())

    def test_false_when_systemctl_itself_is_unavailable(self):
        # systemd hiç bulunmayan ortamlarda host.run FileNotFoundError
        # fırlatabilir - hata yükseltilmemeli, sessizce False dönmeli.
        with mock.patch(
            "bulkuninstaller.usage_service.host.run",
            side_effect=FileNotFoundError("systemctl not found"),
        ):
            self.assertFalse(usage_service.is_enabled())

    def test_false_on_timeout(self):
        with mock.patch(
            "bulkuninstaller.usage_service.host.run",
            side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=5),
        ):
            self.assertFalse(usage_service.is_enabled())


class SetEnabledTests(unittest.TestCase):
    def test_refuses_with_a_clear_message_when_not_installed(self):
        with mock.patch(
            "bulkuninstaller.usage_service.is_installed", return_value=False,
        ), mock.patch(
            "bulkuninstaller.usage_service.host.run"
        ) as run:
            ok, error = usage_service.set_enabled(True)
        self.assertFalse(ok)
        self.assertTrue(error)
        # Kurulu olmadığı için gerçek bir systemctl çağrısı hiç yapılmamalı.
        run.assert_not_called()

    def test_enable_succeeds_and_calls_systemctl_enable_now(self):
        with mock.patch(
            "bulkuninstaller.usage_service.is_installed", return_value=True,
        ), mock.patch(
            "bulkuninstaller.usage_service.host.run",
            return_value=_completed(returncode=0),
        ) as run:
            ok, error = usage_service.set_enabled(True)
        self.assertTrue(ok)
        self.assertEqual(error, "")
        run.assert_called_once_with(
            ["systemctl", "--user", "enable", "--now",
             usage_service.SERVICE_NAME],
            timeout=10,
        )

    def test_disable_succeeds_and_calls_systemctl_disable_now(self):
        with mock.patch(
            "bulkuninstaller.usage_service.is_installed", return_value=True,
        ), mock.patch(
            "bulkuninstaller.usage_service.host.run",
            return_value=_completed(returncode=0),
        ) as run:
            ok, error = usage_service.set_enabled(False)
        self.assertTrue(ok)
        run.assert_called_once_with(
            ["systemctl", "--user", "disable", "--now",
             usage_service.SERVICE_NAME],
            timeout=10,
        )

    def test_failure_returns_the_stderr_message(self):
        with mock.patch(
            "bulkuninstaller.usage_service.is_installed", return_value=True,
        ), mock.patch(
            "bulkuninstaller.usage_service.host.run",
            return_value=_completed(returncode=1, stderr="Unit not found.\n"),
        ):
            ok, error = usage_service.set_enabled(True)
        self.assertFalse(ok)
        self.assertEqual(error, "Unit not found.")

    def test_failure_falls_back_to_stdout_when_stderr_is_empty(self):
        with mock.patch(
            "bulkuninstaller.usage_service.is_installed", return_value=True,
        ), mock.patch(
            "bulkuninstaller.usage_service.host.run",
            return_value=_completed(returncode=1, stdout="failed\n", stderr=""),
        ):
            ok, error = usage_service.set_enabled(True)
        self.assertFalse(ok)
        self.assertEqual(error, "failed")

    def test_exception_from_host_run_is_reported_not_raised(self):
        with mock.patch(
            "bulkuninstaller.usage_service.is_installed", return_value=True,
        ), mock.patch(
            "bulkuninstaller.usage_service.host.run",
            side_effect=OSError("boom"),
        ):
            ok, error = usage_service.set_enabled(True)
        self.assertFalse(ok)
        self.assertIn("boom", error)


if __name__ == "__main__":
    unittest.main()
