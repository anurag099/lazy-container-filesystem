# System Design

## Goal

Reduce startup-time data transfer and materialization by presenting a complete filesystem namespace while fetching file contents only when they are actually accessed.

## Components

1. **Image metadata** — maps POSIX paths to immutable SHA-256 blob IDs.
2. **Blob server** — serves immutable content by checksum.
3. **Lazy FUSE filesystem** — exposes the namespace and fetches missing blobs on demand.
4. **Local cache** — stores verified blobs on local storage.
5. **Container workload** — accesses only a small startup working set.

## Read path

```text
open/read(path)
      |
      v
metadata lookup
      |
      v
SHA-256 digest
      |
      v
cache hit? -------- yes --------> local read
      |
      no
      v
HTTP GET /blob/<digest>
      |
      v
stream + hash verification
      |
      v
atomic cache install
      |
      v
local read -> application
```

## Why this can reduce startup work

The filesystem can expose metadata for thousands of files immediately without downloading their contents. Only the subset required by the startup working set crosses the network and enters the local cache.

For the synthetic benchmark used during development, the logical image was 204.96 MiB, while the startup workload accessed only two tiny files totaling 74 bytes.

## Why not simply pre-download everything?

Eager materialization pays the cost for files that may never be read. That becomes expensive when images are large, startup working sets are small, and network/storage bandwidth is constrained.

## Trade-off

Lazy loading moves some work from image preparation to first access. If an application immediately reads almost the entire image, the advantage shrinks or disappears. A production system could combine lazy loading with workload-aware prefetching.

## Cache correctness

The expected SHA-256 digest comes from immutable metadata. A download is first written to a temporary file, hashed while streaming, flushed, and atomically renamed only after verification succeeds. This prevents readers from seeing partially downloaded content.

## Production evolution

A production design would move from the local demo server to an OCI-aware remote content source and integrate the filesystem as a container runtime snapshotter/rootfs provider. Additional components would likely include:

- cache eviction and size limits;
- request de-duplication for concurrent cache misses;
- connection pooling and retries;
- prefetching and read-ahead;
- metrics/tracing;
- authentication and TLS;
- multi-tenant cache isolation;
- OCI layer/index handling;
- crash recovery and persistent cache metadata.
