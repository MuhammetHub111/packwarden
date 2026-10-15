from .. import host
from .base import Backend, Package


class ZypperBackend(Backend):
    """openSUSE, SLE."""

    id = "zypper"
    display_name = "Zypper"
    needs_root = True

    def is_available(self) -> bool:
        # dnf de rpm tabanlıdır; ikisi birden kuruluysa listeyi DNF verir,
        # aynı paketlerin iki kez görünmesini önlemek için burada çekiliriz.
        return (
            host.command_exists("zypper")
            and host.command_exists("rpm")
            and not host.command_exists("dnf")
        )

    def list_packages(self) -> list[Package]:
        proc = host.run(
            [
                "rpm", "-qa", "--qf",
                "%{NAME}\\t%{ARCH}\\t%{VERSION}-%{RELEASE}\\t%{SIZE}\\t"
                "%{SUMMARY}\\t%{VENDOR}\\t%{INSTALLTIME}\\t%{LICENSE}\\n",
            ],
            timeout=300,
        )
        if proc.returncode != 0:
            return []

        rows = [line.split("\t") for line in proc.stdout.splitlines()]
        rows = [parts for parts in rows if len(parts) >= 2 and parts[0]]

        # Multilib: aynı paket adı birden fazla mimaride ayrı ayrı kurulu
        # olabilir (ör. glibc.x86_64 + glibc.i686 - bkz. backends/dnf.py,
        # aynı sorun, openSUSE de RPM tabanlı olduğu için). Yalnızca
        # GERÇEKTEN çakışan adlarda ve mimari alanı gerçekten doluysa
        # "ad.mimari" biçimine geçiliyor - zypper'ın kendi "capability"
        # söz dizimi de buna uyuyor (man zypper: "NAME[.ARCH][ OP
        # EDITION]", remove komutu install ile aynı seçim söz dizimini
        # kullanıyor). Sıradan tek-mimarili paketlerin kimliği değişmez.
        name_counts: dict[str, int] = {}
        for parts in rows:
            name_counts[parts[0]] = name_counts.get(parts[0], 0) + 1

        packages = []
        for parts in rows:
            name, arch = parts[0], parts[1] if len(parts) > 1 else ""
            if name_counts[name] > 1 and arch:
                display_id = f"{name}.{arch}"
            else:
                # Mimari alanı boş/bozuksa uydurma bir "ad." kimliği
                # üretmek yerine eski (çıplak ad) davranışına düşülür.
                display_id = name
            try:
                size = int(parts[3])
            except (IndexError, ValueError):
                size = 0
            vendor = parts[5] if len(parts) > 5 else ""
            if vendor == "(none)":
                vendor = ""
            try:
                install_date = float(parts[6]) if len(parts) > 6 else None
            except ValueError:
                install_date = None
            license_ = parts[7] if len(parts) > 7 else ""
            if license_ == "(none)":
                license_ = ""
            packages.append(Package(
                id=display_id,
                name=display_id,
                version=parts[2] if len(parts) > 2 else "",
                size=size,
                description=parts[4] if len(parts) > 4 else "",
                source=self.id,
                publisher=vendor,
                install_date=install_date,
                license=license_,
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["zypper", "--non-interactive", "remove", "--clean-deps"] + ids
