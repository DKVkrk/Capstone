#!/usr/bin/env python3
"""
congestion.py: A Mininet script to create a network with 4 senders and 1 receiver,
specifically designed to create a many-to-one congestion scenario for analysis.
This enhanced version includes formatted output and stabilizing delays.
"""
from mininet.net import Mininet
from mininet.node import Controller, OVSController, RemoteController
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info
import time
import os
import sys

def print_header(title):
    """Prints a formatted header to the console."""
    info('\n' + '-'*60 + '\n')
    info(f'*** {title}\n')
    info('-'*60 + '\n')

def create_congestion_network(controller_type):
    """Create and configure the network topology."""
    os.system('sudo mn -c >/dev/null 2>&1')
    net = Mininet(controller=controller_type, link=TCLink, cleanup=True)

    print_header("Network Setup Phase")

    info('--> Adding controller\n')
    if controller_type == OVSController:
        net.addController('c0')
    elif controller_type == RemoteController:
        # Assuming the controller is running on localhost
        net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6633)


    info('--> Adding hosts: 4 senders (h1-h4) and 1 receiver (h5)\n')
    senders = [net.addHost(f'h{i+1}') for i in range(4)]
    receiver = net.addHost('h5')

    info('--> Adding switch: s1\n')
    s1 = net.addSwitch('s1')

    info('--> Creating links and defining bandwidths\n')
    for sender in senders:
        # Senders have a limited bandwidth of 5 Mbps
        net.addLink(sender, s1, bw=5, delay='2ms')
        info(f'    - Link: {sender.name} <--> s1 (5 Mbps)\n')

    # The link to the receiver is the bottleneck.
    # Total sender bandwidth (4*5=20 Mbps) exceeds receiver link capacity (10 Mbps).
    net.addLink(receiver, s1, bw=10, delay='2ms')
    info(f'    - Bottleneck Link: {receiver.name} <--> s1 (10 Mbps)\n')

    info('--> Starting network\n')
    net.start()
    
    # --- STABILIZING DELAY ---
    info('--> Waiting for network components to stabilize...\n')
    time.sleep(2)
    
    return net

def run_traffic_test(net):
    """Run the iperf3 traffic test and capture packets."""
    os.system('rm -f /tmp/*.pcap')
    
    print_header("Traffic Simulation & Data Capture Phase")

    info('--> Starting packet capture (tcpdump) on all hosts\n')
    for host in net.hosts:
        host.cmd(f'tcpdump -w /tmp/{host.name}.pcap -i {host.intf()} &')

    # --- STABILIZING DELAY ---
    # Wait a moment for tcpdump to initialize properly.
    time.sleep(1)

    receiver = net.get('h5')
    info(f'--> Starting iperf3 server on receiver {receiver.name} ({receiver.IP()})\n')
    # The iperf3 server can handle both TCP and UDP traffic by default.
    receiver.cmd('iperf3 -s &')
    
    # --- STABILIZING DELAY ---
    # Wait a moment for the iperf3 server to start listening. This is critical.
    time.sleep(1)

    senders = [net.get(f'h{i+1}') for i in range(4)]
    
    info(f'--> Starting iperf3 clients from h1-h4 to {receiver.name} using UDP\n')
    for sender in senders:
        # The '-u' flag specifies UDP.
        # The '-b 5M' flag tells the client to send at a constant 5 Mbps rate.
        sender.cmd(f'iperf3 -c {receiver.IP()} -u -b 5M -t 20 &')

    info('\n*** Traffic is now running for 20 seconds. FORCING CONGESTION WITH UDP. ***\n')
    
    # --- CRITICAL FIX: Wait for the traffic test to complete ---
    # The iperf3 clients are running in the background for 20 seconds.
    # We must pause the script here to allow them to finish.
    time.sleep(21)  # Wait for 21 seconds (to be safe)

    info('--> Test finished. Killing background processes.\n')
    os.system('killall iperf3 tcpdump >/dev/null 2>&1')
    info('--> Data capture complete. Check /tmp/*.pcap files for analysis.\n')


if __name__ == '__main__':
    setLogLevel('info')
    
    # Default to the built-in controller for standalone tests
    controller = OVSController

    # Check command-line arguments to see if a remote controller is specified
    if any('remote' in arg for arg in sys.argv):
        info('*** Detected --controller=remote flag. Setting up for Ryu.\n')
        controller = RemoteController
    else:
        info('*** No remote controller specified. Using default OVS Controller.\n')

    network = create_congestion_network(controller) 
    run_traffic_test(network)
    
    info('\n' + '-'*60 + '\n')
    info('*** Simulation complete. Starting Mininet CLI.\n')
    info('*** Type "exit" to stop the network.\n')
    info('-'*60 + '\n\n')
    
    CLI(network)
    
    network.stop()





 