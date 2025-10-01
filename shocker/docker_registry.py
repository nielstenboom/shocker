#!/usr/bin/env python3
"""
Docker Registry API client for downloading image layers.
"""

from pathlib import Path
from typing import Dict, Any, Optional
import requests

ARTIFACTS_DIR = Path(__file__).parent.parent / "docker_artifacts"

class DockerRegistryClient:
    """Client for interacting with Docker Registry API v2.
    
    https://docs.docker.com/reference/api/registry/latest/#tag/pull
    """

    def __init__(self, repository: str, tag: str = "latest"):
        self.registry_url = "https://registry-1.docker.io"
        self.repository = f"library/{repository}"
        self.tag = tag
        self.token = self._get_bearer_token()

        if not ARTIFACTS_DIR.exists():
            ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    def _get_bearer_token(self) -> str:
        """Get a bearer token for the specified repository and return it."""
        params = {
            "service": "registry.docker.io",
            "scope": f"repository:{self.repository}:pull"
        }
        url = "https://auth.docker.io/token"
        response = requests.get(url, params=params)
        response.raise_for_status()
        
        return response.json()["token"]

    def _get_manifest_list(self) -> Dict[str, Any]:
        """Get the image manifest list."""
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.docker.distribution.manifest.list.v2+json"
        }
        
        url = f"{self.registry_url}/v2/{self.repository}/manifests/{self.tag}"
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        return response.json()
    
    def _get_platform_manifest_digest(self, manifest_list: Dict[str, Any], 
                                   architecture: str = "amd64", 
                                   os: str = "linux") -> str:
        """Extract the digest for the specified platform from manifest list."""
        for manifest in manifest_list.get("manifests", []):
            platform = manifest.get("platform", {})
            if (platform.get("architecture") == architecture and 
                platform.get("os") == os):
                return manifest["digest"]
        
        raise ValueError(f"Platform {os}/{architecture} not found in manifest list")

    def _get_image_manifest(self, digest: str) -> Dict[str, Any]:
        """Get the platform-specific image manifest."""
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.docker.distribution.manifest.v2+json"
        }

        url = f"{self.registry_url}/v2/{self.repository}/manifests/{digest}"
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        return response.json()

    def _check_blob_exists(self, digest: str) -> bool:
        """Check if a blob exists using HEAD request."""
        headers = {"Authorization": f"Bearer {self.token}"}

        url = f"{self.registry_url}/v2/{self.repository}/blobs/{digest}"
        response = requests.head(url, headers=headers)
        
        return response.status_code <= 400

    def _download_blob(self, digest: str, output_path: Optional[Path] = None) -> bytes:
        """Download a layer blob."""
        headers = {"Authorization": f"Bearer {self.token}"}

        url = f"{self.registry_url}/v2/{self.repository}/blobs/{digest}"
        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()
        
        content = b""
        for chunk in response.iter_content(chunk_size=32768):
            content += chunk
        
        if output_path:
            output_path.write_bytes(content)
        
        return content

    def pull(self, output_dir: Path | None = None, download_layers: bool = True, 
             architecture: str = "amd64", os_type: str = "linux"):
        """
        Pull a complete Docker image by downloading manifest and optionally all layers.
        
        Args:
            output_dir: Directory to save artifacts (defaults to ./docker_artifacts)
            architecture: Target architecture (default: amd64)
            os_type: Target OS (default: linux)
        """
        if output_dir is None:
            output_dir = ARTIFACTS_DIR / f"{self.repository.replace('/', '_')}_{self.tag}"
        
        output_dir.mkdir(exist_ok=True)
    
        
        # Step 1: Get manifest list
        print("Getting manifest list...")
        manifest_list = self._get_manifest_list()
        
        # Step 2: Get platform-specific digest
        print(f"Finding {os_type}/{architecture} manifest...")
        image_digest = self._get_platform_manifest_digest(manifest_list, architecture, os_type)
        
        # Step 3: Get platform-specific manifest
        print("Getting platform-specific manifest...")
        manifest = self._get_image_manifest(image_digest)
        
        # Step 4: Process layers
        if not manifest.get("layers"):
            raise ValueError("No layers found in manifest")

        if download_layers:
            print(f"Downloading {len(manifest['layers'])} layers...")
            for i, layer in enumerate(manifest["layers"], 1):
                layer_digest = layer["digest"]
                
                # Check if layer exists
                print(f"[{i}/{len(manifest['layers'])}] Checking layer {layer_digest}...")
                if not self._check_blob_exists(layer_digest):
                    print(f"Warning: Layer {layer_digest} not found, skipping...")
                    continue
                
                # Download layer
                print(f"[{i}/{len(manifest['layers'])}] Downloading layer {layer_digest}...")
                layer_filename = f"layer_{i:03d}_{layer_digest.replace(':', '_')}.tar.gz"
                layer_path = output_dir / layer_filename
                
                if layer_path.exists():
                    print(f"Layer already exists at {layer_path}, skipping download.")
                    continue

                self._download_blob(layer_digest, layer_path)
                print(f"Layer saved to {layer_path}")
        
        print(f"\nPull completed!")
        print(f"Repository: {self.repository}:{self.tag}")
        print(f"Platform: {os_type}/{architecture}")
        print(f"Output directory: {output_dir}")
        print(f"Layers: {len(manifest['layers'])} total")

    @staticmethod
    def list() -> list:
        """List all pulled images."""
        if not ARTIFACTS_DIR.exists():
            return []
        
        images = []
        for item in ARTIFACTS_DIR.iterdir():
            if item.is_dir():
                # Extract repo and tag from folder name
                folder_name = item.name
                if "_" in folder_name:
                    parts = folder_name.rsplit("_", 1)
                    repo = parts[0].replace("_", "/")
                    tag = parts[1]
                else:
                    repo = folder_name
                    tag = "unknown"
                
                # Calculate total size of all files in the directory
                total_size = 0
                for file_path in item.rglob("*"):
                    if file_path.is_file():
                        total_size += file_path.stat().st_size
                
                # Convert to MB
                size_mb = total_size / (1024 * 1024)
                
                images.append({
                    "repository": repo,
                    "tag": tag,
                    "path": item,
                    "size_mb": round(size_mb, 2)
                })

        return images
