"""Inspector local de carpetas: tamaños reales, solo lectura, para la vista 3D."""

from __future__ import annotations

import errno
import hashlib
import heapq
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def resource_root() -> str:
    """Carpeta de recursos: código fuente o bundle de PyInstaller."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


ROOT = resource_root()
STATIC = os.path.join(ROOT, "static")

APP_VERSION = "1.4.0"
MAX_FILES = 7000
MAX_DIRS = 4000
PARTIAL_INTERVAL = 3.0
PARTIAL_NODE_CAP = 420
FAST_SIZE_PROGRESS_EVERY = 120
SLOW_OP_MS = 500.0
SLOW_OP_RING = 100
SLOW_OP_WORST = 50
REPARSE = 0x400
SKIP_READ = 0x1000 | 0x40000 | 0x400000  # offline, recall on open, recall on data access
ACCESS_DENIED_MSG = (
    "No hay acceso a esta carpeta (suele ser un enlace del sistema protegido). "
    "Prueba C:\\Users\\…"
)


class SlowOpLog:
    """Ring de operaciones I/O lentas + heap de las peores (sin pesar el caso rápido)."""

    def __init__(self, threshold_ms: float = SLOW_OP_MS, ring: int = SLOW_OP_RING, worst: int = SLOW_OP_WORST):
        self.threshold_ms = threshold_ms
        self.recent: deque[dict] = deque(maxlen=ring)
        self._worst: list[tuple] = []
        self._worst_cap = worst
        self._seq = 0

    def add(self, op: str, path: str, ms: float) -> None:
        if ms < self.threshold_ms:
            return
        self._seq += 1
        event = {
            "op": op,
            "path": path,
            "ms": round(ms, 1),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self.recent.append(event)
        item = (ms, self._seq, event)
        if len(self._worst) < self._worst_cap:
            heapq.heappush(self._worst, item)
        elif ms > self._worst[0][0]:
            heapq.heapreplace(self._worst, item)

    def recent_list(self) -> list[dict]:
        return list(self.recent)

    def top(self, n: int = 10) -> list[dict]:
        ranked = sorted(self._worst, key=lambda item: item[0], reverse=True)
        return [event for _ms, _seq, event in ranked[:n]]

    def count(self) -> int:
        return len(self.recent)


def is_access_denied(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if getattr(exc, "winerror", None) == 5:
        return True
    return getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM)


def log_access_denied(path: str, exc: BaseException) -> None:
    print(f"[acceso denegado] {path}: {exc}", file=sys.stderr)


def access_denied_user_message(path: str, exc: BaseException) -> str:
    log_access_denied(path, exc)
    return ACCESS_DENIED_MSG


def user_facing_os_error(path: str, exc: BaseException) -> str:
    if is_access_denied(exc):
        return access_denied_user_message(path, exc)
    return f"No se puede leer la carpeta: {exc}"


def is_reparse_dir(st: os.stat_result) -> bool:
    return bool(stat.S_ISDIR(st.st_mode) and (getattr(st, "st_file_attributes", 0) & REPARSE))


CATEGORIES = {
    "video": ["mp4", "mkv", "avi", "mov", "webm", "wmv", "m4v", "flv", "mpeg", "mpg"],
    "image": ["jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "svg", "heic", "ico", "psd", "tga", "dds"],
    "audio": ["mp3", "wav", "flac", "aac", "ogg", "m4a", "wma"],
    "document": ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "rtf", "odt", "csv", "md"],
    "code": ["js", "mjs", "ts", "py", "rs", "c", "cpp", "h", "hpp", "cs", "java", "go", "html", "css", "json", "xml", "yml", "yaml", "sql", "php", "rb", "vue", "jsx", "tsx"],
    "archive": ["zip", "rar", "7z", "tar", "gz", "bz2", "iso", "rpf"],
    "executable": ["exe", "msi", "dll", "bat", "cmd", "com", "scr", "ps1"],
}
EXT_TO_CAT = {ext: cat for cat, exts in CATEGORIES.items() for ext in exts}

LOCK = threading.Lock()
SCAN = {
    "state": "idle",
    "phase": "idle",
    "files": 0,
    "dirs": 0,
    "bytes": 0,
    "current": "",
    "started": 0.0,
    "error": "",
    "result": None,
    "scanner": None,
    "partial": None,
    "partial_seq": 0,
    "partial_at": 0.0,
    "hash_done": 0,
    "hash_total": 0,
    "detect_duplicates": False,
}


def category_for(ext: str) -> str:
    return EXT_TO_CAT.get((ext or "").lower(), "other")


def norm(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


def shortcuts() -> list[dict]:
    home = os.path.expanduser("~")
    candidates = [
        ("Inicio", home),
        ("Escritorio", os.path.join(home, "Desktop")),
        ("Escritorio", os.path.join(home, "Escritorio")),
        ("Documentos", os.path.join(home, "Documents")),
        ("Documentos", os.path.join(home, "Documentos")),
        ("Descargas", os.path.join(home, "Downloads")),
        ("Descargas", os.path.join(home, "Descargas")),
    ]
    found = []
    seen = set()
    for label, path in candidates:
        if not os.path.isdir(path):
            continue
        key = os.path.normcase(norm(path))
        if key in seen:
            continue
        seen.add(key)
        found.append({"label": label, "path": norm(path)})
    return found


def drives() -> list[str]:
    letters = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        path = f"{letter}:\\"
        if os.path.exists(path):
            letters.append(path)
    return letters


def parse_flag(value, default: bool = False) -> bool:
    """Interpreta duplicates=0|1 (o true/false) desde query/body."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on", "si", "sí"):
        return True
    if text in ("0", "false", "no", "off", ""):
        return False
    return default


class Scanner:
    def __init__(
        self,
        root: str,
        max_files: int = MAX_FILES,
        max_dirs: int = MAX_DIRS,
        detect_duplicates: bool = False,
    ):
        self.root = norm(root)
        self.max_files = max_files
        self.max_dirs = max_dirs
        self.detect_duplicates = detect_duplicates
        self.cancel = False
        self.files_heap: list[tuple] = []
        self.seq = 0
        self.dirs: dict[str, dict] = {}
        self.dir_count = 0
        self.files_seen = 0
        self.bytes = 0
        self.skipped = 0
        self.hit_dir_cap = False
        self.current = self.root
        self._ids = 0
        self.phase = "scanning"
        self._last_partial = 0.0
        self._partial_budget = {"left": PARTIAL_NODE_CAP}
        self.slow = SlowOpLog()
        self.phase_times: list[dict] = []
        self.scan_started = time.time()
        self.phase_started = self.scan_started
        self.hash_done = 0
        self.hash_total = 0

    def nid(self) -> int:
        self._ids += 1
        return self._ids

    def blank_dir(self, path: str) -> dict:
        return {
            "path": path,
            "name": os.path.basename(path) or path,
            "mtime": 0.0,
            "newest": 0.0,
            "oldest": 0.0,
            "file_bytes": 0,
            "file_count": 0,
            "child_dirs": [],
            "leaves": [],
        }

    def set_phase(self, phase: str) -> None:
        now = time.time()
        if self.phase and self.phase != phase:
            self.phase_times.append(
                {
                    "phase": self.phase,
                    "ms": round((now - self.phase_started) * 1000, 1),
                }
            )
        self.phase = phase
        self.phase_started = now
        self.publish()

    def timed_scandir(self, path: str) -> list:
        t0 = time.perf_counter()
        try:
            return list(os.scandir(path))
        finally:
            self.slow.add("scandir", path, (time.perf_counter() - t0) * 1000)

    def timed_stat(self, entry) -> os.stat_result:
        t0 = time.perf_counter()
        try:
            return entry.stat(follow_symlinks=False)
        finally:
            self.slow.add("stat", entry.path, (time.perf_counter() - t0) * 1000)

    def publish(self) -> None:
        with LOCK:
            if SCAN.get("scanner") is not self:
                return
            SCAN["files"] = self.files_seen
            SCAN["dirs"] = self.dir_count
            SCAN["bytes"] = self.bytes
            SCAN["current"] = self.current
            SCAN["phase"] = self.phase
            SCAN["hash_done"] = self.hash_done
            SCAN["hash_total"] = self.hash_total
            SCAN["detect_duplicates"] = self.detect_duplicates

    def store_partial(self, tree: dict, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_partial < PARTIAL_INTERVAL:
            return
        self._last_partial = now
        payload = {
            "demo": False,
            "partial": True,
            "root": tree,
            "root_path": self.root,
            "capped": True,
            "files_seen": self.files_seen,
            "files_kept": 0,
            "dirs": self.dir_count,
            "bytes": tree.get("size") or self.bytes,
            "skipped": self.skipped,
            "elapsed": 0,
            "duplicates": 0,
            "disk": None,
            "note": "Vista parcial mientras se analiza…",
            "phase": self.phase,
        }
        with LOCK:
            if SCAN.get("scanner") is not self:
                return
            SCAN["partial"] = payload
            SCAN["partial_seq"] = int(SCAN.get("partial_seq") or 0) + 1
            SCAN["partial_at"] = now
            SCAN["phase"] = self.phase
            SCAN["files"] = self.files_seen
            SCAN["dirs"] = self.dir_count
            SCAN["bytes"] = self.bytes
            SCAN["current"] = self.current

    def maybe_partial(self, force: bool = False) -> None:
        if self.cancel or self.root not in self.dirs:
            return
        now = time.time()
        if not force and now - self._last_partial < PARTIAL_INTERVAL:
            return
        try:
            self.compute(self.root)
            self._partial_budget = {"left": PARTIAL_NODE_CAP}
            tree = self.build_partial(self.root)
            self.store_partial(tree, force=True)
        except Exception:
            traceback.print_exc()

    def build_partial(self, path: str) -> dict:
        budget = self._partial_budget
        rec = self.dirs[path]
        children: list[dict] = []
        if budget["left"] > 0:
            for child_path in rec["child_dirs"]:
                if budget["left"] <= 0:
                    break
                budget["left"] -= 1
                children.append(self.build_partial(child_path))
            for leaf in rec["leaves"]:
                if budget["left"] <= 0:
                    break
                budget["left"] -= 1
                children.append(dict(leaf))
            if rec["file_bytes"] > 0 or rec["file_count"] > 0:
                if budget["left"] > 0:
                    budget["left"] -= 1
                    children.append(
                        {
                            "id": self.nid(),
                            "name": f"Archivos ({rec['file_count']})",
                            "path": None,
                            "dir": False,
                            "size": max(0, rec["file_bytes"]),
                            "mtime": rec["newest"] or rec["mtime"] or None,
                            "ext": "",
                            "category": "other",
                            "children": [],
                            "count": rec["file_count"],
                            "dup": None,
                            "dup_kind": None,
                            "dup_count": 0,
                            "aggregate": True,
                            "collapsed": False,
                        }
                    )
        return {
            "id": self.nid(),
            "name": rec["name"],
            "path": rec["path"],
            "dir": True,
            "size": rec.get("size") or rec["file_bytes"],
            "mtime": rec.get("newest") or rec["mtime"] or None,
            "oldest": rec.get("oldest") or None,
            "ext": "",
            "category": "folder",
            "children": children,
            "count": rec.get("count") or rec["file_count"],
            "dup": None,
            "dup_kind": None,
            "dup_count": 0,
            "aggregate": False,
            "collapsed": False,
        }

    def add_file(self, path: str, size: int, mtime: float, ext: str) -> None:
        self.files_seen += 1
        self.bytes += size
        parent = self.dirs.get(os.path.dirname(path))
        if parent is not None:
            parent["file_bytes"] += size
            parent["file_count"] += 1
            if mtime > parent["newest"]:
                parent["newest"] = mtime
            if mtime > 0 and (parent["oldest"] <= 0 or mtime < parent["oldest"]):
                parent["oldest"] = mtime
        self.seq += 1
        item = (size, self.seq, path, mtime, ext)
        if len(self.files_heap) < self.max_files:
            heapq.heappush(self.files_heap, item)
        elif size > self.files_heap[0][0]:
            heapq.heapreplace(self.files_heap, item)
        if self.files_seen % 200 == 0:
            self.publish()
            self.maybe_partial()

    def fast_size(self, path: str) -> tuple[int, int, float, float]:
        prev_phase = self.phase
        self.set_phase("measuring")
        t0 = time.perf_counter()
        total = 0
        count = 0
        newest = 0.0
        oldest = 0.0
        stack = [path]
        local_seen = 0
        try:
            while stack:
                if self.cancel:
                    break
                current = stack.pop()
                self.current = current
                try:
                    entries = self.timed_scandir(current)
                except OSError as exc:
                    if is_access_denied(exc):
                        log_access_denied(current, exc)
                    self.skipped += 1
                    continue
                for entry in entries:
                    if self.cancel:
                        break
                    try:
                        st = self.timed_stat(entry)
                    except OSError as exc:
                        if is_access_denied(exc):
                            log_access_denied(entry.path, exc)
                        self.skipped += 1
                        continue
                    if is_reparse_dir(st):
                        self.skipped += 1
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(entry.path)
                        continue
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    total += st.st_size
                    count += 1
                    local_seen += 1
                    if st.st_mtime > newest:
                        newest = st.st_mtime
                    if st.st_mtime > 0 and (oldest <= 0 or st.st_mtime < oldest):
                        oldest = st.st_mtime
                    if local_seen % FAST_SIZE_PROGRESS_EVERY == 0:
                        # progreso provisional dentro de la carpeta medida (sin doble-contar al final)
                        base_files = self.files_seen
                        base_bytes = self.bytes
                        self.files_seen = base_files + local_seen
                        self.bytes = base_bytes + total
                        self.publish()
                        self.maybe_partial()
                        self.files_seen = base_files
                        self.bytes = base_bytes
        finally:
            self.slow.add("fast_size", path, (time.perf_counter() - t0) * 1000)
        self.files_seen += count
        self.bytes += total
        self.publish()
        self.maybe_partial()
        if not self.cancel:
            self.set_phase(prev_phase if prev_phase else "scanning")
        return total, count, newest, oldest

    def execute(self) -> dict:
        started = time.time()
        if not os.path.isdir(self.root):
            raise ValueError("La ruta no es una carpeta")

        self.set_phase("scanning")
        root_rec = self.blank_dir(self.root)
        try:
            st = os.stat(self.root)
            root_rec["mtime"] = st.st_mtime
            root_rec["newest"] = st.st_mtime
        except OSError:
            pass
        self.dirs[self.root] = root_rec
        self.dir_count = 1
        queue = deque([self.root])
        self.maybe_partial(force=True)

        while queue:
            if self.cancel:
                break
            current = queue.popleft()
            rec = self.dirs[current]
            self.current = current
            self.publish()
            self.maybe_partial()
            try:
                entries = self.timed_scandir(current)
            except OSError as exc:
                self.skipped += 1
                if os.path.normcase(current) == os.path.normcase(self.root):
                    raise ValueError(user_facing_os_error(current, exc)) from exc
                if is_access_denied(exc):
                    log_access_denied(current, exc)
                continue
            for entry in entries:
                if self.cancel:
                    break
                try:
                    st = self.timed_stat(entry)
                except OSError as exc:
                    if is_access_denied(exc):
                        log_access_denied(entry.path, exc)
                    self.skipped += 1
                    continue
                if is_reparse_dir(st):
                    self.skipped += 1
                    continue
                if stat.S_ISDIR(st.st_mode):
                    path = norm(entry.path)
                    if self.dir_count >= self.max_dirs:
                        self.hit_dir_cap = True
                        size, count, newest, oldest = self.fast_size(path)
                        if size <= 0 and count <= 0:
                            continue
                        rec["leaves"].append(
                            {
                                "id": self.nid(),
                                "name": entry.name,
                                "path": path,
                                "dir": True,
                                "size": size,
                                "mtime": newest or st.st_mtime,
                                "oldest": oldest or None,
                                "ext": "",
                                "category": "folder",
                                "children": [],
                                "count": count,
                                "dup": None,
                                "dup_kind": None,
                                "dup_count": 0,
                                "aggregate": False,
                                "collapsed": True,
                            }
                        )
                        self.maybe_partial()
                        continue
                    child = self.blank_dir(path)
                    child["mtime"] = st.st_mtime
                    child["newest"] = st.st_mtime
                    self.dirs[path] = child
                    rec["child_dirs"].append(path)
                    self.dir_count += 1
                    queue.append(path)
                elif stat.S_ISREG(st.st_mode):
                    ext = os.path.splitext(entry.name)[1].lower().lstrip(".")
                    self.add_file(norm(entry.path), int(st.st_size), st.st_mtime, ext)

        if self.cancel:
            return {"cancelled": True}

        kept = [
            {
                "path": path,
                "size": size,
                "mtime": mtime,
                "ext": ext,
                "dup": None,
                "dup_kind": None,
                "dup_count": 0,
            }
            for size, _seq, path, mtime, ext in self.files_heap
        ]
        dup_groups = 0
        if self.detect_duplicates:
            self.hash_done = 0
            self.hash_total = 0
            self.current = "Buscando duplicados…"
            self.set_phase("hashing")
            self.maybe_partial(force=True)

            def on_hash_progress(done: int, total: int) -> None:
                if self.cancel:
                    return
                self.hash_done = done
                self.hash_total = total
                if total > 0:
                    self.current = f"Buscando duplicados… {done} / {total} grupos"
                else:
                    self.current = "Buscando duplicados…"
                self.publish()

            dup_groups = mark_duplicates(kept, on_progress=on_hash_progress, slow_log=self.slow)
            if self.cancel:
                return {"cancelled": True}
        kept_by_dir: dict[str, list] = defaultdict(list)
        for item in kept:
            kept_by_dir[os.path.dirname(item["path"])].append(item)

        self.compute(self.root)
        tree = self.build(self.root, kept_by_dir)
        elapsed = time.time() - started
        disk = None
        try:
            usage = shutil.disk_usage(self.root)
            disk = {"total": usage.total, "used": usage.used, "free": usage.free}
        except OSError:
            disk = None

        grouped = self.files_seen > len(kept)
        note = ""
        if self.hit_dir_cap or grouped:
            note = (
                f"Se leyeron {self.files_seen:,} archivos y {self.bytes:,} bytes. "
                f"El mapa detalla los {len(kept):,} archivos más grandes"
            )
            if self.hit_dir_cap:
                note += f" y {self.max_dirs:,} carpetas"
            note += ". El resto conserva su tamaño dentro de bloques «Otros» o carpetas cerradas."
            note = note.replace(",", ".")

        self.set_phase("done")
        return {
            "demo": False,
            "cancelled": False,
            "root": tree,
            "root_path": self.root,
            "capped": bool(self.hit_dir_cap or grouped),
            "files_seen": self.files_seen,
            "files_kept": len(kept),
            "dirs": self.dir_count,
            "bytes": tree["size"],
            "skipped": self.skipped,
            "elapsed": round(elapsed, 2),
            "duplicates": dup_groups,
            "disk": disk,
            "note": note,
        }

    def compute(self, path: str) -> tuple[int, int, float, float]:
        rec = self.dirs[path]
        total = rec["file_bytes"]
        count = rec["file_count"]
        newest = rec["newest"] or rec["mtime"]
        oldest = rec["oldest"] or 0.0
        for child in rec["child_dirs"]:
            c_size, c_count, c_new, c_old = self.compute(child)
            total += c_size
            count += c_count
            if c_new > newest:
                newest = c_new
            if c_old and (oldest <= 0 or c_old < oldest):
                oldest = c_old
        for leaf in rec["leaves"]:
            total += leaf["size"]
            count += leaf["count"]
            leaf_new = leaf.get("mtime") or 0
            leaf_old = leaf.get("oldest") or 0
            if leaf_new > newest:
                newest = leaf_new
            if leaf_old and (oldest <= 0 or leaf_old < oldest):
                oldest = leaf_old
        rec["size"] = total
        rec["count"] = count
        rec["newest"] = newest
        rec["oldest"] = oldest
        return total, count, newest, oldest

    def build(self, path: str, kept_by_dir: dict[str, list]) -> dict:
        rec = self.dirs[path]
        children = [self.build(child, kept_by_dir) for child in rec["child_dirs"]]
        children.extend(rec["leaves"])
        kept = kept_by_dir.get(path, [])
        kept_bytes = 0
        for item in kept:
            kept_bytes += item["size"]
            children.append(self.file_node(item))
        rest_bytes = rec["file_bytes"] - kept_bytes
        rest_count = rec["file_count"] - len(kept)
        if rest_bytes > 0 or rest_count > 0:
            children.append(
                {
                    "id": self.nid(),
                    "name": f"Otros ({rest_count} archivos)",
                    "path": None,
                    "dir": False,
                    "size": max(0, rest_bytes),
                    "mtime": None,
                    "ext": "",
                    "category": "other",
                    "children": [],
                    "count": rest_count,
                    "dup": None,
                    "dup_kind": None,
                    "dup_count": 0,
                    "aggregate": True,
                    "collapsed": False,
                }
            )
        return {
            "id": self.nid(),
            "name": rec["name"],
            "path": rec["path"],
            "dir": True,
            "size": rec["size"],
            "mtime": rec["newest"] or rec["mtime"] or None,
            "oldest": rec["oldest"] or None,
            "ext": "",
            "category": "folder",
            "children": children,
            "count": rec["count"],
            "dup": None,
            "dup_kind": None,
            "dup_count": 0,
            "aggregate": False,
            "collapsed": False,
        }

    def file_node(self, item: dict) -> dict:
        return {
            "id": self.nid(),
            "name": os.path.basename(item["path"]),
            "path": item["path"],
            "dir": False,
            "size": item["size"],
            "mtime": item["mtime"],
            "ext": item["ext"],
            "category": category_for(item["ext"]),
            "children": [],
            "count": 1,
            "dup": item["dup"],
            "dup_kind": item["dup_kind"],
            "dup_count": item["dup_count"],
            "aggregate": False,
            "collapsed": False,
        }


def can_read_local(path: str) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & SKIP_READ:
        return False
    return True


def fingerprint(path: str, size: int) -> str | None:
    if not can_read_local(path):
        return None
    digest = hashlib.md5()
    digest.update(str(size).encode("ascii"))
    try:
        with open(path, "rb") as handle:
            head = handle.read(1024 * 1024)
            digest.update(head)
            if size > len(head):
                handle.seek(max(0, size - 1024 * 1024))
                digest.update(handle.read(1024 * 1024))
    except OSError:
        return None
    return digest.hexdigest()


def mark_duplicates(files: list[dict], on_progress=None, slow_log: SlowOpLog | None = None) -> int:
    groups: dict[int, list[dict]] = defaultdict(list)
    for item in files:
        if item["size"] > 0:
            groups[item["size"]].append(item)
    candidates = [(size, group) for size, group in groups.items() if len(group) >= 2]
    total = len(candidates)
    if on_progress:
        on_progress(0, total)
    found = 0
    for index, (size, group) in enumerate(candidates, start=1):
        too_big = len(group) > 60 or size * len(group) > 8 * 1024**3
        buckets: dict[str, list[dict]] = defaultdict(list)
        if too_big:
            buckets["size"] = group
            kind = "size"
        else:
            kind = "hash"
            for item in group:
                t0 = time.perf_counter()
                token = fingerprint(item["path"], item["size"])
                if slow_log is not None:
                    slow_log.add("fingerprint", item["path"], (time.perf_counter() - t0) * 1000)
                if token:
                    buckets[token].append(item)
        for token, bucket in buckets.items():
            if len(bucket) < 2:
                continue
            found += 1
            label = token[:12] if kind == "hash" else f"tam-{size}"
            for item in bucket:
                item["dup"] = label
                item["dup_kind"] = "contenido" if kind == "hash" else "tamaño"
                item["dup_count"] = len(bucket)
        if on_progress:
            on_progress(index, total)
    return found


def demo_result() -> dict:
    now = time.time()
    seq = {"n": 0}

    def nid() -> int:
        seq["n"] += 1
        return seq["n"]

    def file(name, size, days, cat, ext, dup=None, dup_count=0):
        return {
            "id": nid(),
            "name": name,
            "path": None,
            "dir": False,
            "size": size,
            "mtime": now - days * 86400,
            "ext": ext,
            "category": cat,
            "children": [],
            "count": 1,
            "dup": dup,
            "dup_kind": "contenido" if dup else None,
            "dup_count": dup_count if dup else 0,
            "aggregate": False,
            "collapsed": False,
        }

    def folder(name, children):
        oldest = None
        newest = 0
        for child in children:
            child_old = child.get("oldest") or child.get("mtime") or 0
            child_new = child.get("mtime") or 0
            if child_old and (oldest is None or child_old < oldest):
                oldest = child_old
            if child_new > newest:
                newest = child_new
        return {
            "id": nid(),
            "name": name,
            "path": None,
            "dir": True,
            "size": sum(child["size"] for child in children),
            "mtime": newest,
            "oldest": oldest,
            "ext": "",
            "category": "folder",
            "children": children,
            "count": sum(child["count"] for child in children),
            "dup": None,
            "dup_kind": None,
            "dup_count": 0,
            "aggregate": False,
            "collapsed": False,
        }

    gb = 1024**3
    mb = 1024**2
    videos = folder(
        "Videos",
        [
            file("directo_largo.mp4", 18 * gb, 2, "video", "mp4"),
            file("clips_viejos.mkv", 6 * gb, 800, "video", "mkv"),
            file("intro.mov", 400 * mb, 20, "video", "mov"),
        ],
    )
    juegos = folder(
        "Juegos",
        [
            folder(
                "GTA",
                [
                    file("gta5.exe", 80 * mb, 400, "executable", "exe"),
                    folder(
                        "mods",
                        [
                            file("car.rpf", 2 * gb, 100, "archive", "rpf"),
                            file("texture.dds", 1200 * mb, 100, "image", "dds"),
                        ],
                    ),
                    file("update.rpf", 4 * gb, 30, "archive", "rpf"),
                ],
            ),
            folder(
                "iRacing",
                [
                    file("replay.rpy", 3 * gb, 5, "other", "rpy"),
                    file("paint.tga", 200 * mb, 5, "image", "tga"),
                ],
            ),
        ],
    )
    fotos = folder(
        "Fotos",
        [
            file("IMG_2044.jpg", 25 * mb, 12, "image", "jpg", "foto", 2),
            file("IMG_2044_copia.jpg", 25 * mb, 12, "image", "jpg", "foto", 2),
            file("viaje_01.jpg", 18 * mb, 40, "image", "jpg"),
            file("viaje_02.jpg", 16 * mb, 400, "image", "jpg"),
        ],
    )
    documentos = folder(
        "Documentos",
        [
            file("contrato.pdf", 4 * mb, 20, "document", "pdf"),
            file("notas.txt", 12 * 1024, 1, "document", "txt"),
        ],
    )
    instaladores = folder(
        "Instaladores",
        [
            file("backup_2019.iso", 12 * gb, 2000, "archive", "iso"),
            file("setup_cache.zip", int(1.5 * gb), 600, "archive", "zip", "setup", 2),
            file("setup_cache_copia.zip", int(1.5 * gb), 590, "archive", "zip", "setup", 2),
            file("archivo_viejo.rar", 2 * gb, 1800, "archive", "rar"),
            file("archivo_nuevo.rar", 2 * gb, 1, "archive", "rar"),
        ],
    )
    audio = folder(
        "Audio",
        [
            file("tema_stream.mp3", 40 * mb, 3, "audio", "mp3"),
            file("musica_vieja.flac", 700 * mb, 1200, "audio", "flac"),
        ],
    )
    codigo = folder(
        "Codigo",
        [
            file("app.js", 80 * 1024, 2, "code", "js"),
            file("server.py", 24 * 1024, 1, "code", "py"),
            {
                "id": nid(),
                "name": "Otros (840 archivos)",
                "path": None,
                "dir": False,
                "size": 380 * mb,
                "mtime": now - 15 * 86400,
                "ext": "",
                "category": "other",
                "children": [],
                "count": 840,
                "dup": None,
                "dup_kind": None,
                "dup_count": 0,
                "aggregate": True,
                "collapsed": False,
            },
        ],
    )
    root = folder("Ejemplo — disco", [videos, juegos, fotos, documentos, instaladores, audio, codigo])
    return {
        "demo": True,
        "cancelled": False,
        "root": root,
        "root_path": None,
        "capped": False,
        "files_seen": root["count"],
        "files_kept": root["count"],
        "dirs": 8,
        "bytes": root["size"],
        "skipped": 0,
        "elapsed": 0,
        "duplicates": 2,
        "disk": None,
        "note": "Ejemplo visual. La superficie es proporcional a los bytes; la altura, a la antigüedad. No son archivos de tu disco.",
    }


def clean_dir(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Falta la ruta")
    if "\x00" in path:
        raise ValueError("Ruta inválida")
    full = norm(path.strip().strip('"'))
    if not os.path.isdir(full):
        raise ValueError("La carpeta no existe")
    return full


def status_payload(include_partial: bool = True) -> dict:
    with LOCK:
        started = SCAN["started"]
        payload = {
            "state": SCAN["state"],
            "phase": SCAN.get("phase") or "idle",
            "files": SCAN["files"],
            "dirs": SCAN["dirs"],
            "bytes": SCAN["bytes"],
            "current": SCAN["current"],
            "elapsed": round(time.time() - started, 1) if started else 0,
            "error": SCAN["error"],
            "partial_seq": int(SCAN.get("partial_seq") or 0),
            "partial_at": SCAN.get("partial_at") or 0,
            "hash_done": int(SCAN.get("hash_done") or 0),
            "hash_total": int(SCAN.get("hash_total") or 0),
            "detect_duplicates": bool(SCAN.get("detect_duplicates")),
            "app_version": APP_VERSION,
        }
        if include_partial and SCAN.get("partial") is not None:
            payload["partial"] = SCAN["partial"]
        return payload


def build_debug_report() -> dict:
    """Informe liviano para que el cliente envíe feedback cuando el análisis se traba."""
    now = time.time()
    with LOCK:
        state = SCAN["state"]
        phase = SCAN.get("phase") or "idle"
        files = SCAN["files"]
        dirs = SCAN["dirs"]
        bytes_ = SCAN["bytes"]
        current = SCAN["current"]
        started = SCAN["started"]
        error = SCAN["error"] or ""
        scanner = SCAN.get("scanner")
        hash_done_scan = int(SCAN.get("hash_done") or 0)
        hash_total_scan = int(SCAN.get("hash_total") or 0)
        detect_duplicates_scan = bool(SCAN.get("detect_duplicates"))

    cancelled = bool(getattr(scanner, "cancel", False)) if scanner is not None else False
    if state == "cancelled":
        cancelled = True

    slow = getattr(scanner, "slow", None) if scanner is not None else None
    if not isinstance(slow, SlowOpLog):
        slow = SlowOpLog()
    phase_times = list(getattr(scanner, "phase_times", []) or []) if scanner is not None else []
    phase_started = float(getattr(scanner, "phase_started", started) or started or now)
    scan_started = float(getattr(scanner, "scan_started", started) or started or now)
    root = getattr(scanner, "root", None) if scanner is not None else None
    skipped = int(getattr(scanner, "skipped", 0) or 0) if scanner is not None else 0
    files_seen = int(getattr(scanner, "files_seen", files) or files) if scanner is not None else files
    hash_done = int(getattr(scanner, "hash_done", 0) or 0) if scanner is not None else hash_done_scan
    hash_total = int(getattr(scanner, "hash_total", 0) or 0) if scanner is not None else hash_total_scan
    detect_duplicates = bool(getattr(scanner, "detect_duplicates", False)) if scanner is not None else detect_duplicates_scan

    elapsed_total_s = round(max(0.0, now - scan_started), 2) if scan_started else 0.0
    phase_elapsed_s = round(max(0.0, now - phase_started), 2) if phase_started else 0.0
    top_slow = slow.top(10)
    recent = slow.recent_list()

    return {
        "app_version": APP_VERSION,
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "state": state,
        "phase": phase,
        "root": root,
        "files_seen": files_seen,
        "dirs": dirs,
        "bytes": bytes_,
        "skipped": skipped,
        "current": current,
        "detect_duplicates": detect_duplicates,
        "hash_done": hash_done,
        "hash_total": hash_total,
        "cancelled": cancelled,
        "error": error or None,
        "elapsed_total_s": elapsed_total_s,
        "phase_elapsed_s": phase_elapsed_s,
        "phases": phase_times,
        "slow_ops_threshold_ms": SLOW_OP_MS,
        "slow_ops_recent": recent,
        "top_slow": top_slow,
        "summary": {
            "slow_ops_recorded": slow.count(),
            "top_10": top_slow,
            "elapsed_total_s": elapsed_total_s,
            "phase_elapsed_s": phase_elapsed_s,
            "current_phase": phase,
            "detect_duplicates": detect_duplicates,
        },
    }


def reset_scan_fields(scanner=None, folder: str = "", phase: str = "scanning") -> None:
    SCAN["scanner"] = scanner
    SCAN["state"] = "running"
    SCAN["phase"] = phase
    SCAN["files"] = 0
    SCAN["dirs"] = 0
    SCAN["bytes"] = 0
    SCAN["current"] = folder
    SCAN["started"] = time.time()
    SCAN["error"] = ""
    SCAN["result"] = None
    SCAN["partial"] = None
    SCAN["partial_seq"] = 0
    SCAN["partial_at"] = 0.0
    SCAN["hash_done"] = 0
    SCAN["hash_total"] = 0
    SCAN["detect_duplicates"] = bool(getattr(scanner, "detect_duplicates", False)) if scanner else False


def start_scan(path: str, detect_duplicates: bool = False) -> None:
    folder = clean_dir(path)
    scanner = Scanner(folder, detect_duplicates=detect_duplicates)
    with LOCK:
        previous = SCAN.get("scanner")
        if previous is not None:
            previous.cancel = True
        reset_scan_fields(scanner, folder, "scanning")

    def run() -> None:
        try:
            result = scanner.execute()
        except Exception as exc:
            traceback.print_exc()
            if isinstance(exc, ValueError):
                msg = str(exc)
            elif is_access_denied(exc):
                msg = access_denied_user_message(folder, exc)
            else:
                msg = str(exc)
            with LOCK:
                if SCAN.get("scanner") is scanner:
                    SCAN["state"] = "error"
                    SCAN["phase"] = "error"
                    SCAN["error"] = msg
            return
        with LOCK:
            if SCAN.get("scanner") is not scanner:
                return
            if scanner.cancel or result.get("cancelled"):
                SCAN["state"] = "cancelled"
                SCAN["phase"] = "idle"
                return
            SCAN["result"] = result
            SCAN["state"] = "done"
            SCAN["phase"] = "done"
            SCAN["bytes"] = result["bytes"]
            SCAN["files"] = result["files_seen"]
            SCAN["partial"] = None

    threading.Thread(target=run, daemon=True).start()


class DemoSlowScanner:
    """Simula un análisis largo del ejemplo para probar la ciudad Matrix."""

    def __init__(self):
        self.cancel = False
        self.root = "Ejemplo — disco"
        self.files_seen = 0
        self.skipped = 0
        self.slow = SlowOpLog()
        self.phase_times: list[dict] = []
        self.scan_started = time.time()
        self.phase_started = self.scan_started
        self.phase = "scanning"

    def _set_phase(self, phase: str) -> None:
        now = time.time()
        if self.phase and self.phase != phase:
            self.phase_times.append(
                {
                    "phase": self.phase,
                    "ms": round((now - self.phase_started) * 1000, 1),
                }
            )
        self.phase = phase
        self.phase_started = now

    def run(self) -> dict | None:
        full = demo_result()
        children = list(full["root"]["children"] or [])
        with LOCK:
            if SCAN.get("scanner") is not self:
                return None
            SCAN["phase"] = "scanning"
            SCAN["current"] = self.root
            SCAN["dirs"] = 1
            SCAN["files"] = 0
            SCAN["bytes"] = 0
        self._set_phase("scanning")

        def folder_slice(n: int) -> dict:
            slice_kids = children[: max(1, n)]
            root = dict(full["root"])
            root["children"] = slice_kids
            root["size"] = sum(child["size"] for child in slice_kids)
            root["count"] = sum(child["count"] for child in slice_kids)
            return root

        steps = max(1, len(children))
        for index in range(1, steps + 1):
            if self.cancel:
                return {"cancelled": True}
            tree = folder_slice(index)
            files = tree["count"]
            bytes_ = tree["size"]
            payload = {
                "demo": True,
                "partial": True,
                "root": tree,
                "root_path": None,
                "capped": False,
                "files_seen": files,
                "files_kept": files,
                "dirs": 2 + index,
                "bytes": bytes_,
                "skipped": 0,
                "elapsed": 0,
                "duplicates": 0,
                "disk": None,
                "note": "Ejemplo lento (slowScan): construyendo ciudad…",
                "phase": "scanning",
            }
            with LOCK:
                if SCAN.get("scanner") is not self:
                    return None
                SCAN["phase"] = "scanning"
                SCAN["files"] = files
                SCAN["dirs"] = 2 + index
                SCAN["bytes"] = bytes_
                SCAN["current"] = f"Ejemplo / {children[index - 1]['name']}"
                SCAN["partial"] = payload
                SCAN["partial_seq"] = int(SCAN.get("partial_seq") or 0) + 1
                SCAN["partial_at"] = time.time()
            self.files_seen = files
            t0 = time.perf_counter()
            time.sleep(2.8)
            self.slow.add("scandir", f"Ejemplo / {children[index - 1]['name']}", (time.perf_counter() - t0) * 1000)
            if self.cancel:
                return {"cancelled": True}

        with LOCK:
            if SCAN.get("scanner") is not self:
                return None
            SCAN["phase"] = "measuring"
            SCAN["current"] = "Ejemplo / midiendo carpeta grande…"
        self._set_phase("measuring")
        t0 = time.perf_counter()
        time.sleep(4.5)
        self.slow.add("fast_size", "Ejemplo / midiendo carpeta grande…", (time.perf_counter() - t0) * 1000)
        if self.cancel:
            return {"cancelled": True}

        with LOCK:
            if SCAN.get("scanner") is not self:
                return None
            SCAN["phase"] = "hashing"
            SCAN["current"] = "Buscando duplicados…"
            tree = folder_slice(steps)
            SCAN["partial"] = {
                "demo": True,
                "partial": True,
                "root": tree,
                "root_path": None,
                "capped": False,
                "files_seen": tree["count"],
                "files_kept": tree["count"],
                "dirs": 8,
                "bytes": tree["size"],
                "skipped": 0,
                "elapsed": 0,
                "duplicates": 0,
                "disk": None,
                "note": "Ejemplo lento: finalizando…",
                "phase": "hashing",
            }
            SCAN["partial_seq"] = int(SCAN.get("partial_seq") or 0) + 1
            SCAN["partial_at"] = time.time()
        self._set_phase("hashing")
        self.files_seen = tree["count"]
        t0 = time.perf_counter()
        time.sleep(3.2)
        self.slow.add("fingerprint", "Ejemplo / duplicados", (time.perf_counter() - t0) * 1000)
        if self.cancel:
            return {"cancelled": True}
        return full


def start_demo(slow: bool = False) -> dict | None:
    with LOCK:
        previous = SCAN.get("scanner")
        if previous is not None:
            previous.cancel = True

    if not slow:
        result = demo_result()
        with LOCK:
            SCAN["scanner"] = None
            SCAN["state"] = "done"
            SCAN["phase"] = "done"
            SCAN["result"] = result
            SCAN["error"] = ""
            SCAN["bytes"] = result["bytes"]
            SCAN["files"] = result["files_seen"]
            SCAN["dirs"] = result.get("dirs") or 0
            SCAN["current"] = ""
            SCAN["partial"] = None
            SCAN["partial_seq"] = int(SCAN.get("partial_seq") or 0)
            SCAN["partial_at"] = 0.0
        return result

    demo = DemoSlowScanner()
    with LOCK:
        reset_scan_fields(demo, "Ejemplo — disco", "scanning")

    def run() -> None:
        try:
            result = demo.run()
        except Exception as exc:
            traceback.print_exc()
            with LOCK:
                if SCAN.get("scanner") is demo:
                    SCAN["state"] = "error"
                    SCAN["phase"] = "error"
                    SCAN["error"] = str(exc)
            return
        with LOCK:
            if SCAN.get("scanner") is not demo:
                return
            if demo.cancel or not result or result.get("cancelled"):
                SCAN["state"] = "cancelled"
                SCAN["phase"] = "idle"
                return
            SCAN["result"] = result
            SCAN["state"] = "done"
            SCAN["phase"] = "done"
            SCAN["bytes"] = result["bytes"]
            SCAN["files"] = result["files_seen"]
            SCAN["partial"] = None

    threading.Thread(target=run, daemon=True).start()
    return None


def browse(path: str | None) -> dict:
    folder = clean_dir(path or os.path.expanduser("~"))
    parent = os.path.dirname(folder)
    if os.path.normcase(parent) == os.path.normcase(folder):
        parent = None
    dirs = []
    try:
        entries = list(os.scandir(folder))
    except OSError as exc:
        raise ValueError(user_facing_os_error(folder, exc)) from exc
    for entry in entries:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            if is_access_denied(exc):
                log_access_denied(entry.path, exc)
            continue
        # Saltar junctions/reparse (Documents and Settings, Archivos de programa, …)
        if is_reparse_dir(st):
            continue
        if not stat.S_ISDIR(st.st_mode):
            continue
        dirs.append({"name": entry.name, "path": norm(entry.path)})
    dirs.sort(key=lambda item: item["name"].lower())
    truncated = len(dirs) > 500
    return {
        "path": folder,
        "parent": parent,
        "dirs": dirs[:500],
        "truncated": truncated,
        "drives": drives(),
        "shortcuts": shortcuts(),
    }


def existing_path(path: str) -> str:
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise ValueError("Falta la ruta")
    full = norm(path.strip().strip('"'))
    if not os.path.exists(full):
        raise ValueError("La ruta no existe")
    return full


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("JSON inválido") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON inválido")
        return data

    def static_file(self, url_path: str) -> str | None:
        rel = url_path.lstrip("/") or "index.html"
        full = os.path.realpath(os.path.join(STATIC, rel))
        static_root = os.path.realpath(STATIC)
        if full != static_root and not full.startswith(static_root + os.sep):
            return None
        if os.path.isfile(full):
            return full
        return None

    def send_file(self, path: str) -> None:
        ext = os.path.splitext(path)[1].lower()
        mime = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }.get(ext, "application/octet-stream")
        with open(path, "rb") as handle:
            data = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/browse":
                query = parse_qs(parsed.query)
                raw = query.get("path", [None])[0]
                self.send_json(browse(raw))
                return
            if parsed.path == "/api/scan":
                self.send_json(status_payload(include_partial=True))
                return
            if parsed.path == "/api/scan/debug":
                report = build_debug_report()
                stamp = time.strftime("%Y%m%d-%H%M%S")
                data = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="ArchivosInspector-debug-{stamp}.json"',
                )
                self.end_headers()
                self.wfile.write(data)
                return
            if parsed.path == "/api/scan/partial":
                with LOCK:
                    partial = SCAN.get("partial")
                    seq = int(SCAN.get("partial_seq") or 0)
                    phase = SCAN.get("phase") or "idle"
                    state = SCAN["state"]
                if partial is None:
                    self.send_json({"error": "Sin árbol parcial", "partial_seq": seq, "phase": phase, "state": state}, 404)
                    return
                self.send_json({"partial_seq": seq, "phase": phase, "state": state, "result": partial})
                return
            if parsed.path == "/api/result":
                with LOCK:
                    result = SCAN["result"]
                    state = SCAN["state"]
                if state != "done" or result is None:
                    self.send_json({"error": "Todavía no hay un análisis listo"}, 404)
                    return
                self.send_json(result)
                return
            if parsed.path.startswith("/api/"):
                self.send_json({"error": "Ruta no encontrada"}, 404)
                return
            target = self.static_file(parsed.path)
            if target is None:
                self.send_json({"error": "No encontrado"}, 404)
                return
            self.send_file(target)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            traceback.print_exc()
            self.send_json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            body = self.read_json()
            if parsed.path == "/api/scan":
                query = parse_qs(parsed.query)
                raw_flag = body.get("duplicates")
                if raw_flag is None and "duplicates" in query:
                    raw_flag = query.get("duplicates", [None])[0]
                start_scan(
                    str(body.get("path") or ""),
                    detect_duplicates=parse_flag(raw_flag, default=False),
                )
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/cancel":
                with LOCK:
                    scanner = SCAN.get("scanner")
                    if scanner is not None:
                        scanner.cancel = True
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/demo":
                slow = bool(body.get("slow") or body.get("slowScan"))
                result = start_demo(slow=slow)
                if result is None:
                    self.send_json({"ok": True, "slow": True, "state": "running"})
                else:
                    self.send_json(result)
                return
            if parsed.path in ("/api/open", "/api/reveal"):
                path = existing_path(str(body.get("path") or ""))
                if parsed.path == "/api/open":
                    os.startfile(path)  # type: ignore[attr-defined]
                elif os.path.isdir(path):
                    subprocess.Popen(["explorer", path])
                else:
                    subprocess.Popen(["explorer", "/select,", path])
                self.send_json({"ok": True})
                return
            self.send_json({"error": "Ruta no encontrada"}, 404)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            traceback.print_exc()
            self.send_json({"error": str(exc)}, 500)


def main() -> None:
    port0 = int(sys.argv[1]) if len(sys.argv) > 1 else 8877
    httpd = None
    port = port0
    for port in range(port0, port0 + 15):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            httpd = None
    if httpd is None:
        raise SystemExit("No hay un puerto libre para Archivos Inspector")
    url = f"http://127.0.0.1:{port}/"
    print(url, flush=True)
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Detenido", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
