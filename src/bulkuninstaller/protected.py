"""Kritik sistem paketleri koruması.

Bu paketler silinirse sistem açılmayabilir, ekran gelmeyebilir veya
paket yöneticisi çalışmaz hâle gelebilir. Kaldırma penceresi bunlar
için ek bir uyarı gösterir (Ayarlar → Güvenlik'ten kapatılabilir).
Flatpak/Snap/AppImage kaynakları korunmaz; onlar sandbox'lıdır ve
sistemi bozamaz.
"""

from .backends.base import Package

SYSTEM_SOURCES = {
    "pacman", "apt", "dnf", "zypper", "apk", "xbps", "portage", "nix",
}

PROTECTED_EXACT = {
    # temel sistem ve önyükleme
    "base", "filesystem", "mkinitcpio", "dracut", "efibootmgr",
    "systemd", "openrc", "runit", "init",
    # C kütüphanesi ve çekirdek araçlar
    "glibc", "libc6", "libc-bin", "musl", "bash", "coreutils",
    "util-linux", "busybox", "shadow", "pam",
    # yetki ve iletişim
    "sudo", "doas", "polkit", "dbus", "dbus-broker",
    # paket yöneticilerinin kendileri
    "pacman", "apt", "dpkg", "dnf", "rpm", "zypper", "apk-tools",
    "xbps", "portage", "nix", "flatpak", "snapd",
    # ağ
    "networkmanager", "network-manager", "iwd", "dhcpcd",
    # grafik ve oturum
    "mesa", "xorg-server", "wayland", "sddm", "gdm", "gdm3", "lightdm",
    "plasma-desktop", "plasma-workspace", "gnome-shell", "kwin", "mutter",
    # disk ve dosya sistemi
    "e2fsprogs", "btrfs-progs", "xfsprogs", "dosfstools",
    "lvm2", "cryptsetup", "mdadm",
}

PROTECTED_PREFIXES = (
    "linux",      # çekirdekler: linux, linux-lts, linux-cachyos, linux-image-…
    "kernel",     # Fedora: kernel, kernel-core…
    "grub",       # önyükleyici: grub, grub2-efi…
    "systemd-",   # systemd-libs, systemd-sysvcompat…
    "glibc-",
    "nvidia",     # ekran sürücüsü silinirse görüntü gelmeyebilir
    "mesa-",
    "xf86-",      # Xorg sürücüleri
    "amd-ucode", "intel-ucode",  # işlemci mikrokodu
)

# Sabit listede adı geçmeyen ama onlarca başka paketin ihtiyaç duyduğu
# bir paylaşımlı kütüphane de (ör. cairo, gtk3, icu) aynı derecede
# tehlikeli olabilir — gerçek sistemde denendi: 15+ eşiği sıradan
# kullanıcı uygulamalarını (bunlarda required_by neredeyse hep 0-4)
# etkilemeden alsa-lib/cairo/gnutls/gtk3 gibi temel kütüphaneleri yakalıyor.
REQUIRED_BY_THRESHOLD = 15

# apt/dnf/zypper/portage backend'leri, ÇAKIŞAN paket kimliklerini
# ayırt etmek için "ad:mimari" (apt), "ad.mimari" (dnf/zypper) veya
# "kategori/ad:slot" (portage) biçiminde etiketler (bkz. ilgili
# backend'lerin multiarch/multislot düzeltmeleri). Bu etiketler
# PROTECTED_EXACT/PREFIXES'teki çıplak adlarla hiç eşleşmediğinden,
# etiketli bir "glibc:amd64" artık "glibc" kadar korunaklı GÖRÜNMÜYORDU
# - bu, _unqualified_name() ile düzeltiliyor. Yalnızca bu KARŞILAŞTIRMA
# için kullanılır; pkg.id/pkg.name'in kendisi hiç değişmez.
_KNOWN_RPM_ARCHES = {
    "x86_64", "i686", "i586", "noarch", "aarch64",
    "armv7hl", "armv6hl", "ppc64le", "s390x",
}


def _unqualified_name(name: str) -> str:
    """Bilinen multiarch/multislot eklerini GÜVENLİ biçimde ayıklar.

    - "/" (portage'ın "kategori/ad"ı) desteklenen hiçbir paket
      yöneticisinin GERÇEK adında geçmez - sondaki "/"a kadarki kısım
      güvenle atılabilir.
    - ":" (apt'nin "ad:mimari"sı, portage'ın "ad:slot"u) de aynı
      şekilde hiçbir ekosistemin gerçek adında geçmez.
    - "." (dnf/zypper'ın "ad.mimari"sı) GERÇEKTEN adın bir parçası
      olabilir (ör. "python3.11") - bu yüzden yalnızca son nokta
      sonrası BİLİNEN gerçek bir RPM mimarisiyse ayıklanır; aksi
      hâlde ad hiç değiştirilmeden bırakılır.
    """
    if "/" in name:
        name = name.rpartition("/")[2]
    if ":" in name:
        base = name.rpartition(":")[0]
        if base:
            name = base
    if "." in name:
        base, _, suffix = name.rpartition(".")
        if base and suffix in _KNOWN_RPM_ARCHES:
            name = base
    return name


def is_protected(pkg: Package) -> bool:
    """Bu paket silinirse sistem zarar görür mü?"""
    if pkg.source not in SYSTEM_SOURCES:
        return False
    name = _unqualified_name(pkg.name.lower())
    if name in PROTECTED_EXACT or name.startswith(PROTECTED_PREFIXES):
        return True
    return bool(pkg.required_by and pkg.required_by >= REQUIRED_BY_THRESHOLD)
