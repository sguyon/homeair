"""The checked, bundled NYC clip for the demo's routes. Never downloads or reads a home's road cache."""
import hashlib
import json
import os
from pathlib import Path

import road_data
from roads import haversine_m, local_xy

PACKAGE_DIR = Path(os.path.realpath(__file__)).parent
FOLDER = PACKAGE_DIR / "road-data"


def _read_bundle_file(root, filename, directory_fd):
    """Check containment, then open the original filename without following a symlink."""
    path = root / filename
    if not path.resolve(strict=True).is_relative_to(root) or path.is_symlink():
        raise ValueError("Demo road file is outside the bundle or is a symlink")
    fd = os.open(filename if directory_fd is not None else path,
                 os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
    try:
        source = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)  # fdopen failed (e.g. a directory): don't leak the descriptor
        raise
    with source:
        return source.read()


def route_stretch(name, key, lat, lon):
    """Clip the manifest's file, parsing only the bytes whose sha256 was checked."""
    root = PACKAGE_DIR / "road-data"
    if FOLDER != root or FOLDER.is_symlink() or Path(os.path.realpath(FOLDER, strict=True)) != root:
        raise ValueError("Demo road bundle is outside the packaged directory or is a symlink")
    # A directory descriptor keeps both reads in this bundle even if its path is replaced.
    directory_fd = (os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
                    if os.open in os.supports_dir_fd else None)
    try:
        manifest = json.loads(_read_bundle_file(root, "manifest.json", directory_fd).decode("utf-8"))
        filename = manifest["file"]
        if not isinstance(filename, str) or not road_data.DATA_NAME.fullmatch(filename) or name != filename:
            raise ValueError("Invalid demo road filename")
        raw = _read_bundle_file(root, filename, directory_fd)
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        raise ValueError("Demo road file does not match its sha256")
    doc = road_data._doc(raw)
    inside = lambda p: haversine_m(lat, lon, p[1], p[0]) <= road_data.ROUTE_CLIP_M
    to_xy = local_xy(lat, lon)
    return [piece for feature in doc["features"]
            if feature["properties"].get("g") == key
            and not feature["properties"].get("l") and not feature["properties"].get("t")
            for piece in road_data._clip(feature["geometry"]["coordinates"], inside, to_xy)]
