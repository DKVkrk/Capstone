#!/usr/bin/python3
"""
This script creates a "diamond" topology for the DRL project.
h1 --- s1 --- s2 --- s4 --- h2
        | \       / |
        |  -------  |
        | /       \ |
        --- s3 ---
        
Host h1 is the traffic source.
Host h2 is the traffic destination.
The DRL agent will learn to route traffic from s1 via s2 (Path A) or s3 (Path B).
"""
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, Controller, RemoteController
from mininet.cli import CLI
from mininet.log import setLogLevel

class DiamondTopo(Topo):
    "Diamond Topology with two paths between h1 and h2"
    def build(self):
        # Add hosts
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')

        # Add switches
        s1 = self.addSwitch('s1', dpid='0000000000000001')
        s2 = self.addSwitch('s2', dpid='0000000000000002')
        s3 = self.addSwitch('s3', dpid='0000000000000003')
        s4 = self.addSwitch('s4', dpid='0000000000000004')

        # Add links
        # Host to switch
        self.addLink(h1, s1, port1=1, port2=1) # h1 -> s1:p1
        self.addLink(h2, s4, port1=1, port2=1) # h2 -> s4:p1

        # Path A (s1-s2-s4)
        self.addLink(s1, s2, port1=2, port2=1) # s1:p2 -> s2:p1
        self.addLink(s2, s4, port1=2, port2=2) # s2:p2 -> s4:p2

        # Path B (s1-s3-s4)
        self.addLink(s1, s3, port1=3, port2=1) # s1:p3 -> s3:p1
        self.addLink(s3, s4, port1=2, port2=3) # s3:p2 -> s4:p3

def runNet():
    # Set up the controller
    c0 = RemoteController('c0', ip='127.0.0.1', port=6653)
    
    # Set up the topology
    topo = DiamondTopo()
    
    # Create the network
    net = Mininet(topo=topo, switch=OVSKernelSwitch, controller=c0, autoSetMacs=True)
    
    net.start()
    
    # -----------------------------------------------------------------
    # --- STATIC FLOW RULES FOR ALL SWITCHES ---
    # -----------------------------------------------------------------
    s1, s2, s3, s4 = net.get('s1', 's2', 's3', 's4')
    h1, h2 = net.get('h1', 'h2')
    h1_ip, h2_ip = h1.IP(), h2.IP()

    print("--- Adding static flow rules to s1, s2, s3, and s4 ---")

    # --- s1 (The AI-controlled switch) ---
    # Add rules for the RETURN traffic (h2 -> h1)
    # s1: Forward h2->h1 return traffic (from Path A, in_port=2) out to h1 (output:1)
    s1.cmd(f'ovs-ofctl add-flow {s1.name} priority=10,in_port=2,eth_type=0x0800,nw_dst={h1_ip},actions=output:1')
    # s1: Forward h2->h1 return traffic (from Path B, in_port=3) out to h1 (output:1)
    s1.cmd(f'ovs-ofctl add-flow {s1.name} priority=10,in_port=3,eth_type=0x0800,nw_dst={h1_ip},actions=output:1')

    # --- s2 (Path A) ---
    # Forward h1->h2 traffic (from s1:p1) out to s4:p2
    s2.cmd(f'ovs-ofctl add-flow {s2.name} priority=10,in_port=1,eth_type=0x0800,nw_dst={h2_ip},actions=output:2')
    # Forward h2->h1 return traffic (from s4:p2) out to s1:p1
    s2.cmd(f'ovs-ofctl add-flow {s2.name} priority=10,in_port=2,eth_type=0x0800,nw_dst={h1_ip},actions=output:1')

    # --- s3 (Path B) ---
    # Forward h1->h2 traffic (from s1:p1) out to s4:p2
    s3.cmd(f'ovs-ofctl add-flow {s3.name} priority=10,in_port=1,eth_type=0x0800,nw_dst={h2_ip},actions=output:2')
    # Forward h2->h1 return traffic (from s4:p2) out to s1:p1
    s3.cmd(f'ovs-ofctl add-flow {s3.name} priority=10,in_port=2,eth_type=0x0800,nw_dst={h1_ip},actions=output:1')

    # --- s4 (The join point) ---
    # Forward h1->h2 traffic from Path A (from s2:p2) out to h2:p1
    s4.cmd(f'ovs-ofctl add-flow {s4.name} priority=10,in_port=2,eth_type=0x0800,nw_dst={h2_ip},actions=output:1')
    # Forward h1->h2 traffic from Path B (from s3:p3) out to h2:p1
    s4.cmd(f'ovs-ofctl add-flow {s4.name} priority=10,in_port=3,eth_type=0x0800,nw_dst={h2_ip},actions=output:1')
    
    # Forward h2->h1 return traffic (from h2:p1) to Path A (s2:p2)
    # We will make Path A (s4:p2) the default return path
    s4.cmd(f'ovs-ofctl add-flow {s4.name} priority=10,in_port=1,eth_type=0x0800,nw_dst={h1_ip},actions=output:2')
    # -----------------------------------------------------------------
    # --- END OF NEW CODE BLOCK ---
    # -----------------------------------------------------------------
    
    # Run the Mininet Command-Line Interface
    CLI(net)
    
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    runNet()