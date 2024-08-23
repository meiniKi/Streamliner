

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
    def __init__(self, pads:Record, clock_pads:Record, cd50:ClockDomain, cd125:ClockDomain, mac:str, ip:str, subnetmask:str):
        self.mac = Constant(int(mac.replace(":", ""), 16))
        self.ip  = Constant(str_ip4_to_num(ip))
        self.sbm = Constant(str_ip4_to_num(subnetmask))

        self.tx = stream.Endpoint(udp_stream_descr())
        self.rx = stream.Endpoint(udp_stream_descr())

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
            i_udp_in_fwd_valid      = self.tx.valid,
            i_udp_in_fwd_data       = self.tx.data,
            i_udp_in_fwd_last       = Constant(0b11), # TODO, only x*word-length now
            i_udp_in_fwd_last_valid = self.tx.last,
            i_udp_in_fwd_abort      = Constant(0b0),
            o_udp_in_ready          = self.tx.ready,

            i_udp_in_fwd_ip         = self.tx.ip_address,
            i_udp_in_fwd_length     = self.tx.length,
            i_udp_in_fwd_src_port   = self.tx.src_port,
            i_udp_in_fwd_dst_port   = self.tx.dst_port,

            # upd out
            o_udp_out_fwd_valid         = self.rx.valid,
            o_udp_out_fwd_data          = self.rx.data,
            o_udp_out_fwd_last          = Constant(0b11),
            o_udp_out_fwd_last_valid    = self.rx.last,
            o_udp_out_fwd_abort         = Constant(0b0),
            i_udp_out_ready             = self.rx.ready,

            o_udp_out_fwd_ip            = self.rx.ip_address,
            o_udp_out_fwd_length        = self.rx.length,
            o_udp_out_fwd_src_port      = self.rx.src_port,
            o_udp_out_fwd_dst_port      = self.rx.dst_port
        )

