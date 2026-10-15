from .. import host
from .base import Backend, Package


class XbpsBackend(Backend):
    """Void Linux."""

    id = "xbps"
    display_name = "XBPS"
    needs_root = True

    def is_available(self) -> bool:
        return host.command_exists("xbps-query") and host.command_exists("xbps-remove")

    def list_packages(self) -> list[Package]:
        # "ii paketadi-1.2_1 kısa açıklama" satırları
        proc = host.run(["xbps-query", "-l"], timeout=300)
        if proc.returncode != 0:
            return []

        packages = []
        for line in proc.stdout.splitlines():
            parts = line.split(None, 2)
            if len(parts) < 2:
                continue
            # xbps-query -l yalnızca tam kurulu paketleri değil, "uu"
            # (açılmış ama yapılandırılmamış), "hr" (yarı kaldırılmış,
            # xbps-remove ile TAMAMLANMASI gereken) ve "??" (bilinmeyen)
            # durumdaki kayıtları da listeleyebilir (bkz. xbps-query(1)).
            # Bunlar gerçek, çalışır bir uygulama değil - normal listeye
            # alınırsa kullanıcı zaten bozuk/yarım bir kaydı sağlıklı bir
            # uygulamaymış gibi kaldırmaya çalışabilir.
            if parts[0] != "ii":
                continue
            pkgver = parts[1]
            name, _, version = pkgver.rpartition("-")
            if not name:
                name, version = pkgver, ""
            packages.append(Package(
                id=name,
                name=name,
                version=version,
                size=0,
                description=parts[2] if len(parts) > 2 else "",
                source=self.id,
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        # -R: kullanılmayan bağımlılıkları da kaldır, -y: sorma
        return ["xbps-remove", "-Ry"] + ids
