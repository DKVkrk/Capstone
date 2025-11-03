#!/usr/bin/env python3
"""
cong.py - Expanded Mininet many-to-one congestion test

This script creates a topology with multiple edge switches connected to a core
switch. Each edge switch has multiple sender hosts. A single receiver is
attached to the core switch via a lower-bandwidth link (bottleneck).

Fixes / improvements from earlier version:
 - Uses canonical switch names (s0, s1, s2, ...) so Mininet can derive DPIDs.
 - Better cleanup (try/finally), safer background process handling.
 - Configurable constants at the top for quick scaling.
 - Notes: run as root; requires mininet, iperf3, tcpdump installed.

Usage:
  sudo python3 cong.py
  sudo python3 cong.py remote   # use remote controller (assumes localhost:6633)
"""

import os
import sys
import time
from mininet.net import Mininet
from mininet.node import OVSController, RemoteController
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info

# ------------------ Configuration ------------------
EDGE_SWITCH_COUNT = 3          # number of edge switches (s1..sN)
SENDERS_PER_EDGE = 4          # number of sender hosts per edge switch
SENDER_BW_MEG = 5             # each sender link bandwidth (Mbps)
CORE_TO_RECEIVER_BW_MEG = 10  # bottleneck capacity to receiver (Mbps)
LINK_DELAY = '2ms'
TRAFFIC_DURATION = 20         # seconds for iperf3 client test
CAPTURE_DIR = '/tmp'          # where tcpdump pcap files are stored
# ---------------------------------------------------

def print_header(title):
    info('\n' + '-'*70 + '\n')
    info(f'*** {title}\n')
    info('-'*70 + '\n')

def create_large_congestion_network(controller_type):
    """
    Create a multi-switch many-to-one topology:
      - core switch: s0 (canonical name so Mininet can derive DPID)
      - edge switches: s1 .. sN
      - hosts: h1 .. hM (senders) attached to edge switches
      - receiver: hr attached to core switch (bottleneck)
    """
    # Clean Mininet state
    os.system('sudo mn -c >/dev/null 2>&1')

    net = Mininet(controller=controller_type, link=TCLink, cleanup=True)

    print_header('Network Setup Phase')

    info('--> Adding controller\n')
    if controller_type == OVSController:
        net.addController('c0')
    else:
        net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6633)

    info('--> Adding a core switch (s0) and edge switches (s1..)\n')
    # Use canonical names: s0, s1, s2, ...
    core = net.addSwitch('s0')  # canonical name so Mininet can derive a DPID
    edge_switches = []
    for i in range(EDGE_SWITCH_COUNT):
        sw_name = f's{i+1}'
        sw = net.addSwitch(sw_name)
        edge_switches.append(sw)
        info(f'    - Added edge switch {sw.name}\n')

    info('--> Connecting edge switches to the core switch (high capacity links)\n')
    for sw in edge_switches:
        # Links between core and edges are high capacity (no artificial limit)
        net.addLink(sw, core, bw=1000, delay=LINK_DELAY)
        info(f'    - Link: {sw.name} <--> {core.name} (1 Gbps, {LINK_DELAY})\n')

    info('--> Adding sender hosts on each edge switch\n')
    senders = []
    host_index = 1
    for i, sw in enumerate(edge_switches):
        for j in range(SENDERS_PER_EDGE):
            h_name = f'h{host_index}'
            h = net.addHost(h_name)
            net.addLink(h, sw, bw=SENDER_BW_MEG, delay=LINK_DELAY)
            senders.append(h)
            info(f'    - Host: {h.name} connected to {sw.name} (~{SENDER_BW_MEG} Mbps)\n')
            host_index += 1

    info('--> Adding single receiver host attached to the core switch\n')
    receiver = net.addHost('hr')
    net.addLink(receiver, core, bw=CORE_TO_RECEIVER_BW_MEG, delay=LINK_DELAY)
    info(f'    - Bottleneck Link: {receiver.name} <--> {core.name} ({CORE_TO_RECEIVER_BW_MEG} Mbps)\n')

    info('--> Starting network\n')
    net.start()

    # Stabilize
    info('--> Waiting for network components to stabilize...\n')
    time.sleep(2)

    return net

def run_traffic_test(net):
    """
    Start tcpdump on each host, iperf3 server on receiver, and launch iperf3 UDP
    clients from all senders to the receiver. Wait for completion and cleanup.
    """
    # remove old captures safely
    try:
        os.system(f'rm -f {CAPTURE_DIR}/*.pcap')
    except Exception:
        pass

    print_header('Traffic Simulation & Data Capture Phase')

    # Start tcpdump on each host
    info('--> Starting tcpdump on all hosts (writing to /tmp/<host>.pcap)\n')
    for host in net.hosts:
        # Use defaultIntf name
        intf = host.defaultIntf()
        pcap = f'{CAPTURE_DIR}/{host.name}.pcap'
        # run tcpdump in background; redirect stdout/stderr to avoid clutter
        # Use -U (unbuffered) so pcap grows during capture
        cmd = f'tcpdump -i {intf} -U -w {pcap} >/dev/null 2>&1 &'
        host.cmd(cmd)
        info(f'    - tcpdump on {host.name} (intf {intf}) -> {pcap}\n')

    # Stabilize tcpdump
    time.sleep(1)

    receiver = net.get('hr')
    info(f'--> Starting iperf3 server on receiver {receiver.name} ({receiver.IP()})\n')
    receiver.cmd('iperf3 -s >/dev/null 2>&1 &')

    # Wait for server
    time.sleep(1)

    # Collect sender hosts (exclude receiver)
    senders = [h for h in net.hosts if h.name != receiver.name]

    info(f'--> Launching iperf3 UDP clients from {len(senders)} senders to {receiver.name}\n')
    for s in senders:
        # Each sender attempts to send at configured bandwidth
        s.cmd(f'iperf3 -c {receiver.IP()} -u -b {SENDER_BW_MEG}M -t {TRAFFIC_DURATION} >/dev/null 2>&1 &')
        info(f'    - {s.name} -> {receiver.name} : -u -b {SENDER_BW_MEG}M -t {TRAFFIC_DURATION}\n')

    info(f'\n*** Traffic running for {TRAFFIC_DURATION} seconds. Expect congestion at the bottleneck. ***\n')

    # Wait for traffic to complete (+ safety margin)
    time.sleep(TRAFFIC_DURATION + 2)

    info('--> Traffic test complete. Cleaning up background processes (iperf3, tcpdump)...\n')
    os.system('killall iperf3 tcpdump >/dev/null 2>&1 || true')
    info(f'--> PCAP files are saved in {CAPTURE_DIR}.\n')

def main():
    setLogLevel('info')

    controller = OVSController
    # detect 'remote' anywhere in argv to enable remote controller mode
    if any('remote' in arg for arg in sys.argv[1:]):
        info('*** Detected remote flag. Using RemoteController.\n')
        controller = RemoteController
    else:
        info('*** No remote controller specified. Using default OVSController.\n')

    net = create_large_congestion_network(controller)
    try:
        run_traffic_test(net)

        info('\n' + '-'*70 + '\n')
        info('*** Simulation complete. Starting Mininet CLI.\n')
        info('*** Type "exit" to stop the network.\n')
        info('-'*70 + '\n\n')

        CLI(net)
    finally:
        # Ensure network is stopped even if CLI/traffic throws
        try:
            net.stop()
        except Exception:
            pass
        # best-effort cleanup of background processes
        os.system('killall iperf3 tcpdump >/dev/null 2>&1 || true')

if __name__ == '__main__':
    main()
