# Lazy Container Filesystem

A self-contained proof of concept for reducing container image startup cost by exposing a **read-only, content-addressed filesystem** that fetches file contents only when they are accessed.

The project explores a simple idea:

```text
Traditional:

container image
      ↓
download / materialize everything
      ↓
start useful work


Lazy filesystem:

metadata
      ↓
mount filesystem
      ↓
start useful work
      ↓
fetch only files actually accessed
```

The goal is to avoid spending startup time and bandwidth materializing large portions of an image that the workload never reads.

## Overview

A container image can contain a large number of files, while an application's startup path may require only a small subset of them.

This PoC separates filesystem **metadata** from file **content**.

The filesystem exposes normal paths such as:

```text
/app/startup.txt
/app/config.txt
/data/large-unused.bin
```

but the actual file contents are stored separately as SHA-256-addressed blobs.

When an application reads a file:

1. The filesystem resolves the path to its content digest.
2. It checks the local cache.
3. On a cache miss, it fetches the blob over HTTP.
4. It verifies the SHA-256 digest.
5. It atomically installs the blob into the local cache.
6. It returns the requested bytes to the application.

The filesystem is implemented in user space using FUSE.

## Architecture

```text
                         metadata.json
                    path → SHA-256 digest
                             │
                             ▼
                    ┌──────────────────┐
                    │   Lazy FUSE FS   │
                    └────────┬─────────┘
                             │
                    ┌────────┴────────┐
                    │                 │
                cache hit         cache miss
                    │                 │
                    ▼                 ▼
              local cache       HTTP Blob Server
                                      │
                                      ▼
                               SHA-256 verification
                                      │
                                      ▼
                              atomic cache install
                                      │
                                      ▼
                               return file data
```

For `/app/startup.txt`:

```text
/app/startup.txt
       │
       ▼
SHA-256 digest
       │
       ├── cache hit ──────► local blob
       │
       └── cache miss ─────► GET /blob/<digest>
                                  │
                                  ▼
                              verify hash
                                  │
                                  ▼
                              cache blob
                                  │
                                  ▼
                            return file data
```

## Why content addressing?

Each file is stored using its SHA-256 content digest rather than its filesystem path.

This provides two useful properties:

### Integrity

The filesystem knows the expected digest before downloading the content.

If the downloaded bytes do not match the expected SHA-256 digest, the data is rejected.

### Deduplication

Identical file contents can share the same blob and cache entry even when referenced by different paths.

A production implementation could store the same immutable blobs in object storage, a container registry, or behind a CDN.

## Implementation

The PoC uses:

* **Python** for rapid prototyping
* **FUSE / fusepy** for the user-space filesystem
* **SHA-256** for content addressing and integrity verification
* **HTTP** for the blob transport
* **Local filesystem cache** for repeated reads
* **Docker** for the application workload

The implementation is intentionally self-contained and does not depend on an existing lazy-image filesystem implementation.

## Repository structure

```text
lazy-container-fs/
├── filesystem/
│   └── lazy_fs.py          # Read-only FUSE filesystem
├── registry/
│   └── server.py           # Local HTTP blob server
├── scripts/
│   └── create_image.py     # Synthetic image generator
├── benchmark/
│   └── benchmark.py        # Eager vs lazy benchmark
├── container/
│   ├── Dockerfile
│   └── workload.py         # Container workload
├── docs/
│   ├── design.md
│   └── demo.md
├── .gitignore
├── Makefile
├── README.md
└── requirements.txt
```

## Requirements

The PoC targets Linux because FUSE and container filesystem integration are Linux-oriented primitives.

Tested with:

* Ubuntu 24.04
* ARM64 / AArch64
* Python 3.12
* Docker Engine
* FUSE 3
* `fusepy`

The demonstrated environment used an Ubuntu Linux machine running under OrbStack on an Apple Silicon Mac.

## Setup

Install the system dependencies:

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

### FUSE access for Docker

The FUSE filesystem can be mounted with `--allow-other` so Docker can access the mounted filesystem.

Enable the required FUSE setting:

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

This generates a synthetic content-addressed image containing:

* small application startup files;
* thousands of additional files;
* a 200 MiB file that the application never reads.

The generated image is intentionally excluded from Git using `.gitignore`.

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

The filesystem is mounted read-only.

### 4. Demonstrate lazy fetching

Terminal 3:

```bash
du -sh /tmp/lazy-cache
```

Initially the cache should be empty.

Read a startup file:

```bash
cat /tmp/lazy-root/app/startup.txt
```

Then inspect the cache:

```bash
find /tmp/lazy-cache -type f -printf '%f %s bytes\n'
```

Only the accessed blob should have been fetched and cached.

Reading the same file again is served from the local cache.

### 5. Run the container workload

Build the workload:

```bash
docker build -t lazy-fs-workload container/
```

Run it:

```bash
docker run --rm \
  --mount type=bind,src=/tmp/lazy-root,dst=/image,readonly \
  lazy-fs-workload
```

The container reads:

```text
/image/app/startup.txt
/image/app/config.txt
```

through the lazy filesystem.

## Benchmark

Run:

```bash
python3 benchmark/benchmark.py \
  --image demo-image \
  --lazy-root /tmp/lazy-root \
  --eager-root /tmp/eager-root \
  --cache /tmp/lazy-cache
```

The benchmark compares:

* eager materialization of the complete logical image;
* lazy access to the startup working set;
* warm-cache access after the required blobs have already been fetched.

### Observed result

Measured in the demonstrated environment:

```text
Logical image content: 204.96 MiB

EAGER materialization:
  0.8966s
  204.96 MiB materialized

LAZY cold startup working set:
  0.0736s
  0.000071 MiB requested
  0.000071 MiB downloaded/cached

LAZY warm-cache startup working set:
  0.0004s

Cache size after test:
  74 bytes
```

The lazy path fetched **74 bytes** for the startup working set instead of materializing the full **204.96 MiB** logical image.

That corresponds to approximately **99.99997% less data materialized** in this synthetic workload.

### Benchmark interpretation

The benchmark demonstrates the reduction in data that must be materialized before the startup working set is available.

It does **not** claim that the measured `0.8966s → 0.0736s` difference represents complete Docker container startup time.

The Docker container itself was also executed successfully against the mounted filesystem, but the current PoC does not replace Docker's OCI image pull/extraction or snapshotter path.

The blob server runs over local HTTP in this demonstration, so the benchmark focuses on filesystem behavior and data volume rather than real-world WAN latency or remote object-store performance.

## Design decisions

### Read-only filesystem

The PoC is intentionally read-only.

Container image content is treated as immutable, which removes write-back, consistency, and copy-on-write complexity from the initial implementation.

### FUSE

FUSE allows filesystem behavior to be implemented in user space while exposing normal filesystem paths to applications.

This provides a practical way to validate the lazy-read architecture without implementing a kernel filesystem.

### Content-addressed blobs

SHA-256 provides:

* immutable object identifiers;
* integrity verification;
* deduplication opportunities.

### Local cache

Fetched blobs are stored locally after successful verification.

A production cache would require additional policies such as:

* eviction;
* size limits;
* persistence;
* concurrent request coordination;
* cache warming or prefetching.

### HTTP transport

A tiny HTTP server keeps the PoC self-contained.

A production system could replace it with:

```text
OCI registry
object storage
CDN
authenticated blob service
```

without changing the fundamental filesystem abstraction.

### Python

Python was chosen for rapid prototyping and clarity.

A production implementation could move the data path to Rust or C if profiling showed that Python overhead was significant.

## Failure modes considered

### Missing blob

The filesystem returns an I/O error to the application.

### Network failure

A failed fetch returns an I/O error and does not expose partial data.

### Digest mismatch

Downloaded data is rejected when the SHA-256 digest does not match the metadata.

### Concurrent cache writers

Blob downloads use a temporary file followed by an atomic rename so readers do not observe partially written cache entries.

### Workload reads most of the image

Lazy loading is most beneficial when the startup working set is much smaller than the total image.

If an application eventually reads most of the image, the benefit decreases and may disappear. A production implementation could add prefetching based on workload characteristics.

## Limitations

This is a proof of concept rather than a production container filesystem.

It does not currently implement:

* full POSIX filesystem semantics;
* OCI image or layer parsing;
* direct containerd/runc snapshotter integration;
* distributed cache coordination;
* cache eviction or quotas;
* authentication or encryption;
* resumable downloads;
* write support;
* remote object-store integration.

The current Docker demo mounts the lazy filesystem inside the container at `/image`.

A production version would integrate the lazy root filesystem concept with the container runtime or image snapshotter so that the actual container root filesystem could be backed by on-demand remote content.

## Future work

A production-oriented design could extend the PoC with:

```text
                  OCI Registry / Object Store
                           │
                           ▼
                    Lazy Snapshotter
                           │
                    ┌──────┴──────┐
                    │ Local Cache  │
                    └──────┬──────┘
                           │
                           ▼
                     Container Rootfs
```

Potential improvements include:

* OCI manifest and layer support;
* direct containerd integration;
* parallel range/blob fetching;
* request coalescing for concurrent misses;
* LRU or size-aware cache eviction;
* prefetching;
* remote cache sharing;
* authentication and encrypted transport;
* metrics for cache hit rate, fetch latency, and startup working-set size.


## Summary

The key idea demonstrated by this PoC is simple:

```text
Eager:

image
  ↓
materialize everything
  ↓
start useful work


Lazy:

metadata
  ↓
mount filesystem
  ↓
start useful work
  ↓
fetch only accessed files
  ↓
cache locally
```

The benefit depends on the relationship between the complete image size and the application's actual startup working set.

This PoC makes that trade-off measurable while keeping the implementation small enough to reason about and extend.
