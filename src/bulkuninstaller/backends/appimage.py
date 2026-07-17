import os

from .base import Backend, Package, RemoveResult

SCAN_DIRS = (
    "~/Applications",
    "~/AppImages",
    "~/.local/bin",
    "~/bin",
    "~/Desktop",
    "~/Masaüstü",
    "~/Downloads",
    "~/İndirilenler",
)


class AppImageBackend(Backend):
    """Ev dizinindeki .AppImage dosyaları.

    AppImage'ler paket yöneticisine kayıtlı değildir; bilinen
    klasörlerdeki dosyalar taranarak bulunur. Kaldırmak = dosyayı silmek.
    """

    id = "appimage"
    display_name = "AppImage"
    needs_root = False

    def is_available(self) -> bool:
        return bool(self._scan())

    def list_packages(self) -> list[Package]:
        packages = []
        for path in self._scan():
            base = os.path.basename(path)
            name = base
            for ext in (".AppImage", ".appimage"):
                if name.endswith(ext):
                    name = name[: -len(ext)]
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            packages.append(Package(
                id=path,  # kaldırma dosya yoluyla yapılır
                name=name,
                version="",
                size=size,
                description=path,
                source=self.id,
                origin=os.path.basename(os.path.dirname(path)),
            ))
        return packages

    def _scan(self) -> list[str]:
        found = []
        for base in SCAN_DIRS:
            base_dir = os.path.expanduser(base)
            try:
                entries = os.listdir(base_dir)
            except OSError:
                continue
            for entry in entries:
                if entry.lower().endswith(".appimage"):
                    found.append(os.path.join(base_dir, entry))
        return sorted(found)

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["rm", "--"] + ids  # kullanılmıyor; remove() ezildi

    def remove(self, ids: list[str]) -> RemoveResult:
        errors = []
        for path in ids:
            try:
                os.remove(path)
            except OSError as exc:
                errors.append(f"{path}: {exc}")
        return RemoveResult(
            ok=not errors,
            output="\n".join(errors),
            failed_ids=[e.split(":", 1)[0] for e in errors],
        )
