#!/usr/bin/env python3
import argparse, hashlib, json, os, random, shutil, string
from pathlib import Path


def write_blob(blobs, data):
    digest = hashlib.sha256(data).hexdigest()
    path = blobs / digest
    if not path.exists():
        path.write_bytes(data)
    return digest


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', default='demo-image')
    p.add_argument('--files', type=int, default=5000)
    p.add_argument('--large-mb', type=int, default=200)
    args = p.parse_args()
    root = Path(args.output)
    if root.exists(): shutil.rmtree(root)
    blobs = root / 'blobs'; blobs.mkdir(parents=True)
    files = {}
    dirs = {'/'}

    startup = {
        '/app/startup.txt': b'hello from the application startup path\n',
        '/app/config.txt': b'configuration required at startup\n',
    }
    for path, data in startup.items():
        files[path] = {'sha256': write_blob(blobs, data), 'size': len(data), 'mode': 0o444}
        dirs.add('/app')

    # A large blob that is intentionally NOT touched by the startup workload.
    large = os.urandom(args.large_mb * 1024 * 1024)
    path = '/data/large-unused.bin'
    files[path] = {'sha256': write_blob(blobs, large), 'size': len(large), 'mode': 0o444}
    dirs.add('/data')

    rng = random.Random(42)
    for i in range(args.files):
        name = ''.join(rng.choice(string.ascii_lowercase) for _ in range(32))
        path = f'/unused/{i:05d}-{name}.txt'
        data = (f'unused file {i}\n' + 'x' * 1024).encode()
        files[path] = {'sha256': write_blob(blobs, data), 'size': len(data), 'mode': 0o444}
    dirs.add('/unused')

    metadata = {'version': 1, 'files': files, 'dirs': sorted(dirs)}
    (root / 'metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(f'created {root}: {sum(x["size"] for x in files.values()) / 1024 / 1024:.2f} MiB logical content')
    print(f'files: {len(files)}, blobs: {len(list(blobs.iterdir()))}')


if __name__ == '__main__': main()
