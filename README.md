# 🐳 Shocker 

My shockingly bad docker implementation in Python. Hackathon project to lean about what happens under the hood with docker!

## 🚀 Quick Start

```bash
# Install
uv sync

# Pull an image
sudo .venv/bin/python shocker/main.py pull python:latest

# list images
sudo .venv/bin/python shocker/main.py list

# Run a container with port forwarding
sudo .venv/bin/python shocker/main.py run --name web -p 8000 python:latest -- python3 -m http.server 8000

# run ubuntu container
sudo .venv/bin/python shocker/main.py run --name ubuntu ubuntu:latest -- /bin/sh

# Access it
curl localhost:8000
```

## 🌐 Container Networking

Shocker supports container-to-container networking! Containers can reach each other by hostname.

```bash
# Terminal 1: Start a web server container
sudo .venv/bin/python shocker/main.py run --name web -p 8000 python:latest -- python3 -m http.server 8000

# Terminal 2: Start a client container and access the web server
sudo .venv/bin/python shocker/main.py run --name client busybox -- /bin/sh
container# wget -O- web:8000
# Success! The client can reach the web container by hostname
```

Each container gets:
- A unique IP address on the `shocker0` bridge (69.69.0.x)
- Automatic DNS resolution for named containers
- Network isolation via Linux network namespaces
