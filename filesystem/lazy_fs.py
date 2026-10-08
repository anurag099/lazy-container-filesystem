#!/usr/bin/env python3
"""Read-only content-addressed lazy filesystem PoC using FUSE."""
import argparse
import errno
import hashlib
import json
import os
import stat
import tempfile
import time
from pathlib import PurePosixPath

import requests
from fuse import FUSE, FuseOSError, Operations


class LazyFS(Operations):
    """Expose image metadata as a read-only filesystem and fetch blobs on demand."""

    def __init__(self, metadata_path, blob_url, cache_dir, timeout=30):
        with open(metadata_path, "r", encoding="utf-8") as f:
            self.meta = json.load(f)

        self.files = self.meta["files"]
        self.dirs = set(self.meta.get("dirs", ["/"]))
        self.blob_url = blob_url.rstrip("/")
        self.cache_dir = os.path.abspath(cache_dir)
        self.timeout = timeout

        os.makedirs(self.cache_dir, exist_ok=True)
        self._fd = 0
        self.handles = {}
        self.stats = {
            "cache_hits": 0,
            "cache_misses": 0,
            "bytes_downloaded": 0,
        }

    @staticmethod
    def _norm(path):
        path = "/" + path.lstrip("/")
        return "/" if path == "//" else path.rstrip("/") or "/"

    def _entry(self, path):
        path = self._norm(path)
        if path in self.files:
            return self.files[path]
        if path in self.dirs:
            return {"type": "dir"}
        raise FuseOSError(errno.ENOENT)

    def _cache_path(self, digest):
        return os.path.join(self.cache_dir, digest)

    def _fetch(self, digest):
        """Return a verified local blob, fetching it atomically on a cache miss."""
        target = self._cache_path(digest)
        if os.path.isfile(target):
            self.stats["cache_hits"] += 1
            return target

        self.stats["cache_misses"] += 1
        fd, tmp = tempfile.mkstemp(prefix=f".{digest}.", dir=self.cache_dir)
        os.close(fd)

        try:
            with requests.get(
                f"{self.blob_url}/{digest}",
                stream=True,
                timeout=self.timeout,
            ) as response:
                if response.status_code != 200:
                    raise FuseOSError(errno.EIO)

                hasher = hashlib.sha256()
                total = 0

                with open(tmp, "wb") as out:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        out.write(chunk)
                        hasher.update(chunk)
                        total += len(chunk)

                    out.flush()
                    os.fsync(out.fileno())

                if hasher.hexdigest() != digest:
                    raise FuseOSError(errno.EIO)

                os.replace(tmp, target)
                self.stats["bytes_downloaded"] += total
                return target

        except requests.RequestException as exc:
            raise FuseOSError(errno.EIO) from exc
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def getattr(self, path, fh=None):
        path = self._norm(path)
        entry = self._entry(path)
        now = time.time()

        if entry.get("type") == "dir":
            return {
                "st_mode": stat.S_IFDIR | 0o755,
                "st_nlink": 2,
                "st_uid": os.getuid(),
                "st_gid": os.getgid(),
                "st_size": 0,
                "st_atime": now,
                "st_mtime": now,
                "st_ctime": now,
            }

        return {
            "st_mode": stat.S_IFREG | int(entry.get("mode", 0o444)),
            "st_nlink": 1,
            "st_uid": os.getuid(),
            "st_gid": os.getgid(),
            "st_size": int(entry["size"]),
            "st_atime": now,
            "st_mtime": float(entry.get("mtime", now)),
            "st_ctime": float(entry.get("mtime", now)),
        }

    def readdir(self, path, fh):
        path = self._norm(path)
        self._entry(path)
        prefix = "/" if path == "/" else path + "/"

        names = {".", ".."}
        for directory in self.dirs:
            if directory.startswith(prefix) and directory != path:
                rest = directory[len(prefix) :]
                if rest and "/" not in rest:
                    names.add(rest)

        for filename in self.files:
            if filename.startswith(prefix):
                rest = filename[len(prefix) :]
                if rest and "/" not in rest:
                    names.add(rest)

        yield from names

    def open(self, path, flags):
        entry = self._entry(path)
        if entry.get("type") == "dir":
            raise FuseOSError(errno.EISDIR)
        if flags & (os.O_WRONLY | os.O_RDWR):
            raise FuseOSError(errno.EROFS)

        self._fd += 1
        self.handles[self._fd] = self.files[self._norm(path)]
        return self._fd

    def read(self, path, size, offset, fh):
        entry = self.handles.get(fh) or self.files[self._norm(path)]
        cache_path = self._fetch(entry["sha256"])
        with open(cache_path, "rb") as f:
            f.seek(offset)
            return f.read(size)

    def release(self, path, fh):
        self.handles.pop(fh, None)
        return 0

    def access(self, path, mode):
        self._entry(path)
        if mode & os.W_OK:
            raise FuseOSError(errno.EROFS)

    def statfs(self, path):
        st = os.statvfs(self.cache_dir)
        return {
            "f_bsize": st.f_bsize,
            "f_frsize": st.f_frsize,
            "f_blocks": st.f_blocks,
            "f_bfree": st.f_bfree,
            "f_bavail": st.f_bavail,
            "f_files": st.f_files,
            "f_ffree": st.f_ffree,
            "f_favail": st.f_favail,
            "f_flag": st.f_flag,
            "f_namemax": st.f_namemax,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--blob-url", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument(
        "--allow-other",
        action="store_true",
        help="allow other users/processes (required when Docker daemon accesses the mount)",
    )
    parser.add_argument("mountpoint")
    args = parser.parse_args()

    fs = LazyFS(args.metadata, args.blob_url, args.cache)

    fuse_kwargs = {
        "foreground": True,
        "ro": True,
        "nothreads": False,
        "default_permissions": True,
    }
    if args.allow_other:
        fuse_kwargs["allow_other"] = True

    FUSE(fs, args.mountpoint, **fuse_kwargs)


if __name__ == "__main__":
    main()
