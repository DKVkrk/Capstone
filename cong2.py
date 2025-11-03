#!/usr/bin/env python3
"""
mininet_congestion.py - Simple congestion simulation: many senders → 1 receiver
High sender bandwidth > bottleneck to receiver to force packet loss.
"""

import os
import time
import json
from pathlib import Path
from mininet.net import Mininet
from mininet.node import OVSController, RemoteController
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info

CAPTURE_DIR = Path('/tmp')

# Network config
EDGE_SWITCH_COUNT = 3
SENDERS_PER_EDGE = 4
SENDER_BW_MEG = 20      # Very high to force congestion
CORE_TO_RECEIVER_BW_MEG = 10  # Bottleneck lower than total sender bw
LINK_DELAY = '2ms'
TRAFFIC_DURATION = 15
EXTRA_CROSS_TRAFFIC = True
CROSS_TRAFFIC_RATE_M = 5    # extra traffic to increase congestion

def create_network():
    os.system('sudo mn -c >/dev/null 2>&1')
    
    # Connects to your external Ryu controller
    net = Mininet(controller=None, link=TCLink, cleanup=True)
    net.addController('c0',
                      controller=RemoteController,
                      ip='127.0.0.1',
                      port=6653)

    info('*** Adding core and edge switches\n')
    core = net.addSwitch('s0')
    edge_switches = []
    for i in range(EDGE_SWITCH_COUNT):
        sw = net.addSwitch(f's{i+1}')
        edge_switches.append(sw)
        net.addLink(sw, core, bw=1000, delay=LINK_DELAY)  # fast links to core

    info('*** Adding senders\n')
    senders = []
    host_idx = 1
    for sw in edge_switches:
        for _ in range(SENDERS_PER_EDGE):
            h = net.addHost(f'h{host_idx}')
            net.addLink(h, sw, bw=SENDER_BW_MEG, delay=LINK_DELAY)
            senders.append(h)
            host_idx += 1

    info('*** Adding receiver (bottleneck)\n')
    receiver = net.addHost('hr')
    net.addLink(receiver, core, bw=CORE_TO_RECEIVER_BW_MEG, delay=LINK_DELAY)

    info('*** Starting network\n')
    net.start()
    time.sleep(2)
    return net, senders, receiver

def start_tcpdump(net):
    CAPTURE_DIR.mkdir(exist_ok=True)
    for h in net.hosts:
        intf = h.defaultIntf()
        pcap = CAPTURE_DIR / f'{h.name}.pcap'
        h.cmd(f'tcpdump -i {intf} -U -n -w {pcap} >/dev/null 2>&1 &')
        info(f'    - tcpdump on {h.name} -> {pcap}\n')
    time.sleep(1)

def start_iperf(net, senders, receiver):
    info('*** Starting main iperf traffic (senders -> receiver)\n')
    receiver.cmd('iperf3 -s >/dev/null 2>&1 &')
    time.sleep(1)
    for s in senders:
        out = CAPTURE_DIR / f'iperf_{s.name}.json'
        s.cmd(f'iperf3 -c {receiver.IP()} -u -b {SENDER_BW_MEG}M -t {TRAFFIC_DURATION} -J > {out} 2>&1 &')
        info(f'    - {s.name} -> {receiver.name} : {SENDER_BW_MEG}M\n')

def launch_extra_traffic(net):
    if not EXTRA_CROSS_TRAFFIC:
        return
    
    info('*** Launching extra cross-traffic\n')
    # FIX: Exclude 'hr' from the list of hosts used for cross-traffic
    hosts = [h for h in net.hosts if h.name.startswith('h') and h.name != 'hr']
    
    destinations = []
    
    # Start iperf servers on destination hosts first
    for i in range(0, len(hosts), 2):
        if (i+1) < len(hosts):
            dst = hosts[(i+1)]
            destinations.append(dst)
            # FIX: Start an iperf server on the destination host
            dst.cmd('iperf3 -s >/dev/null 2>&1 &')
            
    time.sleep(1) # Give servers time to start

    # Now start clients
    for i in range(0, len(hosts), 2):
        if (i+1) < len(hosts):
            src = hosts[i]
            dst = hosts[(i+1)]
            out = CAPTURE_DIR / f'iperf_cross_{src.name}_to_{dst.name}.json'
            src.cmd(f'iperf3 -c {dst.IP()} -u -b {CROSS_TRAFFIC_RATE_M}M -t {TRAFFIC_DURATION} -J > {out} 2>&1 &')
            info(f'    - extra {src.name} -> {dst.name} : {CROSS_TRAFFIC_RATE_M}M\n')

def stop_processes():
    os.system('killall iperf3 tcpdump >/dev/null 2>&1 || true')

def analyze_results():
    csv_path = CAPTURE_DIR / 'iperf_summary.csv'
    rows = []
    
    # FIX: Use a glob pattern that *only* matches sender hosts (h1, h2, etc.)
    # This prevents old 'iperf_hr.json' files from being included.
    for j in CAPTURE_DIR.glob('iperf_h[0-9]*.json'):
        try:
            data = json.loads(j.read_text())
        except:
            continue
        end = data.get('end', {})
        # Use 'sum' for UDP results
        sum_stats = end.get('sum')
        if sum_stats is None:
            continue
            
        bps = sum_stats.get('bits_per_second', 0)
        lost = sum_stats.get('lost_percent', 0)
        mbps = bps / 1e6
        rows.append((j.stem.replace('iperf_', ''), bps, mbps, lost))
        
    # Sort rows by host name (h1, h2, ... h10, h11, h12)
    rows.sort(key=lambda x: int(x[0].replace('h', '')))

    # write CSV
    with open(csv_path, 'w') as f:
        f.write('host,bits_per_second,mbps,lost_percent\n')
        for r in rows:
            f.write(f'{r[0]},{r[1]},{r[2]},{r[3]}\n')

    # Print the results to the console
    info('*** iPerf Congestion Results (Senders -> Receiver) ***\n')
    print(f"{'Host':<10} | {'Throughput (Mbps)':<20} | {'Packet Loss (%)':<18}")
    print("-" * 52)

    total_mbps = 0
    total_lost_percent = 0
    sender_count = 0

    for r in rows:
        # r[0] = host, r[1] = bps, r[2] = mbps, r[3] = lost
        host = r[0]
        mbps = r[2]
        lost = r[3]
        print(f"{host:<10} | {mbps:<20.2f} | {lost:<18.2f}")
        total_mbps += mbps
        total_lost_percent += lost
        sender_count += 1
        
    if sender_count > 0:
        avg_loss = total_lost_percent / sender_count
        print("-" * 52)
        print(f"{'TOTAL':<10} | {total_mbps:<20.2f} |")
        print(f"{'AVG LOSS':<10} | {'':<20} | {avg_loss:<18.2f}")
    
    info(f'\n*** CSV summary written to {csv_path}\n')


def main():
    setLogLevel('info')
    net, senders, receiver = create_network()
    try:
        start_tcpdump(net)
        start_iperf(net, senders, receiver)
        launch_extra_traffic(net)
        info(f'*** Waiting {TRAFFIC_DURATION+3}s for traffic to finish\n')
        time.sleep(TRAFFIC_DURATION + 3)
        stop_processes()
        analyze_results() # This will now print the results
        CLI(net)
    finally:
        net.stop()
        stop_processes()

if __name__ == '__main__':
    main()