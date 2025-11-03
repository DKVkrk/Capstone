#!/usr/bin/env python3

"""
A custom Ryu controller for an AI-Driven TE project.
This controller combines two functions:
1.  A simple L2 learning switch (like simple_switch).
2.  A periodic statistics monitor that prints port stats.
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_0
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib import hub
from operator import attrgetter

class ProjectController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_0.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ProjectController, self).__init__(*args, **kwargs)
        # MAC-to-port table for L2 switching
        self.mac_to_port = {}
        # List of connected switches (datapaths)
        self.datapaths = []
        # Start the monitoring thread
        self.monitor_thread = hub.spawn(self._monitor)
        self.logger.info("Project Controller Started...")

    # ===== L2 Learning Switch Logic =====

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """
        Handle switch connection. Install default flow (table-miss flow).
        """
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Add this datapath to our list for monitoring
        if datapath not in self.datapaths:
            self.datapaths.append(datapath)

        # Install table-miss flow entry (sends all packets to controller)
        match = parser.OFPMatch()
        
        # FIX: max_len should be 0 (not OFP_NO_BUFFER)
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          0)]
        
        self.add_flow(datapath, 0, match, actions)
        self.logger.info(f"Switch {datapath.id} connected. Default flow installed.")

    def add_flow(self, datapath, priority, match, actions):
        """
        Helper function to add a flow entry to a switch.
        """
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        mod = parser.OFPFlowMod(datapath=datapath, match=match,
                               cookie=0, command=ofproto.OFPFC_ADD,
                               idle_timeout=0, hard_timeout=0,
                               priority=priority, actions=actions)
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        """
        Handle incoming packets (PacketIn messages).
        """
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.in_port

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        dpid = datapath.id
        
        # learn a mac address to avoid FLOOD next time.
        self_mac_to_port = self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][eth.src] = in_port

        # determine output port
        if eth.dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][eth.dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # install a flow to avoid packet_in next time
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, dl_dst=eth.dst)
            self.add_flow(datapath, 1, match, actions)

        # Send packet out
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)

    # ===== Statistics Monitor Logic =====

    def _monitor(self):
        """
        Monitoring thread. Runs in the background.
        """
        while True:
            # Wait for 10 seconds before polling again
            hub.sleep(10)
            self.logger.info("Polling switches for statistics...")
            for dp in self.datapaths:
                self._request_stats(dp)

    def _request_stats(self, datapath):
        """
        Send a port stats request to a switch.
        """
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Request stats for all ports
        req = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_NONE)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        """
        Handle the reply from a switch.
        """
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        
        # Log the stats
        self.logger.info(f"\n===== PORT STATS FOR SWITCH {dpid} =====")
        for stat in sorted(body, key=attrgetter('port_no')):
            # Skip virtual "local" port
            if stat.port_no == ofproto_v1_0.OFPP_LOCAL:
                continue
            self.logger.info(f" Port {stat.port_no}: "
                             f" RX Pkts: {stat.rx_packets:<8} |"
                             f" TX Pkts: {stat.tx_packets:<8} |"
                             f" RX Bytes: {stat.rx_bytes:<8} |"
                             f" TX Bytes: {stat.tx_bytes:<8} |"
                             f" RX Drops: {stat.rx_dropped:<5} |"
                             f" TX Drops: {stat.tx_dropped:<5}")
        self.logger.info(f"======================================\n")