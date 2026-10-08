#!/usr/bin/env python3
"""Benchmark eager materialization against lazy first-access behavior."""
import argparse
import json
import shutil
import time
from pathlib import Path


STARTUP_FILES = [
    "/app/startup.txt",
    "/app/config.txt",
]


def load_meta(image):
    return json.loads((Path(image) / "metadata.json").read_text(encoding="utf-8"))


def clear_directory(directory):
    """Clear a normal directory without ever touching an active FUSE mount."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for item in directory.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def directory_size(directory):
    directory = Path(directory)
    if not directory.exists():
        return 0
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def eager_materialization(image, destination):
    meta = load_meta(image)
    blobs = Path(image) / "blobs"
    clear_directory(destination)

    start = time.perf_counter()
    total_bytes = 0

    for path, entry in meta["files"].items():
        target = Path(destination) / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(blobs / entry["sha256"], target)
        total_bytes += entry["size"]

    return time.perf_counter() - start, total_bytes


def read_startup_working_set(lazy_root):
    start = time.perf_counter()
    requested_bytes = 0

    for path in STARTUP_FILES:
        file_path = Path(lazy_root) / path.lstrip("/")
        with file_path.open("rb") as f:
            data = f.read()
            requested_bytes += len(data)

    return time.perf_counter() - start, requested_bytes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--blob-url", required=True, help="documented for reproducibility; filesystem uses it")
    parser.add_argument("--lazy-root", default="/tmp/lazy-root")
    parser.add_argument("--eager-root", default="/tmp/eager-root")
    parser.add_argument("--cache", default="/tmp/lazy-cache")
    args = parser.parse_args()

    image = Path(args.image)
    lazy_root = Path(args.lazy_root)
    eager_root = Path(args.eager_root)
    cache = Path(args.cache)

    if not lazy_root.exists():
        raise SystemExit(f"Lazy filesystem mount not found: {lazy_root}")

    meta = load_meta(image)
    logical_bytes = sum(entry["size"] for entry in meta["files"].values())

    print("=" * 60)
    print("Lazy Container Filesystem Benchmark")
    print("=" * 60)
    print(f"Logical image content: {logical_bytes / 1024 / 1024:.2f} MiB")
    print(f"Startup files: {', '.join(STARTUP_FILES)}")
    print()

    eager_time, eager_bytes = eager_materialization(image, eager_root)
    print(f"EAGER materialization: {eager_time:.4f}s")
    print(f"EAGER bytes materialized: {eager_bytes / 1024 / 1024:.2f} MiB")
    print()

    clear_directory(cache)
    before_cache = directory_size(cache)
    lazy_cold_time, requested_bytes = read_startup_working_set(lazy_root)
    after_cache = directory_size(cache)
    downloaded_bytes = after_cache - before_cache

    print(f"LAZY cold startup working set: {lazy_cold_time:.4f}s")
    print(f"LAZY requested bytes: {requested_bytes / 1024 / 1024:.6f} MiB")
    print(f"LAZY bytes downloaded/cached: {downloaded_bytes / 1024 / 1024:.6f} MiB")
    print()

    lazy_warm_time, _ = read_startup_working_set(lazy_root)
    print(f"LAZY warm-cache startup working set: {lazy_warm_time:.4f}s")
    print(f"Cache size after test: {directory_size(cache)} bytes")
    print()

    print("=" * 60)
    print("Interpretation")
    print("=" * 60)
    print("Eager mode materializes the complete logical image.")
    print("Lazy mode fetches only files actually accessed during startup.")
    print("This benchmark does not claim that the Docker runtime's image pull/extraction path was replaced.")
    print("Container startup should be measured separately in the demo.")


if __name__ == "__main__":
    main()
