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
sudo .venv/bin/python shocker/main.py run -p 8000 python:latest -- python3 -m http.server 8000

# Access it
curl localhost:8000
```
