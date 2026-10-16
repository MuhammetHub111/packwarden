import os

from .. import host
from ._flathub_metadata import developers_for
from ._github_license import licenses_for
from .base import Backend, Package, parse_human_size

_INSTALL_ROOTS = (
    "/var/lib/flatpak/app",
    os.path.expanduser("~/.local/share/flatpak/app"),
)


class FlatpakBackend(Backend):
    """Flatpak apps — works the same on every distro."""

    id = "flatpak"
    display_name = "Flatpak"
    # flatpak talks to polkit itself when a system install needs it
    needs_root = False

    def is_available(self) -> bool:
        return host.command_exists("flatpak")

    def list_packages(self) -> list[Package]:
        proc = host.run(
            [
                "env", "LC_ALL=C",
                "flatpak", "list", "--app",
                "--columns=application,name,version,size,description,origin,installation",
            ],
            timeout=300,
        )
        if proc.returncode != 0:
            return []

        rows = []
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2 or not parts[0]:
                continue
            rows.append(parts)

        # Aynı uygulama hem sistem hem kullanıcı kurulumunda AYNI ANDA
        # kurulu olabilir (Flatpak'ın resmi, desteklenen bir özelliği) -
        # "installation" sütunu eklenmeden önce ikisi de aynı application
        # id'sini veriyordu, arayüzde ayırt edilemeyen iki özdeş satır
        # oluşuyordu. Yalnızca GERÇEKTEN çakışan id'lerde (name_counts>1)
        # ve kurulum bilgisi gerçekten doluysa "app_id:installation"
        # biçimine geçiliyor - tek kurulumlu sıradan uygulamaların id'si
        # hiç değişmiyor. Flatpak uygulama kimlikleri yalnızca
        # [A-Z][a-z][0-9]_ ve son bileşende "-" içerebilir (resmi Flathub
        # kuralı) - ":" HİÇBİR ZAMAN geçerli bir id karakteri değil, bu
        # yüzden ayraç olarak güvenle kullanılabilir.
        app_id_counts: dict[str, int] = {}
        for parts in rows:
            app_id_counts[parts[0]] = app_id_counts.get(parts[0], 0) + 1

        # Flathub'ın kendi API'si gerçek geliştirici adını veriyor —
        # yalnızca oradan gelen uygulamalar için sorgulanır, üçüncü
        # parti depolardakiler ters-DNS tahminine düşer (aşağıda).
        flathub_ids = [
            parts[0] for parts in rows
            if len(parts) > 5 and parts[5].lower() == "flathub"
        ]
        developers = developers_for(flathub_ids)

        packages = []
        for parts in rows:
            app_id = parts[0]
            installation = parts[6] if len(parts) > 6 else ""
            display_id = app_id
            if app_id_counts[app_id] > 1 and installation:
                display_id = f"{app_id}:{installation}"
            publisher = developers.get(app_id) or self._publisher(app_id)
            packages.append(Package(
                id=display_id,
                name=parts[1] or display_id,
                version=parts[2] if len(parts) > 2 else "",
                size=parse_human_size(parts[3]) if len(parts) > 3 else 0,
                description=parts[4] if len(parts) > 4 else "",
                source=self.id,
                publisher=publisher,
                origin=parts[5] if len(parts) > 5 else "",
                install_date=self._install_date(app_id),
                install_path=self._install_path(app_id),
                license=self._license(app_id),
            ))

        # İkinci kademe: flatpak info'da lisans yoksa (mağaza yayıncısı
        # hiç doldurmamışsa) ve kimlik GitHub'ı işaret ediyorsa
        # (io.github.<kullanıcı>.<proje>), GitHub'ın kendi tespit ettiği
        # lisansı dene. Kimlik GitHub'ı işaret etmiyorsa hiç sorgulanmaz
        # (bkz. _github_license.py).
        still_missing = [p.id for p in packages if not p.license]
        if still_missing:
            github_licenses = licenses_for(still_missing)
            for pkg in packages:
                if not pkg.license and pkg.id in github_licenses:
                    pkg.license = github_licenses[pkg.id]

        return packages

    @staticmethod
    def _license(app_id: str) -> str:
        """flatpak info'nun "License:" satırı.

        Snap'in aksine (snap info paket başına ~300-500ms, mağazaya
        gidiyor) flatpak info tamamen yerel — ölçüldü: ~15ms, ağ yok.
        Bu yüzden Snap'teki gibi arka planda sonradan doldurmaya gerek
        yok, doğrudan burada, liste yüklenirken senkron çağrılabiliyor."""
        try:
            proc = host.run(
                ["env", "LC_ALL=C", "flatpak", "info", app_id], timeout=5
            )
        except Exception:
            return ""
        if proc.returncode != 0:
            return ""
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("License:"):
                return line[len("License:"):].strip()
        return ""

    @staticmethod
    def _install_path(app_id: str) -> str:
        for root in _INSTALL_ROOTS:
            path = os.path.join(root, app_id)
            if os.path.isdir(path):
                return path
        return ""

    @staticmethod
    def _install_date(app_id: str) -> float | None:
        # flatpak install tarihini ayrı vermiyor; etkin dağıtımın (deploy)
        # dizin mtime'ı kuruluşta veya son güncellemede yenilenir, en
        # yakın karşılığı bu.
        for root in _INSTALL_ROOTS:
            current = os.path.join(root, app_id, "current")
            try:
                target = os.readlink(current)
                deploy_dir = os.path.normpath(os.path.join(root, app_id, target))
                return os.stat(deploy_dir).st_mtime
            except OSError:
                continue
        return None

    @staticmethod
    def _publisher(app_id: str) -> str:
        """Uygulama kimliğindeki ters alan adından yayıncıyı çıkar.

        com.valvesoftware.Steam -> valvesoftware.com
        io.github.kolunmi.Bazaar -> kolunmi.github.io
        """
        parts = app_id.split(".")
        if len(parts) < 2:
            return ""
        hosts = ("github", "gitlab", "codeberg", "sourceforge", "frama")
        if len(parts) >= 3 and parts[1] in hosts:
            return f"{parts[2]}.{parts[1]}.{parts[0]}"
        return f"{parts[1]}.{parts[0]}"

    def remove_argv(self, ids: list[str]) -> list[str]:
        # list_packages() yalnızca gerçekten çakışan id'leri
        # "app_id:installation" biçiminde etiketler - burada bu etiket
        # geri çözülüp doğru --system/--user/--installation=AD bayrağına
        # çevrilir. "system"/"user" DIŞINDAKİ bir değer, /etc/flatpak/
        # installations.d altında tanımlanan ÖZEL ADLANDIRILMIŞ bir
        # sistem kurulumu olabilir (resmi belge: "--installation=NAME").
        #
        # Bayrak yalnızca TÜM id'ler etiketliyse VE hepsi AYNI kuruluma
        # işaret ediyorsa eklenir - "flatpak uninstall" tek bir çağrıda
        # tek bir kapsamla çalışır, bu yüzden etiketsiz (çakışmayan,
        # normal) bir id'yle etiketli birini AYNI çağrıda karıştırmak,
        # etiketsiz olana YANLIŞLIKLA yanlış bir kapsam dayatabilirdi.
        # Böyle karışık/belirsiz bir durumda bayrak HİÇ eklenmez - bu,
        # düzeltmeden ÖNCEki davranışla aynıdır (flatpak kendi
        # belgelenmiş "birden fazla kurulumda varsa hata ver" güvenlik
        # önlemine düşer, yanlış bir kopyayı sessizce hedeflemez).
        bare_ids = []
        scopes = set()
        all_tagged = True
        for id_ in ids:
            app_id, sep, scope = id_.partition(":")
            bare_ids.append(app_id)
            if sep and scope:
                scopes.add(scope)
            else:
                all_tagged = False

        argv = ["flatpak", "uninstall", "-y", "--noninteractive"]
        if all_tagged and len(scopes) == 1:
            scope = next(iter(scopes))
            if scope == "system":
                argv.append("--system")
            elif scope == "user":
                argv.append("--user")
            else:
                argv.append(f"--installation={scope}")
        return argv + bare_ids
