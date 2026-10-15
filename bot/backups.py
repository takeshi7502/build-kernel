"""Validate backup contents before any live state or catalog file is replaced."""

import json
import zipfile
from pathlib import Path, PurePosixPath

from .storage import HybridStorage


def read_backup(archive_path, web_root):
    """Return (state, catalog files), rejecting paths unsafe on Linux or Windows."""
    snapshot = None
    files = []
    seen = set()
    root = Path(web_root).resolve()
    with zipfile.ZipFile(archive_path) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 50 * 1024 * 1024:
            raise ValueError("Bản backup giải nén vượt quá 50 MB.")
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if (
                "\\" in entry.orig_filename
                or path.is_absolute()
                or ".." in path.parts
                or any(":" in part for part in path.parts)
            ):
                raise ValueError("Bản backup chứa đường dẫn không hợp lệ.")
            if entry.is_dir():
                continue
            if str(path) in seen:
                raise ValueError("Bản backup chứa file trùng đường dẫn.")
            seen.add(str(path))
            if entry.filename == "data.json":
                snapshot = json.loads(archive.read(entry).decode("utf-8"))
                HybridStorage._validate_data(snapshot)
            elif len(path.parts) > 1 and path.parts[0] == "web_data" and path.suffix == ".json":
                relative = Path(*path.parts[1:])
                if not (root / relative).resolve().is_relative_to(root):
                    raise ValueError("Đường dẫn catalog nằm ngoài thư mục dữ liệu.")
                content = json.loads(archive.read(entry).decode("utf-8"))
                if not isinstance(content, dict):
                    raise ValueError("Dữ liệu website phải là JSON object.")
                files.append((relative, content))
    if snapshot is None and not files:
        raise ValueError("Không tìm thấy data.json hoặc web_data/*.json.")
    return snapshot, files
