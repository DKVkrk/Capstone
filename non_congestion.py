#!/usr/bin/env python3
"""
A Mininet script to create a network with 4 senders and 1 receiver,
generating predictable, non-congested traffic for clean Wireshark captures.
"""
from mininet.net import Mininet
# MODIFICATION 1: Import OVSController instead of the generic Controller
from mininet.node import OVSController
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info
import time
import os

def create_clean_network():
    """Create and configure the network topology."""
    os.system('sudo mn -c >/dev/null 2>&1')

    # MODIFICATION 2: Use the built-in OVSController
    net = Mininet(controller=OVSController, link=TCLink, cleanup=True)

    info('*** Adding controller\n')
    # The controller is now managed by Mininet automatically, no need to add it
    # c0 = net.addController('c0') # This line is no longer needed

    info('*** Adding hosts\n')
    senders = [net.addHost(f'h{i+1}') for i in range(4)]
    receiver = net.addHost('h5')

    info('*** Adding switch\n')
    s1 = net.addSwitch('s1')

    info('*** Creating links\n')
    for sender in senders:
        net.addLink(sender, s1, bw=10, delay='2ms')

    net.addLink(receiver, s1, bw=100, delay='2ms')

    info('*** Starting network\n')
    net.start()
    return net

def run_traffic_test(net):
    """Run the iperf3 traffic test and capture packets."""
    os.system('rm -f /tmp/*.pcap')

    info('*** Starting tcpdump on all hosts\n')
    for host in net.hosts:
        host.cmd(f'tcpdump -w /tmp/{host.name}.pcap -i {host.intf()} &')

    time.sleep(1)

    receiver = net.get('h5')
    info(f'*** Starting iperf3 server on {receiver.name}\n')
    receiver.cmd('iperf3 -s &')
    
    time.sleep(1)

    senders = [net.get(f'h{i+1}') for i in range(4)]
    client_procs = []
    
    info(f'*** Starting iperf3 clients from h1-h4 to {receiver.name}\n')
    for sender in senders:
        proc = sender.popen(f'iperf3 -c {receiver.IP()} -t 10 -b 5M')
        client_procs.append(proc)

    info('*** Traffic running for 10 seconds. Waiting for clients to finish...\n')
    for proc in client_procs:
        proc.wait()

    info('*** iperf clients finished. Cleaning up.\n')
    
    time.sleep(2)

    receiver.cmd('kill %iperf3')
    for host in net.hosts:
        host.cmd('kill %tcpdump')

    info('*** Done. Check /tmp/*.pcap files for Wireshark\n')


if __name__ == '__main__':
    setLogLevel('info')
    network = create_clean_network()
    run_traffic_test(network)
    
    CLI(network)
    
    network.stop()
