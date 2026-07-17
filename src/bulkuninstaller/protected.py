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


def is_protected(pkg: Package) -> bool:
    """Bu paket silinirse sistem zarar görür mü?"""
    if pkg.source not in SYSTEM_SOURCES:
        return False
    name = pkg.name.lower()
    return name in PROTECTED_EXACT or name.startswith(PROTECTED_PREFIXES)
