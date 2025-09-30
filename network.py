#!/usr/bin/env python3
"""
Minimal network namespace test for shocker
Run with: sudo python test_network.py
"""

import os
import subprocess
import time
from pyroute2 import IPRoute, NetNS, netns
from pyroute2.netlink.exceptions import NetlinkError

def test_basic_netns():
    """Test 1: Just create and list network namespaces"""
    print("=== Test 1: Basic Network Namespace ===")
    
    ns_name = "test_shocker"
    
    try:
        # Create namespace
        print(f"Creating namespace: {ns_name}")
        netns.create(ns_name)
        
        # List namespaces
        print("Current namespaces:")
        result = subprocess.run(['ip', 'netns', 'list'], capture_output=True, text=True)
        print(result.stdout)
        
        # Test running command in namespace
        print("Testing command in namespace:")
        result = subprocess.run(['ip', 'netns', 'exec', ns_name, 'ip', 'link', 'show'], 
                              capture_output=True, text=True)
        print("Interfaces in namespace:", result.stdout)
        
    except NetlinkError as e:
        print(f"Error: {e}")
    finally:
        # Cleanup
        try:
            netns.remove(ns_name)
            print(f"Cleaned up namespace: {ns_name}")
        except:
            pass

def test_veth_pair():
    """Test 2: Create veth pair and basic connectivity"""
    print("\n=== Test 2: Veth Pair ===")
    
    ns_name = "test_shocker2"
    host_veth = "veth_host_test"
    container_veth = "veth_cont_test"
    
    ip = IPRoute()
    
    try:
        # Create namespace
        print(f"Creating namespace: {ns_name}")
        netns.create(ns_name)
        
        # Create veth pair
        print(f"Creating veth pair: {host_veth} <-> {container_veth}")
        ip.link('add', ifname=host_veth, peer=container_veth, kind='veth')
        
        # Get indices
        host_idx = ip.link_lookup(ifname=host_veth)[0]
        container_idx = ip.link_lookup(ifname=container_veth)[0]
        
        # Move container veth to namespace
        print(f"Moving {container_veth} to namespace")
        ip.link('set', index=container_idx, net_ns_fd=ns_name)
        
        # Configure host side
        print("Configuring host side")
        ip.addr('add', index=host_idx, address='172.17.0.1', prefixlen=24)
        ip.link('set', index=host_idx, state='up')
        
        # Configure container side
        print("Configuring container side")
        with NetNS(ns_name) as ns:
            container_idx_ns = ns.link_lookup(ifname=container_veth)[0]
            lo_idx = ns.link_lookup(ifname='lo')[0]
            
            ns.addr('add', index=container_idx_ns, address='172.17.0.2', prefixlen=24)
            ns.link('set', index=container_idx_ns, state='up')
            ns.link('set', index=lo_idx, state='up')
        
        # Test connectivity
        print("\nTesting connectivity:")
        print("From host to container:")
        result = subprocess.run(['ping', '-c', '1', '172.17.0.2'], 
                              capture_output=True, text=True)
        print("Ping result:", "SUCCESS" if result.returncode == 0 else "FAILED")
        
        print("From container to host:")
        result = subprocess.run(['ip', 'netns', 'exec', ns_name, 
                               'ping', '-c', '1', '172.17.0.1'], 
                              capture_output=True, text=True)
        print("Ping result:", "SUCCESS" if result.returncode == 0 else "FAILED")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Cleanup
        try:
            # Remove veth (removes both ends)
            host_indices = ip.link_lookup(ifname=host_veth)
            if host_indices:
                ip.link('del', index=host_indices[0])
        except:
            pass
        
        try:
            netns.remove(ns_name)
            print(f"Cleaned up namespace: {ns_name}")
        except:
            pass
        
        ip.close()

def test_internet_with_bridge_pyroute2():
    """Test 6: Internet access via bridge (Pure pyroute2, no iptables)"""
    print("\n=== Test 6: Internet via Bridge (Pure pyroute2) ===")
    
    # Use unique names with timestamp to avoid conflicts
    timestamp = int(time.time())
    ns_name = f"test_bridge_{timestamp}"
    bridge_name = f"br_shocker_{timestamp}"
    container_veth = f"veth_bridge_cont_{timestamp}"
    host_veth = f"veth_bridge_host_{timestamp}"
    
    ip = IPRoute()
    
    try:
        # Clean up any existing interfaces first
        print("Cleaning up any existing interfaces...")
        try:
            # Try to remove old bridge if exists
            old_bridges = ip.link_lookup(ifname="br_shocker")
            for idx in old_bridges:
                ip.link('del', index=idx)
        except:
            pass
        
        try:
            # Remove old namespace if exists
            netns.remove("test_bridge")
        except:
            pass
        
        # Create bridge interface
        print(f"Creating bridge: {bridge_name}")
        ip.link('add', ifname=bridge_name, kind='bridge')
        bridge_idx = ip.link_lookup(ifname=bridge_name)[0]
        
        # Configure bridge
        print("Configuring bridge...")
        ip.addr('add', index=bridge_idx, address='172.18.0.1', prefixlen=24)
        ip.link('set', index=bridge_idx, state='up')
        
        # Enable bridge forwarding (optional, may not be needed)
        try:
            ip.link('set', index=bridge_idx, br_stp_state=1)  # Enable STP
        except Exception as e:
            print(f"Note: STP setup failed (may not be critical): {e}")
        
        # Create namespace
        print(f"Creating namespace: {ns_name}")
        netns.create(ns_name)
        
        # Create veth pair with unique names
        print(f"Creating veth pair: {host_veth} <-> {container_veth}")
        ip.link('add', ifname=host_veth, peer={'ifname': container_veth}, kind='veth')
        
        host_idx = ip.link_lookup(ifname=host_veth)[0]
        container_idx = ip.link_lookup(ifname=container_veth)[0]
        
        print(f"Host veth index: {host_idx}, Container veth index: {container_idx}")
        
        # Move container veth to namespace
        print(f"Moving {container_veth} to namespace")
        ip.link('set', index=container_idx, net_ns_fd=ns_name)
        
        # Connect host veth to bridge
        print(f"Connecting {host_veth} to bridge")
        ip.link('set', index=host_idx, master=bridge_idx)
        ip.link('set', index=host_idx, state='up')
        
        # Configure container side
        print("Configuring container networking")
        with NetNS(ns_name) as ns:
            container_idx_ns = ns.link_lookup(ifname=container_veth)[0]
            lo_idx = ns.link_lookup(ifname='lo')[0]
            
            ns.addr('add', index=container_idx_ns, address='172.18.0.2', prefixlen=24)
            ns.link('set', index=container_idx_ns, state='up')
            ns.link('set', index=lo_idx, state='up')
            ns.route('add', dst='default', gateway='172.18.0.1')
        
        # Enable IP forwarding
        print("Enabling IP forwarding...")
        with open('/proc/sys/net/ipv4/ip_forward', 'w') as f:
            f.write('1')
        
        # Test basic bridge connectivity first
        print("Testing basic bridge connectivity...")
        result = subprocess.run(['ping', '-c', '1', '172.18.0.2'], 
                              capture_output=True, text=True)
        print("Bridge ping:", "SUCCESS" if result.returncode == 0 else "FAILED")
        
        if result.returncode != 0:
            print("❌ Basic connectivity failed, skipping internet test")
            return
        
        # For internet access, we'd need NAT rules
        print("ℹ️  For internet access, you would need NAT/masquerading rules")
        print("   This would typically require iptables or nftables")
        print("   Bridge networking is working correctly for container-to-host communication")
        
        # Let's try a simple DNS test instead of ping
        print("Testing DNS resolution from container...")
        result = subprocess.run([
            'ip', 'netns', 'exec', ns_name,
            'nslookup', 'google.com'
        ], capture_output=True, text=True, timeout=10)
        
        print("DNS test:", "SUCCESS" if result.returncode == 0 else "FAILED")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Cleanup network interfaces
        print("Cleaning up...")
        try:
            bridge_indices = ip.link_lookup(ifname=bridge_name)
            if bridge_indices:
                ip.link('del', index=bridge_indices[0])
                print(f"Removed bridge: {bridge_name}")
        except Exception as e:
            print(f"Failed to remove bridge: {e}")
        
        try:
            netns.remove(ns_name)
            print(f"Cleaned up namespace: {ns_name}")
        except Exception as e:
            print(f"Failed to remove namespace: {e}")
        
        ip.close()

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("❌ This script requires root privileges. Run with: sudo python network.py")
        exit(1)
    
    print("🧪 Testing network namespaces step by step")
    
    # Run tests one by one
    test_basic_netns()
    test_veth_pair()
    test_internet_with_bridge_pyroute2()
    
    print("\n✅ All tests completed!")