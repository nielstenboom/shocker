import tempfile
import tarfile
import shutil
import os
import subprocess
from pathlib import Path
from typing import List, Dict, Optional
from pyroute2 import IPRoute, NetNS, netns
from pyroute2.netlink.exceptions import NetlinkError
from shocker.docker_registry import ARTIFACTS_DIR

def setup_network_namespace(netns_name: str) -> str:
    """
    Create and configure a network namespace.
    Returns the namespace name.
    """
    try:
        # Create network namespace
        netns.create(netns_name)
        print(f"🌐 Created network namespace: {netns_name}")
        
        # Configure container end inside namespace
        with NetNS(netns_name) as ns:
            # Set up loopback
            lo_idx = ns.link_lookup(ifname='lo')[0]
            ns.link('set', index=lo_idx, state='up')
        
        print(f"🔗 Network namespace configured with loopback only")
        return netns_name
        
    except Exception as e:
        print(f"❌ Failed to setup network namespace: {e}")
        # Cleanup on failure
        cleanup_network_namespace(netns_name)
        raise

def cleanup_network_namespace(netns_name: str):
    """Clean up network namespace and associated resources."""
    try:
        # Remove namespace
        try:
            netns.remove(netns_name)
            print(f"🧹 Cleaned up network namespace: {netns_name}")
        except:
            pass
            
    except Exception as e:
        print(f"⚠️  Error during network cleanup: {e}")

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
    

    chroot_cmd = ["ip", "netns", "exec", netns_name, "chroot", str(root_path)] + command
    
    print(f"🔧 Executing: {' '.join(chroot_cmd)}")
    
    result = subprocess.run(chroot_cmd, env=env, check=True)

    return result


def run_container(repository: str, tag: str, command: List[str], tty: bool = False):
    """Run a container from pulled image layers."""

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
    
    netns_name = f"shocker-{temp_dir.name}"
    setup_network_namespace(netns_name)
    
    try:
        # Extract all layers in order
        for i, layer_file in enumerate(layer_files, 1):
            print(f"[{i}/{len(layer_files)}] Extracting {layer_file.name}...")

            with tarfile.open(layer_file, 'r:gz') as tar:
                # Extract to rootfs, allowing overwrites (later layers override earlier ones)
                tar.extractall(path=rootfs_path)
        
        print(f"✅ Container filesystem ready at: {rootfs_path}")
        print(f"📁 Contents: {list(rootfs_path.iterdir())[:10]}{'...' if len(list(rootfs_path.iterdir())) > 10 else ''}")
        
        # Setup DNS for internet access
        setup_dns(rootfs_path)
        
        # Execute the command in chroot
        print(f"\n🚀 Running command: {' '.join(command)}")
        print(f"🌐 Running in isolated network namespace: {netns_name}")

        result = chroot_execute(
            root_path=rootfs_path,
            command=command,
            netns_name=netns_name
        )
        
        if result and hasattr(result, 'returncode'):
            print(f"\n✅ Command completed with exit code: {result.returncode}")
            
    finally:

        cleanup_network_namespace(netns_name)
        
        # Clean up temporary directory
        print(f"\n🧹 Cleaning up temporary directory: {temp_dir}")
        shutil.rmtree(temp_dir)

def setup_dns(root_path):
    """Copy host DNS config to container"""
    host_resolv = Path("/etc/resolv.conf")
    container_resolv = root_path / "etc" / "resolv.conf"
    
    # Ensure etc directory exists
    container_resolv.parent.mkdir(parents=True, exist_ok=True)
    
    # Copy DNS configuration
    if host_resolv.exists():
        shutil.copy2(host_resolv, container_resolv)
        print(f"📡 Copied DNS configuration to container")
