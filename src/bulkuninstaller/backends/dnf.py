from .. import host
from .base import Backend, Package


class DnfBackend(Backend):
    """Fedora, RHEL, AlmaLinux, Rocky, ..."""

    id = "dnf"
    display_name = "DNF"
    needs_root = True

    def is_available(self) -> bool:
        return host.command_exists("dnf") and host.command_exists("rpm")

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

        # dnf, kullanıcının elle kurduğu paketleri ayrıca izliyor (dnf
        # history) — rpm veritabanının kendisinde böyle bir alan yok.
        # Tek toplu komutla alınır, paket başına ayrı çağrı gerekmez.
        explicit = self._explicit_set()

        rows = [line.split("\t") for line in proc.stdout.splitlines()]
        rows = [parts for parts in rows if len(parts) >= 2 and parts[0]]

        # Multilib: aynı paket adı birden fazla mimaride ayrı ayrı kurulu
        # olabilir (ör. glibc.x86_64 + glibc.i686 - 32-bit uyumluluk/
        # Steam/Wine kurulumlarında yaygın, apt.py'deki multiarch ile
        # aynı sorun). "rpm -qa" bunları İKİ AYRI satır olarak basar,
        # ikisi de %{NAME}'de aynı çıplak adı verir - mimari eklenmezse
        # arayüzde ayırt edilemezler VE kaldırma komutu hangi mimariyi
        # hedefleyeceğini bilemez. RPM/dnf'nin kendi söz dizimi apt'in
        # "ad:mimari"sinden farklı - NOKTA kullanır ("ad.mimari", ör.
        # "glibc.i686"). Yalnızca GERÇEKTEN çakışan adlarda ve mimari
        # alanı gerçekten doluysa bu biçime geçiliyor - sıradan
        # tek-mimarili paketlerin kimliği hiç değişmiyor.
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
                # üretmek yerine eski (çıplak ad) davranışına düşülür -
                # en kötü ihtimalle önceden olduğu kadar belirsiz kalır.
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
            install_reason = ""
            if explicit is not None:
                install_reason = "explicit" if name in explicit else "dependency"
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
                install_reason=install_reason,
            ))
        return packages

    @staticmethod
    def _explicit_set() -> set[str] | None:
        proc = host.run(["dnf", "repoquery", "--userinstalled", "--qf", "%{NAME}"],
                         timeout=120)
        if proc.returncode != 0:
            return None
        return set(proc.stdout.split())

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["dnf", "remove", "-y"] + ids
