# Demo / Submission Flow

This is the reproducible flow used for the interview proof of concept.

## 1. Generate the image

```bash
python3 scripts/create_image.py --output demo-image --files 5000 --large-mb 200
```

Capture the printed logical size.

## 2. Start the blob server

Terminal 1:

```bash
python3 registry/server.py --root demo-image/blobs --port 9000
```

Keep it running so the FUSE filesystem can fetch blobs over HTTP.

## 3. Start the lazy filesystem

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

Keep this process in the foreground.

## 4. Prove a cold cache miss

Terminal 3:

```bash
du -sh /tmp/lazy-cache
cat /tmp/lazy-root/app/startup.txt
du -sh /tmp/lazy-cache
```

The cache grows only after the file is read.

## 5. Prove cache reuse

```bash
cat /tmp/lazy-root/app/startup.txt
```

Read the same file again. It should be served from the local cache.

## 6. Build the workload image

```bash
docker build -t lazy-fs-workload container/
```

## 7. Run the workload through the lazy filesystem

```bash
docker run --rm \
  --mount type=bind,src=/tmp/lazy-root,dst=/image,readonly \
  lazy-fs-workload
```

Expected output contains:

```text
hello from the application startup path
configuration required at startup
APPLICATION_FIRST_READ_SECONDS=...
```

## 8. Run the benchmark

```bash
python3 benchmark/benchmark.py \
  --image demo-image \
  --blob-url http://127.0.0.1:9000/blob \
  --lazy-root /tmp/lazy-root \
  --eager-root /tmp/eager-root \
  --cache /tmp/lazy-cache
```

The benchmark compares full eager materialization with lazy access to the startup working set and also reports warm-cache performance.

## 9. What to show in the video

The clearest demo sequence is:

```text
1. Show the generated 204+ MiB image content.
2. Show an empty /tmp/lazy-cache.
3. Read startup.txt.
4. Show the tiny cache entry that appeared.
5. Build/run the Docker workload.
6. Run the benchmark and show the real numbers.
7. Explain that unused image content was never fetched.
8. Explain the production next step: containerd/runc/OCI snapshotter integration.
```

## Measurement wording

Use precise language:

> "In this synthetic benchmark, eager materialization copied the complete 204.96 MiB logical image, while the lazy startup working set fetched only 74 bytes. The Docker demo uses the lazy filesystem as a mounted application filesystem; it does not replace Docker's complete OCI image pull/extraction path."
