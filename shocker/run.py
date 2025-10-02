import tempfile
import tarfile
import shutil
import os
import subprocess
from pathlib import Path
from typing import List, Dict, Optional

from shocker.docker_registry import ARTIFACTS_DIR
from shocker.networking import BRIDGE_NAME, cleanup_network_namespace, setup_dns, setup_network_namespace, setup_port_forwarding, cleanup_port_forwarding
from shocker.container_registry import ContainerRegistry

def chroot_execute(root_path: Path, command: List[str], 
                   env_vars: Dict[str, str] | None = None, 
                   netns_name: str | None = None) -> subprocess.CompletedProcess | None:
    """
    Execute a command in a chroot environment using system chroot command.
    
    Args:
        root_path: Path to the new root directory
        command: Command to execute as list
        env_vars: Environment variables dict (optional)
        interactive: Whether to run interactively
        netns_name: Network namespace to run in (optional)
    """
    # Build environment
    env = os.environ.copy()
    if env_vars:
        env.update(env_vars)
    
    # Default container environment with simple prompt
    env.update({
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/root",
        "PS1": "container# ",  # Simple prompt
        "SHELL": "/bin/sh",
        "TERM": "xterm"
    })
    

    chroot_cmd = ["ip", "netns", "exec", netns_name, "chroot", str(root_path)] + list(command)
    
    print(f"🔧 Executing: {' '.join(chroot_cmd)}")
    
    result = subprocess.run(chroot_cmd, env=env, check=True)

    return result


def run_container(repository: str, tag: str, command: List[str], 
                  tty: bool = False, port_mappings: List[tuple[int, int]] = None,
                  container_name: str = None):
    """Run a container from pulled image layers."""
    if port_mappings is None:
        port_mappings = []
    
    # Check if running as root
    if os.geteuid() != 0:
        raise PermissionError("This command requires root privileges. Please run with sudo.")
    
    # Find the image directory
    image_dir = ARTIFACTS_DIR / f"library_{repository}_{tag}"
    if not image_dir.exists():
        raise FileNotFoundError(f"Image {repository}:{tag} not found. Run 'python main.py pull {repository} --tag {tag}' first.")
    
    # Find all layer files
    layer_files = sorted([f for f in image_dir.glob("layer_*.tar.gz")])
    if not layer_files:
        raise FileNotFoundError(f"No layer files found in {image_dir}")
    
    print(f"Found {len(layer_files)} layers to extract...")
    
    # Create temporary directory for the container filesystem
    temp_dir = Path(tempfile.mkdtemp(prefix="shocker_", dir="/tmp"))
    rootfs_path = temp_dir
    
    print(f"Created temporary container filesystem at: {rootfs_path}")
    
    # Allocate IP from registry and setup network
    container_ip = ContainerRegistry.allocate_ip()
    netns_name = f"shocker-{temp_dir.name}"
    setup_network_namespace(netns_name, container_ip)
    
    # Register container if named
    if container_name:
        ContainerRegistry.register(container_name, container_ip, netns_name)
        print(f"📝 Registered container '{container_name}' with IP {container_ip}")
    
    if port_mappings:
        setup_port_forwarding(port_mappings, container_ip)
    
    try:
        # Extract all layers in order
        for i, layer_file in enumerate(layer_files, 1):
            print(f"[{i}/{len(layer_files)}] Extracting {layer_file.name}...")

            with tarfile.open(layer_file, 'r:gz') as tar:
                tar.extractall(path=rootfs_path)
        
        print(f"✅ Container filesystem ready at: {rootfs_path}")
        
        # Setup DNS and hosts file (includes all registered containers)
        setup_dns(rootfs_path, container_name)
        
        # Execute the command in chroot
        print(f"\n🚀 Running command: {' '.join(command)}")
        print(f"🌐 Container IP: {container_ip} (connected to bridge {BRIDGE_NAME})")

        result = chroot_execute(
            root_path=rootfs_path,
            command=command,
            netns_name=netns_name
        )
        
        if result and hasattr(result, 'returncode'):
            print(f"\n✅ Command completed with exit code: {result.returncode}")
            
    finally:
        # Cleanup port forwarding
        if port_mappings:
            cleanup_port_forwarding(port_mappings, container_ip)
        
        # Unregister container if named
        if container_name:
            ContainerRegistry.unregister(container_name)
            print(f"🗑️  Unregistered container '{container_name}'")
        
        cleanup_network_namespace(netns_name)
        
        # Clean up temporary directory
        print(f"\n🧹 Cleaning up temporary directory: {temp_dir}")
        shutil.rmtree(temp_dir)
