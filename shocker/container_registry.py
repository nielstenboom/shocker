from pathlib import Path
import json
from typing import Dict, Optional

CONTAINERS_FILE = Path("/var/run/shocker/containers.json")

class ContainerRegistry:
    
    @staticmethod
    def _load_containers() -> Dict[str, Dict[str, str]]:
        """Load the containers from registry file."""
        if not CONTAINERS_FILE.exists():
            return {}
        data = json.loads(CONTAINERS_FILE.read_text())
        # Support both old format (just dict) and new format (with 'containers' key)
        if isinstance(data, dict) and 'containers' in data:
            return data['containers']
        return data
    
    @staticmethod
    def _save_containers(containers: Dict[str, Dict[str, str]]):
        """Save the containers to registry file."""
        CONTAINERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONTAINERS_FILE.write_text(json.dumps(containers, indent=2))
    
    @staticmethod
    def allocate_ip() -> str:
        """
        Allocate the next available IP address by finding the highest
        currently in use and incrementing.
        """
        containers = ContainerRegistry._load_containers()
        
        if not containers:
            return "69.69.0.2"  # First container
        
        # Extract all IPs and find the highest last octet
        used_ips = []
        for info in containers.values():
            ip = info['ip']
            last_octet = int(ip.split('.')[-1])
            used_ips.append(last_octet)
        
        next_ip = max(used_ips) + 1
        return f"69.69.0.{next_ip}"
    
    @staticmethod
    def register(container_name: str, ip: str, netns: str):
        """Register a running container."""
        containers = ContainerRegistry._load_containers()
        
        # Check if container name already exists
        if container_name in containers:
            raise ValueError(f"Container name '{container_name}' already exists. Choose a different name.")
        
        containers[container_name] = {
            "ip": ip,
            "netns": netns
        }
        ContainerRegistry._save_containers(containers)
    
    @staticmethod
    def unregister(container_name: str):
        """Unregister a container."""
        containers = ContainerRegistry._load_containers()
        containers.pop(container_name, None)
        ContainerRegistry._save_containers(containers)
    
    @staticmethod
    def list_all() -> Dict[str, Dict[str, str]]:
        """List all registered containers."""
        return ContainerRegistry._load_containers()
    
    @staticmethod
    def get_ip(container_name: str) -> Optional[str]:
        """Get IP address of a container by name."""
        containers = ContainerRegistry.list_all()
        return containers.get(container_name, {}).get("ip")
    
    @staticmethod
    def get_hosts_entries() -> str:
        """Get /etc/hosts entries for all containers."""
        containers = ContainerRegistry.list_all()
        lines = []
        for name, info in containers.items():
            lines.append(f"{info['ip']}\t{name}")
        return "\n".join(lines)