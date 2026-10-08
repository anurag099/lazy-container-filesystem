import time
from pathlib import Path

start = time.perf_counter()
startup = Path('/image/app/startup.txt').read_text()
config = Path('/image/app/config.txt').read_text()
elapsed = time.perf_counter() - start
print(startup.strip())
print(config.strip())
print(f'APPLICATION_FIRST_READ_SECONDS={elapsed:.6f}')
