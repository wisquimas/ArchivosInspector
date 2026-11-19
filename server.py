"""Inspector local de carpetas: tamaños reales, solo lectura, para la vista 3D."""

from __future__ import annotations

import hashlib
import heapq
import json
import os
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

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")

MAX_FILES = 7000
MAX_DIRS = 4000
REPARSE = 0x400
SKIP_READ = 0x1000 | 0x40000 | 0x400000  # offline, recall on open, recall on data access

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
    "files": 0,
    "dirs": 0,
    "bytes": 0,
    "current": "",
    "started": 0.0,
    "error": "",
    "result": None,
    "scanner": None,
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


class Scanner:
    def __init__(self, root: str, max_files: int = MAX_FILES, max_dirs: int = MAX_DIRS):
        self.root = norm(root)
        self.max_files = max_files
        self.max_dirs = max_dirs
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

    def publish(self) -> None:
        with LOCK:
            if SCAN.get("scanner") is not self:
                return
            SCAN["files"] = self.files_seen
            SCAN["dirs"] = self.dir_count
            SCAN["bytes"] = self.bytes
            SCAN["current"] = self.current

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

    def fast_size(self, path: str) -> tuple[int, int, float, float]:
        total = 0
        count = 0
        newest = 0.0
        oldest = 0.0
        stack = [path]
        while stack:
            if self.cancel:
                break
            current = stack.pop()
            self.current = current
            try:
                entries = list(os.scandir(current))
            except OSError:
                self.skipped += 1
                continue
            for entry in entries:
                if self.cancel:
                    break
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    self.skipped += 1
                    continue
                if stat.S_ISDIR(st.st_mode):
                    if getattr(st, "st_file_attributes", 0) & REPARSE:
                        self.skipped += 1
                        continue
                    stack.append(entry.path)
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                total += st.st_size
                count += 1
                if st.st_mtime > newest:
                    newest = st.st_mtime
                if st.st_mtime > 0 and (oldest <= 0 or st.st_mtime < oldest):
                    oldest = st.st_mtime
        self.files_seen += count
        self.bytes += total
        self.publish()
        return total, count, newest, oldest

    def execute(self) -> dict:
        started = time.time()
        if not os.path.isdir(self.root):
            raise ValueError("La ruta no es una carpeta")

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

        while queue:
            if self.cancel:
                break
            current = queue.popleft()
            rec = self.dirs[current]
            self.current = current
            self.publish()
            try:
                entries = list(os.scandir(current))
            except OSError:
                self.skipped += 1
                continue
            for entry in entries:
                if self.cancel:
                    break
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    self.skipped += 1
                    continue
                attrs = getattr(st, "st_file_attributes", 0)
                if stat.S_ISDIR(st.st_mode):
                    if attrs & REPARSE:
                        self.skipped += 1
                        continue
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
        dup_groups = mark_duplicates(kept)
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


def mark_duplicates(files: list[dict]) -> int:
    groups: dict[int, list[dict]] = defaultdict(list)
    for item in files:
        if item["size"] > 0:
            groups[item["size"]].append(item)
    found = 0
    for size, group in groups.items():
        if len(group) < 2:
            continue
        too_big = len(group) > 60 or size * len(group) > 8 * 1024**3
        buckets: dict[str, list[dict]] = defaultdict(list)
        if too_big:
            buckets["size"] = group
            kind = "size"
        else:
            kind = "hash"
            for item in group:
                token = fingerprint(item["path"], item["size"])
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


def status_payload() -> dict:
    with LOCK:
        started = SCAN["started"]
        return {
            "state": SCAN["state"],
            "files": SCAN["files"],
            "dirs": SCAN["dirs"],
            "bytes": SCAN["bytes"],
            "current": SCAN["current"],
            "elapsed": round(time.time() - started, 1) if started else 0,
            "error": SCAN["error"],
        }


def start_scan(path: str) -> None:
    folder = clean_dir(path)
    scanner = Scanner(folder)
    with LOCK:
        previous = SCAN.get("scanner")
        if previous is not None:
            previous.cancel = True
        SCAN["scanner"] = scanner
        SCAN["state"] = "running"
        SCAN["files"] = 0
        SCAN["dirs"] = 0
        SCAN["bytes"] = 0
        SCAN["current"] = folder
        SCAN["started"] = time.time()
        SCAN["error"] = ""
        SCAN["result"] = None

    def run() -> None:
        try:
            result = scanner.execute()
        except Exception as exc:
            traceback.print_exc()
            with LOCK:
                if SCAN.get("scanner") is scanner:
                    SCAN["state"] = "error"
                    SCAN["error"] = str(exc)
            return
        with LOCK:
            if SCAN.get("scanner") is not scanner:
                return
            if scanner.cancel or result.get("cancelled"):
                SCAN["state"] = "cancelled"
                return
            SCAN["result"] = result
            SCAN["state"] = "done"
            SCAN["bytes"] = result["bytes"]
            SCAN["files"] = result["files_seen"]

    threading.Thread(target=run, daemon=True).start()


def browse(path: str | None) -> dict:
    folder = clean_dir(path or os.path.expanduser("~"))
    parent = os.path.dirname(folder)
    if os.path.normcase(parent) == os.path.normcase(folder):
        parent = None
    dirs = []
    try:
        entries = list(os.scandir(folder))
    except OSError as exc:
        raise ValueError(f"No se puede leer la carpeta: {exc}") from exc
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                dirs.append({"name": entry.name, "path": norm(entry.path)})
        except OSError:
            continue
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
                self.send_json(status_payload())
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
                start_scan(str(body.get("path") or ""))
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
                result = demo_result()
                with LOCK:
                    previous = SCAN.get("scanner")
                    if previous is not None:
                        previous.cancel = True
                    SCAN["scanner"] = None
                    SCAN["state"] = "done"
                    SCAN["result"] = result
                    SCAN["error"] = ""
                    SCAN["bytes"] = result["bytes"]
                    SCAN["files"] = result["files_seen"]
                    SCAN["current"] = ""
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
