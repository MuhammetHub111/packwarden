"""protected.py: multiarch/multislot etiketli kimliklerde koruma.

Gerçek paket kaldırma/sistem değişikliği YAPILMAZ - yalnızca
`is_protected()` saf bir fonksiyon olarak test edilir.

Çalıştırma:
    PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bulkuninstaller.backends.base import Package  # noqa: E402
from bulkuninstaller.protected import is_protected, _unqualified_name  # noqa: E402


def _pkg(source, name, required_by=None):
    return Package(
        id=name, name=name, version="1.0", size=0, description="",
        source=source, required_by=required_by,
    )


class TaggedCriticalPackagesAreProtectedTests(unittest.TestCase):
    def test_glibc_amd64_is_protected(self):
        self.assertTrue(is_protected(_pkg("apt", "glibc:amd64")))

    def test_glibc_i386_is_protected(self):
        self.assertTrue(is_protected(_pkg("apt", "glibc:i386")))

    def test_mesa_i386_is_protected(self):
        self.assertTrue(is_protected(_pkg("apt", "mesa:i386")))

    def test_glibc_dot_x86_64_is_protected(self):
        # dnf/zypper'ın "ad.mimari" biçimi - "x86_64" bilinen bir RPM
        # mimarisi olduğu için güvenle ayıklanır.
        self.assertTrue(is_protected(_pkg("dnf", "glibc.x86_64")))

    def test_mesa_dot_i686_is_protected(self):
        self.assertTrue(is_protected(_pkg("zypper", "mesa.i686")))

    def test_prefix_matched_package_with_tag_is_still_protected(self):
        # "nvidia" bir ÖNEK kuralı (PROTECTED_PREFIXES) - etiketli
        # hâli de hâlâ eşleşmeli.
        self.assertTrue(is_protected(_pkg("apt", "nvidia-utils:amd64")))


class PortageCategorySlotTests(unittest.TestCase):
    def test_category_name_slot_format_is_handled(self):
        # "kategori/ad:slot" biçimi - hem "/" hem ":" ayıklanmalı.
        pkg = _pkg("portage", "sys-libs/glibc:2.2")
        self.assertTrue(is_protected(pkg))

    def test_unprotected_category_name_slot_is_not_protected(self):
        # Koruma listesinde olmayan sıradan bir paket, yalnızca slot
        # etiketi taşıyor diye korunmuş hâle GELMEMELİ.
        pkg = _pkg("portage", "dev-lang/spidermonkey:78")
        self.assertFalse(is_protected(pkg))


class RealDottedNamesAreNotTruncatedTests(unittest.TestCase):
    def test_python3_11_is_not_mistaken_for_an_arch_tag(self):
        self.assertEqual(_unqualified_name("python3.11"), "python3.11")

    def test_python3_11_package_protection_unaffected(self):
        # "python3.11" korunan listede yok - kısaltılıp "python3" ya
        # da başka bir şeye dönüşüp yanlışlıkla korunmuş OLMAMALI,
        # ya da yanlışlıkla korumasız da KALMAMALI - gerçek davranış
        # (korunmuyor) değişmemeli.
        self.assertFalse(is_protected(_pkg("dnf", "python3.11")))

    def test_dotted_name_resembling_but_not_matching_known_arch(self):
        # "noarch" bilinen bir mimari ama burada denenen ad bu
        # DEĞİL - rastgele bir sürüm/alt-ad biçimi yanlışlıkla
        # ayıklanmamalı.
        self.assertEqual(_unqualified_name("mypackage.v2"), "mypackage.v2")


class UntaggedPackagesUnchangedTests(unittest.TestCase):
    """Gerileme kontrolü: etiketsiz paketler için mevcut davranış
    (bu düzeltmeden önce de olduğu gibi) birebir korunuyor."""

    def test_plain_glibc_still_protected(self):
        self.assertTrue(is_protected(_pkg("pacman", "glibc")))

    def test_plain_unrelated_package_still_unprotected(self):
        self.assertFalse(is_protected(_pkg("pacman", "firefox")))

    def test_prefix_rule_still_works_untagged(self):
        self.assertTrue(is_protected(_pkg("pacman", "linux-lts")))

    def test_non_system_source_still_never_protected(self):
        # Flatpak/Snap/AppImage hâlâ hiç korunmuyor - bu değişmedi.
        self.assertFalse(is_protected(_pkg("flatpak", "glibc:amd64")))

    def test_required_by_threshold_still_works_untagged(self):
        self.assertTrue(is_protected(_pkg("pacman", "cairo", required_by=20)))

    def test_required_by_threshold_still_works_tagged(self):
        # required_by eşiği etiketli adlarda da çalışmaya devam etmeli
        # - _unqualified_name yalnızca tam-ad/önek karşılaştırmasını
        # etkiler, required_by kontrolünü değil.
        self.assertTrue(
            is_protected(_pkg("apt", "libfoo:amd64", required_by=20))
        )

    def test_ordinary_package_with_a_tag_is_not_accidentally_protected(self):
        # Sıradan, korunan listede OLMAYAN bir paket, yalnızca mimari
        # etiketi taşıyor diye korunmuş hâle gelmemeli.
        self.assertFalse(is_protected(_pkg("apt", "htop:amd64")))


if __name__ == "__main__":
    unittest.main()
