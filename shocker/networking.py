from pathlib import Path
import shutil
import subprocess
from typing import List
from pyroute2 import IPRoute, NetNS, netns
from pyroute2.netlink.exceptions import NetlinkError

# Simple bridge configuration
BRIDGE_NAME = "shocker0"
BRIDGE_IP = "69.69.0.1"

def ensure_bridge_exists():
    """Create and configure the shocker bridge if it doesn't exist."""
    try:
        with IPRoute() as ipr:
            # Check if bridge already exists
            try:
                bridge_idx = ipr.link_lookup(ifname=BRIDGE_NAME)[0]
                print(f"🌉 Bridge {BRIDGE_NAME} already exists")
                enable_bridge_forwarding()  # Add this line
                return
            except IndexError:
                pass  # Bridge doesn't exist, create it
            
            # Create bridge
            ipr.link('add', ifname=BRIDGE_NAME, kind='bridge')
            bridge_idx = ipr.link_lookup(ifname=BRIDGE_NAME)[0]
            
            # Configure bridge IP
            ipr.addr('add', index=bridge_idx, address=BRIDGE_IP, prefixlen=24)
            ipr.link('set', index=bridge_idx, state='up')
            
            enable_bridge_forwarding()  # Add this line too
            
            print(f"🌉 Created bridge {BRIDGE_NAME} at {BRIDGE_IP}/24")
            
    except Exception as e:
        print(f"❌ Failed to create bridge: {e}")
        raise

def setup_network_namespace(netns_name: str, container_ip: str) -> str:
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
        
        # Create veth pair with unique names
        veth_host = f"veth-{netns_name[-8:]}"  # Use last 8 chars of netns name
        veth_container = "eth0"  # Standard container interface name
        
        with IPRoute() as ipr:
            # Create veth pair
            ipr.link('add', ifname=veth_host, peer=veth_container, kind='veth')
            
            # Get the container end's interface index before moving it
            container_idx = ipr.link_lookup(ifname=veth_container)[0]
            
            # Generate a unique MAC address based on the container IP
            # Format: 02:42:AC:XX:XX:XX (Docker-style, locally administered)
            ip_parts = container_ip.split('.')
            mac_address = f"02:42:45:{int(ip_parts[2]):02x}:{int(ip_parts[3]):02x}:00"
            
            # Set MAC address before moving to namespace
            ipr.link('set', index=container_idx, address=mac_address)
            
            # Move container end to namespace
            ipr.link('set', index=container_idx, net_ns_fd=netns_name)
            
            # Connect host end to bridge
            bridge_idx = ipr.link_lookup(ifname=BRIDGE_NAME)[0]
            veth_host_idx = ipr.link_lookup(ifname=veth_host)[0]
            ipr.link('set', index=veth_host_idx, master=bridge_idx, state='up')
            
            # Enable hairpin mode and learning on the veth
            subprocess.run(['bridge', 'link', 'set', 'dev', veth_host, 'hairpin', 'on'], check=False)
            subprocess.run(['bridge', 'link', 'set', 'dev', veth_host, 'learning', 'on'], check=False)
        
        # Configure container end inside namespace with dynamic IP
        with NetNS(netns_name) as ns:
            container_idx = ns.link_lookup(ifname=veth_container)[0]
            ns.addr('add', index=container_idx, address=container_ip, prefixlen=24)
            ns.link('set', index=container_idx, state='up')
            
            # Set up loopback
            lo_idx = ns.link_lookup(ifname='lo')[0]
            ns.link('set', index=lo_idx, state='up')
            
            # Add default route via bridge
            ns.route('add', dst='default', gateway=BRIDGE_IP)
        
        # Send gratuitous ARP to populate bridge fdb
        subprocess.run([
            'ip', 'netns', 'exec', netns_name,
            'ping', '-c', '1', '-W', '1', BRIDGE_IP
        ], check=False, capture_output=True)
        
        print(f"🔗 Container connected to bridge: {container_ip} → {BRIDGE_NAME}")
        return netns_name
        
    except Exception as e:
        print(f"❌ Failed to setup network namespace: {e}")
        cleanup_network_namespace(netns_name)
        raise

def cleanup_network_namespace(netns_name: str):
    """Clean up network namespace and associated resources."""
    try:
        # Remove veth interface with unique name
        veth_host = f"veth-{netns_name[-8:]}"
        try:
            with IPRoute() as ipr:
                host_idx = ipr.link_lookup(ifname=veth_host)
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


def setup_dns(root_path, container_name: str = None):
    """Setup DNS and hosts file for container."""
    from shocker.container_registry import ContainerRegistry
    
    host_resolv = Path("/etc/resolv.conf")
    container_resolv = root_path / "etc" / "resolv.conf"
    container_hosts = root_path / "etc" / "hosts"
    
    # Ensure etc directory exists
    container_resolv.parent.mkdir(parents=True, exist_ok=True)
    
    # Copy DNS configuration
    if host_resolv.exists():
        shutil.copy2(host_resolv, container_resolv)
    
    # Build hosts file with standard entries + all containers
    hosts_content = [
        "127.0.0.1\tlocalhost",
        "::1\t\tlocalhost ip6-localhost ip6-loopback",
        "fe00::0\t\tip6-localnet",
        "ff00::0\t\tip6-mcastprefix",
        "ff02::1\t\tip6-allnodes",
        "ff02::2\t\tip6-allrouters",
        "",
        "# Container hostnames",
    ]
    
    # Add all registered containers
    container_entries = ContainerRegistry.get_hosts_entries()
    if container_entries:
        hosts_content.append(container_entries)
    
    container_hosts.write_text("\n".join(hosts_content) + "\n")
    
    print(f"📡 Configured DNS and hosts for container")
    if container_entries:
        print(f"   Added {len(ContainerRegistry.list_all())} container hostname(s)")

def setup_port_forwarding(port_mappings: List[tuple[int, int]], container_ip: str):
    """Set up port forwarding using iptables for localhost access."""
    
    # Enable route_localnet to allow DNAT from localhost to work properly
    subprocess.run([
        'sysctl', '-w', 'net.ipv4.conf.lo.route_localnet=1'
    ], check=False)
    
    subprocess.run([
        'sysctl', '-w', 'net.ipv4.conf.all.route_localnet=1'
    ], check=False)
    
    for host_port, container_port in port_mappings:
        # FORWARD rules - insert AFTER the bridge rules (position 3 or higher)
        # Find the position after bridge rules
        subprocess.run([
            "iptables", "-A", "FORWARD",  # Changed from -I to -A (append)
            "-d", container_ip, "-p", "tcp", "--dport", str(container_port),
            "-j", "ACCEPT"
        ], check=True)
        
        subprocess.run([
            "iptables", "-A", "FORWARD",  # Changed from -I to -A (append)
            "-s", container_ip, "-p", "tcp", "--sport", str(container_port),
            "-j", "ACCEPT"
        ], check=True)
        
        # DNAT rules
        subprocess.run([
            "iptables", "-t", "nat", "-A", "PREROUTING",
            "-p", "tcp", "--dport", str(host_port),
            "-j", "DNAT", "--to-destination", f"{container_ip}:{container_port}"
        ], check=True)
        
        subprocess.run([
            "iptables", "-t", "nat", "-A", "OUTPUT",
            "-p", "tcp", "-d", "127.0.0.1", "--dport", str(host_port),
            "-j", "DNAT", "--to-destination", f"{container_ip}:{container_port}"
        ], check=True)
        
        # MASQUERADE rule
        subprocess.run([
            "iptables", "-t", "nat", "-A", "POSTROUTING",
            "-p", "tcp", "-d", container_ip, "--dport", str(container_port),
            "-j", "MASQUERADE"
        ], check=True)
        
        print(f"🔌 Port forwarding: localhost:{host_port} → {container_ip}:{container_port}")
            

def cleanup_port_forwarding(port_mappings: List[tuple[int, int]], container_ip: str):
    """Clean up iptables port forwarding rules."""
    for host_port, container_port in port_mappings:
        # Clean up NAT rules
        subprocess.run([
            'iptables', '-t', 'nat', '-D', 'PREROUTING',
            '-p', 'tcp', '--dport', str(host_port),
            '-j', 'DNAT', '--to-destination', f'{container_ip}:{container_port}'
        ], check=False)
        
        subprocess.run([
            'iptables', '-t', 'nat', '-D', 'OUTPUT',
            '-p', 'tcp', '-d', '127.0.0.1', '--dport', str(host_port),
            '-j', 'DNAT', '--to-destination', f'{container_ip}:{container_port}'
        ], check=False)
        
        subprocess.run([
            'iptables', '-t', 'nat', '-D', 'POSTROUTING',
            '-p', 'tcp', '-d', container_ip, '--dport', str(container_port),
            '-j', 'MASQUERADE'
        ], check=False)
        
        # Clean up FORWARD rules
        subprocess.run([
            'iptables', '-D', 'FORWARD',
            '-s', container_ip, '-p', 'tcp', '--sport', str(container_port),
            '-j', 'ACCEPT'
        ], check=False)
        
        subprocess.run([
            'iptables', '-D', 'FORWARD',
            '-d', container_ip, '-p', 'tcp', '--dport', str(container_port),
            '-j', 'ACCEPT'
        ], check=False)
    
    print("🧹 Cleaned up iptables rules")

def enable_bridge_forwarding():
    """Enable IP forwarding and configure iptables for container networking."""
    # Enable IP forwarding
    subprocess.run(['sysctl', '-w', 'net.ipv4.ip_forward=1'], check=False)
    
    # Check and add bridge forwarding rule for traffic on the bridge subnet
    check_rule = subprocess.run([
        'iptables', '-C', 'FORWARD',
        '-s', '69.69.0.0/24', '-d', '69.69.0.0/24',
        '-j', 'ACCEPT'
    ], capture_output=True)
    
    if check_rule.returncode != 0:
        subprocess.run([
            'iptables', '-I', 'FORWARD', '1',
            '-s', '69.69.0.0/24', '-d', '69.69.0.0/24',
            '-j', 'ACCEPT'
        ], check=True)
    
    # Check and add established connections rule
    check_established = subprocess.run([
        'iptables', '-C', 'FORWARD',
        '-m', 'state', '--state', 'RELATED,ESTABLISHED',
        '-j', 'ACCEPT'
    ], capture_output=True)
    
    if check_established.returncode != 0:
        subprocess.run([
            'iptables', '-I', 'FORWARD', '2',
            '-m', 'state', '--state', 'RELATED,ESTABLISHED',
            '-j', 'ACCEPT'
        ], check=True)
    
    print(f"🔀 Enabled forwarding for {BRIDGE_NAME}")
