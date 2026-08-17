"""Lutris kütüphanesindeki oyunlar (yalnızca Unused Apps taraması için).

Lutris CLI'sinde oyun silme komutu yok (yalnızca -u/--uninstall-runner
var, o da Wine sürümü siler) — bu yüzden silme, oyun klasörünü silip
pga.db'de installed=0 işaretlemek şeklinde yapılır. Bu, Lutris'in
kütüphanede "kurulu değil" göstermesiyle aynı sonucu verir ama
bazı oyunların kendi özel kaldırma betiklerini atlar.

Bu makinede Lutris kurulu ama hiç kullanılmamış (pga.db yok); şema
iyi bilinen/stabil olsa da canlı doğrulanamadı.
"""

import os
import shutil
import sqlite3

from ..backends.base import Backend, Package, RemoveResult

PGA_DB_PATH = "~/.local/share/lutris/pga.db"


def _db_path() -> str | None:
    path = os.path.expanduser(PGA_DB_PATH)
    return path if os.path.isfile(path) else None


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


class LutrisGameBackend(Backend):
    id = "lutris"
    display_name = "Lutris"
    needs_root = False

    def is_available(self) -> bool:
        return _db_path() is not None

    def list_packages(self) -> list[Package]:
        path = _db_path()
        if not path:
            return []
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT id, name, directory, lastplayed "
                    "FROM games WHERE installed = 1"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return []

        packages = []
        for row_id, name, directory, lastplayed in rows:
            if not name:
                continue
            size = 0
            install_date = None
            if directory and os.path.isdir(directory):
                size = _dir_size(directory)
                try:
                    install_date = os.stat(directory).st_mtime
                except OSError:
                    pass
            packages.append(Package(
                id=str(row_id),
                name=name,
                version="",
                size=size,
                description="",
                source=self.id,
                publisher="Lutris",
                last_used=float(lastplayed) if lastplayed else None,
                install_path=directory or "",
                install_date=install_date,
            ))
        return packages

    def remove_argv(self, ids: list[str]) -> list[str]:
        return ["true"]  # remove() pga.db'yi ve dizini doğrudan işler

    def remove(self, ids: list[str]) -> RemoveResult:
        path = _db_path()
        if not path:
            return RemoveResult(False, "Lutris database not found", failed_ids=ids)

        try:
            conn = sqlite3.connect(path)
        except sqlite3.Error as exc:
            return RemoveResult(False, str(exc), failed_ids=ids)

        failed = []
        messages = []
        try:
            for row_id in ids:
                try:
                    row = conn.execute(
                        "SELECT directory FROM games WHERE id = ?", (row_id,)
                    ).fetchone()
                    directory = row[0] if row else None
                    if directory and os.path.isdir(directory):
                        shutil.rmtree(directory)
                    conn.execute(
                        "UPDATE games SET installed = 0 WHERE id = ?", (row_id,)
                    )
                    conn.commit()
                except (OSError, sqlite3.Error) as exc:
                    failed.append(row_id)
                    messages.append(f"{row_id}: {exc}")
        finally:
            conn.close()

        if failed:
            return RemoveResult(False, "\n".join(messages), failed_ids=failed)
        return RemoveResult(True, "")
