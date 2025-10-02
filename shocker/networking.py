from pathlib import Path
import shutil
import subprocess
from typing import List
from pyroute2 import IPRoute, NetNS, netns
from pyroute2.netlink.exceptions import NetlinkError

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
        
        print(f"🔗 Container connected to bridge: {CONTAINER_IP} → {BRIDGE_NAME}")
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
    """Set up port forwarding using iptables for localhost access."""
    
    # Enable route_localnet to allow DNAT from localhost to work properly
    # This allows the loopback interface to route to non-local addresses
    subprocess.run([
        'sysctl', '-w', 'net.ipv4.conf.lo.route_localnet=1'
    ], check=False)
    
    subprocess.run([
        'sysctl', '-w', 'net.ipv4.conf.all.route_localnet=1'
    ], check=False)
    
    for host_port, container_port in port_mappings:
        # FORWARD rules first
        subprocess.run([
            "iptables", "-I", "FORWARD", "1",
            "-d", CONTAINER_IP, "-p", "tcp", "--dport", str(container_port),
            "-j", "ACCEPT"
        ], check=True)
        
        subprocess.run([
            "iptables", "-I", "FORWARD", "1", 
            "-s", CONTAINER_IP, "-p", "tcp", "--sport", str(container_port),
            "-j", "ACCEPT"
        ], check=True)
        
        # DNAT rules
        subprocess.run([
            "iptables", "-t", "nat", "-A", "PREROUTING",
            "-p", "tcp", "--dport", str(host_port),
            "-j", "DNAT", "--to-destination", f"{CONTAINER_IP}:{container_port}"
        ], check=True)
        
        subprocess.run([
            "iptables", "-t", "nat", "-A", "OUTPUT",
            "-p", "tcp", "-d", "127.0.0.1", "--dport", str(host_port),
            "-j", "DNAT", "--to-destination", f"{CONTAINER_IP}:{container_port}"
        ], check=True)
        
        # MASQUERADE rule - remove -o restriction since DNATed packets aren't routing through bridge properly
        subprocess.run([
            "iptables", "-t", "nat", "-A", "POSTROUTING",
            "-p", "tcp", "-d", CONTAINER_IP, "--dport", str(container_port),
            "-j", "MASQUERADE"
        ], check=True)
        
        print(f"🔌 iptables port forwarding: localhost:{host_port} → container:{container_port}")
            

def cleanup_port_forwarding(port_mappings: List[tuple[int, int]]):
    """Clean up iptables port forwarding rules."""
    for host_port, container_port in port_mappings:
        # Clean up NAT rules - match exactly what we created
        subprocess.run([
            'iptables', '-t', 'nat', '-D', 'PREROUTING',
            '-p', 'tcp', '--dport', str(host_port),
            '-j', 'DNAT', '--to-destination', f'{CONTAINER_IP}:{container_port}'
        ], check=False)
        
        subprocess.run([
            'iptables', '-t', 'nat', '-D', 'OUTPUT',
            '-p', 'tcp', '-d', '127.0.0.1', '--dport', str(host_port),
            '-j', 'DNAT', '--to-destination', f'{CONTAINER_IP}:{container_port}'
        ], check=False)
        
        subprocess.run([
            'iptables', '-t', 'nat', '-D', 'POSTROUTING',
            '-p', 'tcp', '-d', CONTAINER_IP, '--dport', str(container_port),
            '-j', 'MASQUERADE'
        ], check=False)
        
        # Clean up FORWARD rules - match exactly what we created
        subprocess.run([
            'iptables', '-D', 'FORWARD',
            '-s', CONTAINER_IP, '-p', 'tcp', '--sport', str(container_port),
            '-j', 'ACCEPT'
        ], check=False)
        
        subprocess.run([
            'iptables', '-D', 'FORWARD',
            '-d', CONTAINER_IP, '-p', 'tcp', '--dport', str(container_port),
            '-j', 'ACCEPT'
        ], check=False)
    
    print("🧹 Cleaned up iptables rules")


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
