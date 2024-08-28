

import os

from migen import *

from litex.gen import *

from litex import get_data_mod

from litex.soc.interconnect import wishbone
from litex.soc.interconnect.csr import *

from litex.soc.integration.soc import SoCRegion

from litex.soc.interconnect import stream

from functools import reduce

def udp_stream_descr():
    param_layout = [
        ("src_port",   16),
        ("dst_port",   16),
        ("ip_address", 32),
        ("length",     16)
    ]
    payload_layout = [
        ("data",       32)
    ]
    return stream.EndpointDescription(payload_layout, param_layout)

def str_ip4_to_num(x:str) -> int:
    return reduce(lambda x,y: x|y, map(lambda ix: (int(ix[1]) << (8 * ix[0])), enumerate(reversed(x.split(".")))))

class UdpCore(LiteXModule):
    def __init__(self, platform, eth_phy:int, cd50:ClockDomain, cd125:ClockDomain, mac:str, ip:str, subnetmask:str):
        self.mac = Constant(int(mac.replace(":", ""), 16))
        self.ip  = Constant(str_ip4_to_num(ip))
        self.sbm = Constant(str_ip4_to_num(subnetmask))

        self.sink = stream.Endpoint(udp_stream_descr())
        self.source = stream.Endpoint(udp_stream_descr())

        clock_pads = platform.request("eth_clocks", eth_phy)
        pads       = platform.request("eth", eth_phy)

        if cd50.name == "sys":
            pass
        else:
            raise NotImplementedError("TODO: CDC for eth")

        self.udp_core_params = dict(
            # generic
            i_clk50         = cd50.clk,
            i_rst50         = cd50.rst,
            # phy
            i_eth_tx_clk_in = cd125.clk,
            i_eth_tx_rst    = cd125.rst,
            i_eth_rx_clk    = clock_pads.rx,
            i_eth_rx_ctl    = pads.rx_ctl,
            i_eth_rx_data   = pads.rx_data,
            o_eth_tx_clk    = clock_pads.tx,
            o_eth_tx_ctl    = pads.tx_ctl,
            o_eth_tx_data   = pads.tx_data,
            # config
            i_mac           = self.mac,
            i_ip            = self.ip,
            i_subnetmask    = self.sbm,
            # upd in
            i_udp_in_fwd_valid      = self.sink.valid,
            i_udp_in_fwd_data       = self.sink.data,
            i_udp_in_fwd_last       = Constant(0b11), # TODO, only x*word-length now
            i_udp_in_fwd_last_valid = self.sink.last,
            i_udp_in_fwd_abort      = Constant(0b0),
            o_udp_in_ready          = self.sink.ready,

            i_udp_in_fwd_ip         = self.sink.ip_address,
            i_udp_in_fwd_length     = self.sink.length,
            i_udp_in_fwd_src_port   = self.sink.src_port,
            i_udp_in_fwd_dst_port   = self.sink.dst_port,

            # upd out
            o_udp_out_fwd_valid         = self.source.valid,
            o_udp_out_fwd_data          = self.source.data,
            #o_udp_out_fwd_last          = TODO
            o_udp_out_fwd_last_valid    = self.source.last,
            #o_udp_out_fwd_abort         = TODO
            i_udp_out_ready             = self.source.ready,

            o_udp_out_fwd_ip            = self.source.ip_address,
            o_udp_out_fwd_length        = self.source.length,
            o_udp_out_fwd_src_port      = self.source.src_port,
            o_udp_out_fwd_dst_port      = self.source.dst_port
        )

        # Add Verilog sources.
        # --------------------
        self.add_sources(platform)


    @staticmethod
    def add_sources(platform):
        core_files = "modules/verilog/udp"
        if not os.path.exists(core_files):
            raise NotImplementedError("TODO: implement generation from submodule")
        platform.add_source_dir(core_files)

    def do_finalize(self):
        self.specials += Instance("udpCore", **self.udp_core_params)