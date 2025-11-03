# traffic_engineering_controller.py (LITE VERSION)
#
# This controller does NOT do L2 learning. It ONLY:
# 1. Collects statistics for the DRL agent.
# 2. Provides the REST API for the DRL agent.

import json
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

# Imports for REST API
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from webob import Response

CONTROLLER_INSTANCE_NAME = 'te_controller_app'

class TrafficEngineeringController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(TrafficEngineeringController, self).__init__(*args, **kwargs)
        
        # --- Statistics Monitoring ---
        self.datapaths = {}
        self.port_stats = {} # Stores port statistics
        self.flow_stats = {} # Stores flow statistics
        self.monitor_thread = hub.spawn(self._monitor)
        
        # --- REST API ---
        self.wsgi = kwargs['wsgi']
        self.wsgi.register(RestController, {CONTROLLER_INSTANCE_NAME: self})

    # -------------------------------------------------------------------
    # --- NO L2 LEARNING LOGIC (NO _packet_in_handler) ---
    # -------------------------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id

        self.datapaths[dpid] = datapath
        self.port_stats.setdefault(dpid, {})
        self.flow_stats.setdefault(dpid, {})

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions) # Priority 0 (lowest)
        self.logger.info("Switch %d connected and table-miss rule installed.", dpid)

    # Helper function to add flow entries
    def add_flow(self, datapath, priority, match, actions, buffer_id=None, idle_timeout=0, hard_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    idle_timeout=idle_timeout,
                                    hard_timeout=hard_timeout,
                                    instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match,
                                    idle_timeout=idle_timeout,
                                    hard_timeout=hard_timeout,
                                    instructions=inst)
        datapath.send_msg(mod)


    # -------------------------------------------------------------------
    # --- 2. Statistics Monitoring Logic ---
    # -------------------------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        dpid = datapath.id
        if ev.state == MAIN_DISPATCHER:
            if dpid not in self.datapaths:
                self.datapaths[dpid] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if dpid in self.datapaths:
                del self.datapaths[dpid]

    def _monitor(self):
        while True:
            for dp in self.datapaths.values():
                self._request_stats(dp)
            hub.sleep(5) # Request stats every 5 seconds

    def _request_stats(self, datapath):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        req = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)
        req = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        self.flow_stats[dpid] = body

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        self.port_stats[dpid] = body

# -------------------------------------------------------------------
# --- 3. REST API Logic (For DRL Agent) ---
# --- THIS SECTION CONTAINS THE FIXES ---
# -------------------------------------------------------------------

class RestController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(RestController, self).__init__(req, link, data, **config)
        self.controller_app = data[CONTROLLER_INSTANCE_NAME]

    @route('stats', '/network_state', methods=['GET'])
    def get_network_state(self, req, **kwargs):
        # FIX: Use Response(json=...) to correctly format the body
        return Response(json=self.controller_app.port_stats)

    @route('action', '/reroute_flow', methods=['POST'])
    def reroute_flow(self, req, **kwargs):
        try:
            data = req.json
            dpid = data.get('dpid')
            priority = data.get('priority', 10)
            match_fields = data.get('match', {})
            action_fields = data.get('actions', [])

            datapath = self.controller_app.datapaths.get(dpid)
            if not datapath:
                # FIX: Use Response(json=...) for error messages
                return Response(status=404, json={"error": "Datapath not found."})

            parser = datapath.ofproto_parser
            match = parser.OFPMatch(**match_fields)
            
            actions = []
            for a in action_fields:
                if a.get('type') == 'OUTPUT':
                    actions.append(parser.OFPActionOutput(a.get('port')))
            
            self.controller_app.add_flow(datapath, priority, match, actions, hard_timeout=10)
            
            # FIX: Use Response(json=...) for success messages
            return Response(json={'status': 'success', 'message': 'Flow rule added.'})

        except Exception as e:
            self.controller_app.logger.error("Error in /reroute_flow: %s", e)
            # FIX: Use Response(json=...) for error messages
            return Response(status=500, json={"error": str(e)})




