#!/usr/bin/env python3
"""
cong_analysis.py - Mininet many-to-one congestion test + analysis + CSV & quick-demo + optional Wireshark open

This upgraded script includes everything from the previous version plus:
 - Automatic CSV summary of per-sender throughput & loss (/tmp/iperf_summary.csv)
 - Quick-demo mode (use --quick) to run a short test with fewer hosts and shorter duration
 - Optional Wireshark GUI auto-open (use --open-wireshark) if Wireshark is installed and an X display is available
 - Command-line flags: remote, --quick, --open-wireshark

Usage examples:
  sudo python3 cong_analysis.py                # full run (default)
  sudo python3 cong_analysis.py remote         # remote controller
  sudo python3 cong_analysis.py --quick        # quick demo (fewer hosts, shorter run)
  sudo python3 cong_analysis.py --open-wireshark --quick

Outputs:
  - /tmp/*.pcap (pcap files)
  - /tmp/iperf_*.json (iperf3 JSON files)
  - /tmp/tshark_summary_*.txt (tshark text summaries)
  - /tmp/iperf_summary.csv (CSV of per-sender results: host, bits_per_second, Mbps, lost_percent)

Requirements: mininet, iperf3, tcpdump, tshark, optional wireshark
"""

import os
import sys
import time
import json
import shutil
import subprocess
from pathlib import Path
from argparse import ArgumentParser
from mininet.net import Mininet
from mininet.node import OVSController, RemoteController
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info

# ------------------ Default Configuration ------------------
EDGE_SWITCH_COUNT = 3          # number of edge switches (s1..sN)
SENDERS_PER_EDGE = 4           # number of sender hosts per edge switch
SENDER_BW_MEG = 5              # each sender link bandwidth (Mbps)
CORE_TO_RECEIVER_BW_MEG = 10   # bottleneck capacity to receiver (Mbps)
LINK_DELAY = '2ms'
TRAFFIC_DURATION = 20          # seconds for iperf3 client test
CAPTURE_DIR = Path('/tmp')     # where tcpdump pcap files are stored
EXTRA_CROSS_TRAFFIC = True     # launch additional cross-traffic to increase congestion
CROSS_TRAFFIC_RATE_M = 2       # Mbps per cross-traffic flow
# -----------------------------------------------------------


def print_header(title):
    info('\n' + '-' * 70 + '\n')
    info(f'*** {title}\n')
    info('-' * 70 + '\n')


def check_tools():
    """Ensure required CLI tools are available and warn if not."""
    missing = []
    for tool in ('iperf3', 'tcpdump', 'tshark'):
        if shutil.which(tool) is None:
            missing.append(tool)
    if missing:
        info(f"WARNING: Required tools missing: {', '.join(missing)}\n")
        info('Please install them (e.g. `sudo apt install iperf3 tcpdump tshark`) and re-run.\n')


def create_congestion_network(controller_type, edge_count, senders_per_edge):
    os.system('sudo mn -c >/dev/null 2>&1')
    net = Mininet(controller=controller_type, link=TCLink, cleanup=True)

    print_header('Network Setup Phase')

    info('--> Adding controller\n')
    if controller_type == OVSController:
        net.addController('c0')
    else:
        net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6633)

    info('--> Adding core switch (s0) and edge switches (s1..sN)\n')
    core = net.addSwitch('s0')
    edge_switches = []
    for i in range(edge_count):
        sw = net.addSwitch(f's{i + 1}')
        edge_switches.append(sw)
        info(f'    - Added edge switch {sw.name}\n')

    info('--> Connecting edge switches to core (high capacity)\n')
    for sw in edge_switches:
        net.addLink(sw, core, bw=1000, delay=LINK_DELAY)
        info(f'    - Link: {sw.name} <--> {core.name} (1 Gbps)\n')

    info('--> Adding senders to edge switches\n')
    senders = []
    host_idx = 1
    for i, sw in enumerate(edge_switches):
        for j in range(senders_per_edge):
            h = net.addHost(f'h{host_idx}')
            net.addLink(h, sw, bw=SENDER_BW_MEG, delay=LINK_DELAY)
            senders.append(h)
            info(f'    - Host: {h.name} -> {sw.name} (~{SENDER_BW_MEG} Mbps)\n')
            host_idx += 1

    info('--> Adding receiver host attached to core (bottleneck)\n')
    receiver = net.addHost('hr')
    net.addLink(receiver, core, bw=CORE_TO_RECEIVER_BW_MEG, delay=LINK_DELAY)
    info(f'    - Bottleneck: {receiver.name} <--> {core.name} ({CORE_TO_RECEIVER_BW_MEG} Mbps)\n')

    info('--> Starting network\n')
    net.start()
    time.sleep(2)
    return net


def launch_tcpdump_on_hosts(net):
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    info('--> Starting tcpdump on each host (pcap -> /tmp/<host>.pcap)\n')
    for h in net.hosts:
        intf = h.defaultIntf()
        pcap = CAPTURE_DIR / f'{h.name}.pcap'
        cmd = f'tcpdump -i {intf} -U -n -w {pcap} >/dev/null 2>&1 &'
        h.cmd(cmd)
        info(f'    - tcpdump: {h.name} (intf {intf}) -> {pcap}\n')
    time.sleep(1)


def start_iperf_server(receiver):
    info(f"--> Starting iperf3 server on {receiver.name} ({receiver.IP()})\n")
    receiver.cmd('iperf3 -s >/dev/null 2>&1 &')
    time.sleep(1)


def launch_iperf_clients(senders, receiver, duration, bw_mbps):
    info('--> Launching iperf3 UDP clients (JSON output to /tmp/iperf_<host>.json)\n')
    for s in senders:
        out = CAPTURE_DIR / f'iperf_{s.name}.json'
        cmd = f'iperf3 -c {receiver.IP()} -u -b {bw_mbps}M -t {duration} -J > {out} 2>&1 &'
        s.cmd(cmd)
        info(f'    - {s.name} -> {receiver.name} : {bw_mbps}M (output: {out})\n')


def launch_cross_traffic(net, duration, rate_mbps):
    if not EXTRA_CROSS_TRAFFIC:
        return
    info('--> Launching extra cross-traffic flows between pairs (UDP)\n')
    senders = [h for h in net.hosts if h.name.startswith('h')]
    for i in range(0, len(senders), 2):
        src = senders[i]
        dst = senders[(i + 1) % len(senders)]
        out = CAPTURE_DIR / f'iperf_cross_{src.name}_to_{dst.name}.json'
        cmd = f'iperf3 -c {dst.IP()} -u -b {rate_mbps}M -t {duration} -J > {out} 2>&1 &'
        src.cmd(cmd)
        info(f'    - cross {src.name} -> {dst.name} : {rate_mbps}M (output: {out})\n')


def wait_for_traffic_completion(duration):
    info(f'--> Waiting for traffic to complete ({duration}s + margin)...\n')
    time.sleep(duration + 3)


def cleanup_background_processes():
    info('--> Killing iperf3 and tcpdump background processes (best effort)\n')
    os.system('killall iperf3 tcpdump >/dev/null 2>&1 || true')


def analyze_iperf_jsons_and_write_csv(csv_path):
    info('\n*** iperf3 JSON summaries (per-client) ***\n')
    rows = []
    for j in sorted(CAPTURE_DIR.glob('iperf_h*.json')):
        try:
            data = json.loads(j.read_text())
        except Exception as e:
            info(f'    - Failed to parse {j.name}: {e}\n')
            continue
        end = data.get('end', {})
        sum_stats = end.get('sum') or end.get('sum_received') or end.get('sum_sent')
        if sum_stats is None:
            info(f'    - {j.name}: no sum stats found in JSON\n')
            continue
        bps = sum_stats.get('bits_per_second', 0)
        lost = None
        if 'lost_percent' in sum_stats:
            lost = sum_stats.get('lost_percent')
        elif 'lost_packets' in sum_stats and 'packets' in sum_stats:
            try:
                lost = (sum_stats['lost_packets'] / sum_stats['packets']) * 100.0
            except Exception:
                lost = None
        mbps = float(bps) / 1e6 if bps else 0.0
        rows.append((j.stem.replace('iperf_', ''), bps, mbps, lost))
        info(f'    - {j.name}: {mbps:.3f} Mbps, loss%={lost}\n')

    try:
        with open(csv_path, 'w') as fo:
            fo.write('host,bits_per_second,mbps,lost_percent\n')
            for r in rows:
                fo.write(f'{r[0]},{r[1]},{r[2]},{r[3]}\n')
        info(f'--> Wrote CSV summary to {csv_path}\n')
    except Exception as e:
        info(f'--> Failed to write CSV {csv_path}: {e}\n')
    return rows


def run_tshark_analysis_for_pcaps():
    info('\n*** tshark analysis (pcap -> summaries) ***\n')
    for pcap in sorted(CAPTURE_DIR.glob('*.pcap')):
        out = CAPTURE_DIR / f'tshark_summary_{pcap.stem}.txt'
        info(f'    - Analyzing {pcap} -> {out}\n')
        try:
            conv_udp = subprocess.run(['tshark', '-r', str(pcap), '-q', '-z', 'conv,udp'],
                                      capture_output=True, text=True, timeout=20)
            io_stat = subprocess.run(['tshark', '-r', str(pcap), '-q', '-z', 'io,stat,1'],
                                     capture_output=True, text=True, timeout=20)
            with open(out, 'w') as fo:
                fo.write('=== conv,udp ===\n')
                fo.write(conv_udp.stdout + '\n')
                fo.write('=== io,stat,1 (I/O per 1s interval) ===\n')
                fo.write(io_stat.stdout + '\n')
            info(f'        - Wrote tshark analysis to {out}\n')
        except FileNotFoundError:
            info('        - tshark not installed; skipping.\n')
            break
        except subprocess.TimeoutExpired:
            info('        - tshark timed out while analyzing pcap.\n')


def maybe_open_wireshark(pcap_to_open):
    if shutil.which('wireshark') is None:
        info('--> wireshark not found in PATH; skipping GUI open.\n')
        return
    if os.environ.get('DISPLAY') is None:
        info('--> No DISPLAY environment (no X); skipping wireshark GUI open.\n')
        return
    try:
        info(f'--> Launching Wireshark on {pcap_to_open} (GUI)...\n')
        subprocess.Popen(['wireshark', str(pcap_to_open)])
    except Exception as e:
        info(f'--> Failed to launch Wireshark: {e}\n')


def run_traffic_and_analysis(net, duration, bw_mbps, open_wireshark):
    try:
        check_tools()
        launch_tcpdump_on_hosts(net)
        receiver = net.get('hr')
        start_iperf_server(receiver)

        senders = [h for h in net.hosts if h.name.startswith('h')]
        launch_iperf_clients(senders, receiver, duration, bw_mbps)
        launch_cross_traffic(net, duration, CROSS_TRAFFIC_RATE_M)

        wait_for_traffic_completion(duration)
        cleanup_background_processes()

        csv_path = CAPTURE_DIR / 'iperf_summary.csv'
        analyze_iperf_jsons_and_write_csv(csv_path)
        run_tshark_analysis_for_pcaps()

        if open_wireshark:
            maybe_open_wireshark(CAPTURE_DIR / 'hr.pcap')

        info('\n*** Analysis complete. Pcap, iperf JSON, and CSV in /tmp. ***\n')

    except Exception as ex:
        info(f'Error during traffic/analysis: {ex}\n')
        cleanup_background_processes()


def main():
    setLogLevel('info')

    parser = ArgumentParser()
    parser.add_argument('controller', nargs='?', default=None,
                        help='"remote" to use RemoteController')
    parser.add_argument('--quick', action='store_true',
                        help='Run a quick demo with fewer hosts & shorter duration')
    parser.add_argument('--open-wireshark', action='store_true',
                        help='Open Wireshark GUI on receiver pcap after run')
    args = parser.parse_args()

    controller = OVSController
    if args.controller == 'remote' or any('remote' in a for a in sys.argv[1:]):
        info('*** Detected remote flag. Using RemoteController.\n')
        controller = RemoteController
    else:
        info('*** No remote controller specified. Using default OVSController.\n')

    edge_count = EDGE_SWITCH_COUNT
    senders_per_edge = SENDERS_PER_EDGE
    duration = TRAFFIC_DURATION
    bw_mbps = SENDER_BW_MEG

    if args.quick:
        info('*** Quick demo mode enabled: smaller topology and shorter run.\n')
        edge_count = 1
        senders_per_edge = 2
        duration = 8
        bw_mbps = 6

    net = create_congestion_network(controller, edge_count, senders_per_edge)
    try:
        run_traffic_and_analysis(net, duration, bw_mbps, args.open_wireshark)

        info('\n' + '-' * 70 + '\n')
        info('*** Finished automated traffic+analysis run. Dropping to Mininet CLI. ***\n')
        info('*** Type "exit" to stop the network and cleanup. ***\n')
        info('-' * 70 + '\n\n')
        CLI(net)
    finally:
        try:
            net.stop()
        except Exception:
            pass
        cleanup_background_processes()


if __name__ == '__main__':
    main()
