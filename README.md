# Lazy Container Filesystem — Proof of Concept

A small proof-of-concept demonstrating how a **read-only lazy filesystem** can reduce the amount of container image data that must be materialized before an application starts.

> **Assignment constraint:** This implementation does not use eStargz, Nydus, Dragonfly, or another existing lazy-image filesystem implementation.

## Assignment goal

The interview assignment asks for a filesystem that can help a container start faster by avoiding eager materialization of image content that the application never reads. A proof of concept is sufficient; a production container runtime or full OCI implementation is not required.

## What this PoC implements

The filesystem separates **metadata** from **file contents**:

```text
                       metadata.json
                  path -> SHA-256 digest
                           |
                           v
Container/application -> Lazy FUSE filesystem
                           |
                    +------+------+
                    |             |
                cache hit      cache miss
                    |             |
                    v             v
               local SSD     HTTP blob server
                                  |
                                  v
                            SHA-256 verify
                                  |
                                  v
                         atomic cache install
```

For a file such as `/app/startup.txt`:

```text
/app/startup.txt
       |
       v
SHA-256 digest
       |
       +---- cache hit ----> local blob
       |
       +---- cache miss ---> GET /blob/<digest>
                                  |
                                  v
                              verify hash
                                  |
                                  v
                              cache blob
                                  |
                                  v
                            return file data
```

## Why content addressing?

Each file is stored by SHA-256 content digest rather than path. This gives us two useful properties:

1. **Integrity:** the downloaded content must hash to the expected digest.
2. **Deduplication:** identical file contents can share one blob and one cache entry.

A production implementation could place the same immutable blobs in an OCI registry or object store and share the local cache across workloads.

## Repository layout

```text
filesystem/   Read-only FUSE filesystem and lazy fetch/cache logic
registry/     Tiny local HTTP blob server
scripts/      Synthetic image/manifest generator
benchmark/    Eager vs lazy working-set benchmark
container/    Docker workload used by the demo
docs/         Design and reproducible demo instructions
```

## Linux setup

Linux is recommended because FUSE and container filesystem integration are Linux primitives. The demonstrated environment was Ubuntu 24.04 on an ARM64 Linux machine running under OrbStack on an Apple Silicon Mac.

Install:

```bash
sudo apt update
sudo apt install -y \
  fuse3 \
  libfuse3-dev \
  libfuse2t64 \
  python3 \
  python3-pip \
  python3-venv \
  docker.io
```

Create the Python environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If Docker needs to access a FUSE mount created by the current user, enable `allow_other`:

```bash
sudo sh -c 'grep -qxF "user_allow_other" /etc/fuse.conf || echo "user_allow_other" >> /etc/fuse.conf'
```

## Quick demo

### 1. Generate a synthetic image

```bash
python3 scripts/create_image.py \
  --output demo-image \
  --files 5000 \
  --large-mb 200
```

The generated image contains a small startup working set plus a 200 MiB file that the workload deliberately never reads.

### 2. Start the blob server

Terminal 1:

```bash
python3 registry/server.py \
  --root demo-image/blobs \
  --port 9000
```

### 3. Mount the lazy filesystem

Terminal 2:

```bash
mkdir -p /tmp/lazy-root /tmp/lazy-cache

python3 filesystem/lazy_fs.py \
  --metadata demo-image/metadata.json \
  --blob-url http://127.0.0.1:9000/blob \
  --cache /tmp/lazy-cache \
  --allow-other \
  /tmp/lazy-root
```

### 4. Prove lazy fetching

Terminal 3:

```bash
du -sh /tmp/lazy-cache
# expected: 0

cat /tmp/lazy-root/app/startup.txt

du -sh /tmp/lazy-cache
# expected: a small cache entry now exists
```

Reading the same file again uses the local cache.

### 5. Build and run the container workload

```bash
docker build -t lazy-fs-workload container/
```

```bash
docker run --rm \
  --mount type=bind,src=/tmp/lazy-root,dst=/image,readonly \
  lazy-fs-workload
```

The container reads `/image/app/startup.txt` and `/image/app/config.txt` through the lazy filesystem.

## Benchmark

With the same synthetic image used in the demo:

```bash
python3 benchmark/benchmark.py \
  --image demo-image \
  --blob-url http://127.0.0.1:9000/blob \
  --lazy-root /tmp/lazy-root \
  --eager-root /tmp/eager-root \
  --cache /tmp/lazy-cache
```

### Observed result from the demo environment

```text
Logical image content: 204.96 MiB

EAGER materialization: 0.8966s
EAGER bytes materialized: 204.96 MiB

LAZY cold startup working set: 0.0736s
LAZY requested bytes: 0.000071 MiB
LAZY bytes downloaded/cached: 0.000071 MiB

LAZY warm-cache startup working set: 0.0004s
Cache size after test: 74 bytes
```

The lazy filesystem fetched **74 bytes** for the startup working set instead of materializing the full **204.96 MiB** synthetic image — about **99.99997% less data** in this benchmark.

The benchmark's 0.8966s vs 0.0736s numbers measure **filesystem preparation/working-set access**, not complete Docker container startup. The Docker command was also measured separately during the demo, but because eager materialization was performed beforehand, those `docker run` timings are not a fair end-to-end image-startup comparison.

## Design decisions and trade-offs

### Read-only

The PoC is intentionally read-only. Container image content is treated as immutable, which removes write-back and consistency complexity from the first implementation.

### FUSE

FUSE lets us implement filesystem behavior in user space while presenting normal POSIX-style paths to applications. This makes it practical to prove the lazy-read mechanism without writing a kernel filesystem.

### Content-addressed blobs

SHA-256 provides immutable object IDs and integrity verification. It also enables deduplication when different paths or images reference identical content.

### Local cache

Once a blob is fetched and verified, subsequent reads avoid another network request. A production cache would need an eviction policy, quotas, persistence rules, and concurrency coordination.

### HTTP transport

A tiny HTTP server keeps the PoC self-contained. In production, the blob source could be backed by an OCI registry, object storage, or a CDN layer.

### Python

Python was chosen for rapid prototyping. A production filesystem datapath could move the hot path to Rust or C if profiling showed Python overhead to be material.

## Failure modes considered

- Missing blob -> I/O error to the application.
- Network request failure -> I/O error; partial data is not installed as the final cache entry.
- Digest mismatch -> I/O error; corrupted data is discarded.
- Concurrent cache writers -> atomic temporary-file + rename avoids exposing partial blobs.
- Workload reads most of the image -> lazy loading may provide little or no benefit; prefetching could be added.

## Limitations

This is deliberately a PoC, not a production container filesystem. It does not implement:

- full POSIX filesystem semantics;
- OCI image/layer parsing;
- direct containerd/runc snapshotter integration;
- distributed cache coordination;
- cache eviction/quotas;
- authentication or encryption;
- resumable downloads;
- write support.

The current Docker demo mounts the lazy filesystem into the container at `/image`. A production version would integrate the same lazy-rootfs concept at the container runtime/image snapshotter layer so the container's actual root filesystem could be backed by on-demand remote content.

## What the PoC demonstrates

The central result is architectural:

```text
Traditional approach:

image -> download/materialize complete filesystem -> start useful work

Lazy approach:

metadata -> mount filesystem -> start process -> fetch only accessed files
```

The benefit depends on the size of the startup working set relative to the complete image and on network latency/cache locality. The PoC intentionally makes that trade-off measurable instead of assuming every workload becomes faster.
