.PHONY: setup image server mount benchmark docker-build docker-run

setup:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -r requirements.txt

image:
	python3 scripts/create_image.py --output demo-image --files 5000 --large-mb 200

server:
	python3 registry/server.py --root demo-image/blobs --port 9000

mount:
	mkdir -p /tmp/lazy-root /tmp/lazy-cache
	python3 filesystem/lazy_fs.py --metadata demo-image/metadata.json --blob-url http://127.0.0.1:9000/blob --cache /tmp/lazy-cache --allow-other /tmp/lazy-root

benchmark:
	python3 benchmark/benchmark.py --image demo-image --blob-url http://127.0.0.1:9000/blob --lazy-root /tmp/lazy-root --eager-root /tmp/eager-root --cache /tmp/lazy-cache

docker-build:
	docker build -t lazy-fs-workload container/

docker-run:
	docker run --rm --mount type=bind,src=/tmp/lazy-root,dst=/image,readonly lazy-fs-workload
