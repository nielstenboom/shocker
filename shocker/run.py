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

# Simple bridge configuration
BRIDGE_NAME = "shocker0"
BRIDGE_IP = "69.69.0.1"
CONTAINER_IP = "69.69.0.2"

def ensure_bridge_exists():
    """Create and configure the shocker bridge if it doesn't exist."""
    try:
        with IPRoute() as ipr:
            # Check if bridge already exists
            try:
                bridge_idx = ipr.link_lookup(ifname=BRIDGE_NAME)[0]
                print(f"🌉 Bridge {BRIDGE_NAME} already exists")
                return
            except IndexError:
                pass  # Bridge doesn't exist, create it
            
            # Create bridge
            ipr.link('add', ifname=BRIDGE_NAME, kind='bridge')
            bridge_idx = ipr.link_lookup(ifname=BRIDGE_NAME)[0]
            
            # Configure bridge IP
            ipr.addr('add', index=bridge_idx, address=BRIDGE_IP, prefixlen=24)
            ipr.link('set', index=bridge_idx, state='up')
            
            print(f"🌉 Created bridge {BRIDGE_NAME} at {BRIDGE_IP}/24")
            
    except Exception as e:
        print(f"❌ Failed to create bridge: {e}")
        raise

def setup_network_namespace(netns_name: str) -> str:
    """
    Create and configure a network namespace with bridge connection.
    Returns the namespace name.
    """
    try:
        # Ensure bridge exists
        ensure_bridge_exists()
        
        # Create network namespace
        netns.create(netns_name)
        print(f"🌐 Created network namespace: {netns_name}")
        
        # Create veth pair
        veth_host = "veth-host"
        veth_container = "eth0"  # Standard container interface name
        
        with IPRoute() as ipr:
            # Create veth pair
            ipr.link('add', ifname=veth_host, peer=veth_container, kind='veth')
            
            # Move container end to namespace
            container_idx = ipr.link_lookup(ifname=veth_container)[0]
            ipr.link('set', index=container_idx, net_ns_fd=netns_name)
            
            # Connect host end to bridge (instead of direct IP assignment)
            bridge_idx = ipr.link_lookup(ifname=BRIDGE_NAME)[0]
            veth_host_idx = ipr.link_lookup(ifname=veth_host)[0]
            ipr.link('set', index=veth_host_idx, master=bridge_idx, state='up')
        
        # Configure container end inside namespace
        with NetNS(netns_name) as ns:
            container_idx = ns.link_lookup(ifname=veth_container)[0]
            ns.addr('add', index=container_idx, address=CONTAINER_IP, prefixlen=24)
            ns.link('set', index=container_idx, state='up')
            
            # Set up loopback
            lo_idx = ns.link_lookup(ifname='lo')[0]
            ns.link('set', index=lo_idx, state='up')
            
            # Add default route via bridge
            ns.route('add', dst='default', gateway=BRIDGE_IP)
        
        print(f"🔗 Container connected to bridge: {CONTAINER_IP} -> {BRIDGE_NAME}")
        return netns_name
        
    except Exception as e:
        print(f"❌ Failed to setup network namespace: {e}")
        cleanup_network_namespace(netns_name)
        raise

def cleanup_network_namespace(netns_name: str):
    """Clean up network namespace and associated resources."""
    try:
        # Remove veth interface (removes both ends and disconnects from bridge)
        try:
            with IPRoute() as ipr:
                host_idx = ipr.link_lookup(ifname="veth-host")
                if host_idx:
                    ipr.link('del', index=host_idx[0])
        except:
            pass
        
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
    

    chroot_cmd = ["ip", "netns", "exec", netns_name, "chroot", str(root_path)] + list(command)
    
    print(f"🔧 Executing: {' '.join(chroot_cmd)}")
    
    result = subprocess.run(chroot_cmd, env=env, check=True)

    return result


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

def setup_port_forwarding(port_mappings: List[tuple[int, int]]):
    """Set up port forwarding using minimal iptables."""
    
    # Enable IP forwarding
    subprocess.run(['sysctl', '-w', 'net.ipv4.ip_forward=1'], check=True)
    
    for host_port, container_port in port_mappings:
        try:
            # Rule for external traffic (PREROUTING)
            subprocess.run([
                'iptables', '-t', 'nat', '-A', 'PREROUTING',
                '-p', 'tcp', '--dport', str(host_port),
                '-j', 'DNAT', '--to-destination', f'{CONTAINER_IP}:{container_port}'
            ], check=True)
            
            # Rule for local traffic (OUTPUT)
            subprocess.run([
                'iptables', '-t', 'nat', '-A', 'OUTPUT',
                '-p', 'tcp', '--dport', str(host_port),
                '-d', '127.0.0.1',
                '-j', 'DNAT', '--to-destination', f'{CONTAINER_IP}:{container_port}'
            ], check=True)
            
            # MASQUERADE for local traffic return path - THIS IS THE FIX
            subprocess.run([
                'iptables', '-t', 'nat', '-A', 'POSTROUTING',
                '-d', CONTAINER_IP,
                '-p', 'tcp', '--dport', str(container_port),
                '-j', 'MASQUERADE'
            ], check=True)
            
            # ADD THESE FORWARD RULES (like Docker does)
            subprocess.run([
                'iptables', '-I', 'FORWARD', '1',
                '-d', CONTAINER_IP,
                '-p', 'tcp', '--dport', str(container_port),
                '-j', 'ACCEPT'
            ], check=True)
            
            subprocess.run([
                'iptables', '-I', 'FORWARD', '1',
                '-s', CONTAINER_IP,
                '-p', 'tcp', '--sport', str(container_port),
                '-j', 'ACCEPT'
            ], check=True)
            
            # Allow traffic from/to shocker0 bridge (like Docker does for docker0)
            subprocess.run([
                'iptables', '-I', 'FORWARD', '1',
                '-i', BRIDGE_NAME,
                '-j', 'ACCEPT'
            ], check=True)
            
            subprocess.run([
                'iptables', '-I', 'FORWARD', '1',
                '-o', BRIDGE_NAME,
                '-j', 'ACCEPT'
            ], check=True)
            
            print(f"🔌 Port forwarding: localhost:{host_port} → container:{container_port}")
            
        except Exception as e:
            print(f"⚠️  Failed to setup port forwarding: {e}")

def cleanup_port_forwarding(port_mappings: List[tuple[int, int]]):
    """Clean up port forwarding rules."""
    for host_port, container_port in port_mappings:
        # Clean up NAT rules
        subprocess.run([
            'iptables', '-t', 'nat', '-D', 'PREROUTING',
            '-p', 'tcp', '--dport', str(host_port),
            '-j', 'DNAT', '--to-destination', f'{CONTAINER_IP}:{container_port}'
        ], check=False)
        
        subprocess.run([
            'iptables', '-t', 'nat', '-D', 'OUTPUT',
            '-p', 'tcp', '--dport', str(host_port),
            '-d', '127.0.0.1',
            '-j', 'DNAT', '--to-destination', f'{CONTAINER_IP}:{container_port}'
        ], check=False)
        
        subprocess.run([
            'iptables', '-t', 'nat', '-D', 'POSTROUTING',
            '-d', CONTAINER_IP,
            '-p', 'tcp', '--dport', str(container_port),
            '-j', 'MASQUERADE'
        ], check=False)
        
        # Clean up FORWARD rules
        subprocess.run([
            'iptables', '-D', 'FORWARD',
            '-d', CONTAINER_IP,
            '-p', 'tcp', '--dport', str(container_port),
            '-j', 'ACCEPT'
        ], check=False)
        
        subprocess.run([
            'iptables', '-D', 'FORWARD',
            '-s', CONTAINER_IP,
            '-p', 'tcp', '--sport', str(container_port),
            '-j', 'ACCEPT'
        ], check=False)
        
        subprocess.run([
            'iptables', '-D', 'FORWARD',
            '-i', BRIDGE_NAME,
            '-j', 'ACCEPT'
        ], check=False)
        
        subprocess.run([
            'iptables', '-D', 'FORWARD',
            '-o', BRIDGE_NAME,
            '-j', 'ACCEPT'
        ], check=False)

def setup_port_forwarding_socat(port_mappings: List[tuple[int, int]]) -> List[subprocess.Popen]:
    """Set up port forwarding using socat (simpler than iptables)."""
    socat_processes = []
    
    for host_port, container_port in port_mappings:
        try:
            # Start socat process to forward traffic
            socat_cmd = [
                'socat', 
                f'TCP-LISTEN:{host_port},fork,reuseaddr',
                f'TCP:{CONTAINER_IP}:{container_port}'
            ]
            
            process = subprocess.Popen(socat_cmd)
            socat_processes.append(process)
            
            print(f"🔌 socat port forwarding: localhost:{host_port} → container:{container_port}")
            
        except Exception as e:
            print(f"⚠️  Failed to setup socat forwarding: {e}")
    
    return socat_processes

def cleanup_port_forwarding_socat(socat_processes: List[subprocess.Popen]):
    """Clean up socat processes."""
    for process in socat_processes:
        try:
            process.terminate()
            process.wait(timeout=5)
        except:
            try:
                process.kill()
                process.wait()
            except:
                pass
    print("🧹 Cleaned up socat processes")

def run_container(repository: str, tag: str, command: List[str], 
                  tty: bool = False, port_mappings: List[tuple[int, int]] = None):
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
    
    netns_name = f"shocker-{temp_dir.name}"
    setup_network_namespace(netns_name)
    
    # Set up port forwarding with socat instead of iptables
    socat_processes = []
    if port_mappings:
        socat_processes = setup_port_forwarding_socat(port_mappings)
    
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
        print(f"🌐 Container IP: {CONTAINER_IP} (connected to bridge {BRIDGE_NAME})")

        result = chroot_execute(
            root_path=rootfs_path,
            command=command,
            netns_name=netns_name
        )
        
        if result and hasattr(result, 'returncode'):
            print(f"\n✅ Command completed with exit code: {result.returncode}")
            
    finally:
        # Clean up socat processes
        if socat_processes:
            cleanup_port_forwarding_socat(socat_processes)
        
        cleanup_network_namespace(netns_name)
        
        # Clean up temporary directory
        print(f"\n🧹 Cleaning up temporary directory: {temp_dir}")
        shutil.rmtree(temp_dir)
