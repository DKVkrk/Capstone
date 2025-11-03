# static_path_controller.py
# A corrected, robust Ryu application that installs static flow rules for a 
# predictable path while also handling general L2 traffic (like ARP) to 
# prevent crashes.

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types

class StaticPathController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(StaticPathController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.switches = {} # To store datapath objects
        self.paths_installed = False # Flag to ensure paths are installed only once

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        dpid = datapath.id
        self.switches[dpid] = datapath
        self.mac_to_port.setdefault(dpid, {})
        self.logger.info(f"*** Switch {dpid:016x} connected.")
        
        # Install a default table-miss flow entry to send unknown packets to the controller
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

        # Once all 8 switches have connected, install the static paths
        if len(self.switches) == 8 and not self.paths_installed:
            self.logger.info("\n*** All 8 switches connected. Installing static flow rules. ***\n")
            self.install_static_paths()
            self.paths_installed = True

    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        """
        Helper function to add a flow entry.
        CORRECTED: Now correctly handles the optional buffer_id.
        """
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst)
        datapath.send_msg(mod)

    def install_static_paths(self):
        """Installs hard-coded paths for CSE <--> ECE traffic via core switch c1."""
        
        # DPIDs from your college_topology.py script
        d1_dpid, c1_dpid, d2_dpid = 257, 1, 513
        
        d1 = self.switches.get(d1_dpid)
        c1 = self.switches.get(c1_dpid)
        d2 = self.switches.get(d2_dpid)
        
        if not all([d1, c1, d2]):
            self.logger.error("Could not find all required switches (d1, c1, d2). Aborting static path installation.")
            return

        parser_d1, parser_c1, parser_d2 = d1.ofproto_parser, c1.ofproto_parser, d2.ofproto_parser
        
        # IP subnets from your college_topology.py script
        cse_subnet = '10.0.1.0/24'
        ece_subnet = '10.0.2.0/24'

        # Path: CSE -> ECE via c1
        # Port numbers based on link creation order in college_topology.py
        # d1 -> c1 is on d1's port 1
        # c1 -> d2 is on c1's port 3
        match = parser_d1.OFPMatch(eth_type=0x0800, ipv4_dst=ece_subnet)
        actions = [parser_d1.OFPActionOutput(1)]
        self.add_flow(d1, 10, match, actions)
        
        match = parser_c1.OFPMatch(in_port=2, eth_type=0x0800, ipv4_dst=ece_subnet)
        actions = [parser_c1.OFPActionOutput(3)]
        self.add_flow(c1, 10, match, actions)

        # Path: ECE -> CSE via c1 (Return Path)
        # d2 -> c1 is on d2's port 1
        # c1 -> d1 is on c1's port 2
        match = parser_d2.OFPMatch(eth_type=0x0800, ipv4_dst=cse_subnet)
        actions = [parser_d2.OFPActionOutput(1)]
        self.add_flow(d2, 10, match, actions)
        
        match = parser_c1.OFPMatch(in_port=3, eth_type=0x0800, ipv4_dst=cse_subnet)
        actions = [parser_c1.OFPActionOutput(2)]
        self.add_flow(c1, 10, match, actions)
        
        self.logger.info("*** Static path rules installed: CSE <--> ECE via c1. ***")
        self.logger.info("*** The path through c2 is now idle, ready for the AI agent. ***\n")

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        """
        This is the complete, working L2 learning switch logic. It handles any
        packet that does not match our high-priority static rules (e.g., ARP).
        """
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        # --- BUG FIX ---
        # Ignore packets that are not standard Ethernet frames.
        # This prevents crashes when receiving non-Ethernet protocols.
        if not eth:
            return

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            # ignore lldp packet
            return
            
        dst = eth.dst
        src = eth.src
        dpid = datapath.id

        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # install a flow to avoid packet_in next time
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            # We don't want to install a flow for our statically routed traffic,
            # so we only install flows with a lower priority.
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, 1, match, actions, msg.buffer_id)
                return
            else:
                self.add_flow(datapath, 1, match, actions)

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)

