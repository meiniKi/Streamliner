#!/usr/bin/env python3

import os
import argparse
import sys

from migen import *
from migen.genlib.misc import WaitTimer
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex.build.io import DDROutput

from litex_boards.platforms import colorlight_5a_75b, colorlight_5a_75e, colorlight_i5a_907

from litex.soc.cores.clock import *
from litex.soc.cores.spi_flash import ECP5SPIFlash
from litex.soc.cores.gpio import GPIOOut
from litex.soc.cores.led import LedChaser
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *

from litedram.modules import M12L16161A, M12L64322A
from litedram.phy import GENSDRPHY, HalfRateGENSDRPHY

from litex.soc.interconnect import wishbone

from litex.soc.cores.dma import WishboneDMAReader

from modules.udp_core import UdpCore

# CRG ----------------------------------------------------------------------------------------------

class _CRG(LiteXModule):
    def __init__(self, platform, sys_clk_freq, use_internal_osc=False, with_usb_pll=False, with_eth_pll=False, with_rst=True, sdram_rate="1:1"):
        self.rst    = Signal()
        self.cd_sys = ClockDomain()
        if sdram_rate == "1:2":
            self.cd_sys2x    = ClockDomain()
            self.cd_sys2x_ps = ClockDomain()
        else:
            self.cd_sys_ps = ClockDomain()

        # # #

        # Clk / Rst
        if not use_internal_osc:
            clk = platform.request("clk25")
            clk_freq = 25e6
        else:
            clk = Signal()
            div = 5
            self.specials += Instance("OSCG",
                                p_DIV = div,
                                o_OSC = clk)
            clk_freq = 310e6/div

        rst_n = 1 if not with_rst else platform.request("user_btn_n", 0)

        # PLL
        self.pll = pll = ECP5PLL()
        self.comb += pll.reset.eq(~rst_n | self.rst)
        pll.register_clkin(clk, clk_freq)
        pll.create_clkout(self.cd_sys,    sys_clk_freq)
        if sdram_rate == "1:2":
            pll.create_clkout(self.cd_sys2x,    2*sys_clk_freq)
            pll.create_clkout(self.cd_sys2x_ps, 2*sys_clk_freq, phase=180) # Idealy 90° but needs to be increased.
        else:
           pll.create_clkout(self.cd_sys_ps, sys_clk_freq, phase=180) # Idealy 90° but needs to be increased.

        # USB PLL
        if with_usb_pll:
            self.usb_pll = usb_pll = ECP5PLL()
            self.comb += usb_pll.reset.eq(~rst_n | self.rst)
            usb_pll.register_clkin(clk, clk_freq)
            self.cd_usb_12 = ClockDomain()
            self.cd_usb_48 = ClockDomain()
            usb_pll.create_clkout(self.cd_usb_12, 12e6, margin=0)
            usb_pll.create_clkout(self.cd_usb_48, 48e6, margin=0)

        # ETH PLL
        if with_eth_pll:
            self.eth_pll = eth_pll = ECP5PLL()
            self.comb += eth_pll.reset.eq(~rst_n | self.rst)
            eth_pll.register_clkin(clk, clk_freq)
            self.cd_eth_50  = ClockDomain()
            self.cd_eth_125 = ClockDomain()
            eth_pll.create_clkout(self.cd_eth_50,   50e6, margin=0)
            eth_pll.create_clkout(self.cd_eth_125, 125e6, margin=0)

        # SDRAM clock
        sdram_clk = ClockSignal("sys2x_ps" if sdram_rate == "1:2" else "sys_ps")
        self.specials += DDROutput(1, 0, platform.request("sdram_clock"), sdram_clk)

# BaseSoC ------------------------------------------------------------------------------------------

class BaseSoC(SoCCore):
    def __init__(self, board, revision, sys_clk_freq=60e6, toolchain="trellis",
        with_ethernet    = False,
        eth_ip           = "192.168.1.50",
        eth_phy          = 0,
        eth_subn         = "255.255.255.0",
        eth_mac          = "AE:00:00:00:00:00",
        with_led_chaser  = True,
        use_internal_osc = False,
        sdram_rate       = "1:1",
        with_spi_flash   = False,
        **kwargs):
        board = board.lower()
        assert board in ["5a-75b", "5a-75e", "i5a-907"]
        if board == "5a-75b":
            platform = colorlight_5a_75b.Platform(revision=revision, toolchain=toolchain)
        elif board == "5a-75e":
            platform = colorlight_5a_75e.Platform(revision=revision, toolchain=toolchain)
        elif board == "i5a-907":
            platform = colorlight_i5a_907.Platform(revision=revision, toolchain=toolchain)

        if board == "5a-75e" and revision == "6.0" and (with_ethernet):
            assert use_internal_osc, "You cannot use the 25MHz clock as system clock since it is provided by the Ethernet PHY and will stop during PHY reset."

        # CRG --------------------------------------------------------------------------------------
        with_rst     = kwargs["uart_name"] not in ["serial", "crossover"] # serial_rx shared with user_btn_n.
        if board == "i5a-907":
            with_rst = True
        with_usb_pll = kwargs.get("uart_name", None) == "usb_acm"
        with_eth_pll = with_ethernet

        self.crg = _CRG(platform, sys_clk_freq,
            use_internal_osc = use_internal_osc,
            with_usb_pll     = with_usb_pll,
            with_eth_pll     = with_eth_pll,
            with_rst         = with_rst,
            sdram_rate       = sdram_rate
        )

        # SoCCore ----------------------------------------------------------------------------------
        # Uartbone ---------------------------------------------------------------------------------
        if kwargs["with_uartbone"]:
            if board != "i5a-907":
                raise ValueError("uartbone only supported on i5a-907")

        SoCCore.__init__(self, platform, int(sys_clk_freq), ident="LiteX SoC on Colorlight " + board.upper(), **kwargs)

        # SDR SDRAM --------------------------------------------------------------------------------
        if not self.integrated_main_ram_size:
            sdrphy_cls = HalfRateGENSDRPHY if sdram_rate == "1:2" else GENSDRPHY
            self.sdrphy = sdrphy_cls(platform.request("sdram"), sys_clk_freq)
            if board == "5a-75e" and revision == "6.0":
                sdram_cls  = M12L64322A
            else:
                sdram_cls  = M12L16161A
            self.add_sdram("sdram",
                phy                     = self.sdrphy,
                module                  = sdram_cls(sys_clk_freq, sdram_rate),
                l2_cache_size           = kwargs.get("l2_size", 8192),
                l2_cache_full_memory_we = False,

            )

        # Ethernet / Etherbone ---------------------------------------------------------------------
        if with_ethernet:
            self.udp_rd_if = wishbone.Interface(
                data_width=self.bus.data_width,
                adr_width=self.bus.data_width,
                addressing="byte"
            )

            self.wb_udp_tx_dma = WishboneDMAReader(bus=self.udp_rd_if, with_csr=True)
            self.bus.add_master(name="udp_rd", master=self.udp_rd_if)

            self.upd_core = UdpCore(
                clock_pads = self.platform.request("eth_clocks", eth_phy),
                pads       = self.platform.request("eth", eth_phy),
                cd50       = self.crg.cd_sys,
                cd125      = self.crg.cd_eth_125,
                ip         = eth_ip,
                subnetmask = eth_subn,
                mac        = eth_mac
            )

        # Leds -------------------------------------------------------------------------------------
        # Disable leds when serial is used.
        if (platform.lookup_request("serial", loose=True) is None and with_led_chaser
            or board == "i5a-907"):
            self.leds = LedChaser(
                pads         = platform.request_all("user_led_n"),
                sys_clk_freq = sys_clk_freq)

        # SPI Flash --------------------------------------------------------------------------------
        if with_spi_flash:
            if board == "i5a-907":
                raise ValueError("SPI Flash chip is unknown on i5a-907, feel free to fix")
                # from litespi.modules import XXXXXX as SpiFlashModule
            elif board == "5a-75b" and revision == "6.0":
                raise ValueError("SPI Flash chip is unknown on 5A-75B v6.0, feel free to fix")
                # from litespi.modules import XXXXXX as SpiFlashModule
            elif board == "5a-75b" and revision == "6.1":
                # It's very possible that V6.0 uses this as well, but no documentation can be found for it
                from litespi.modules import GD25Q16C as SpiFlashModule
            # 5A-75B v7.0/v8.0 and all 5A-75Es seem to use W25Q32JV
            else:
                from litespi.modules import W25Q32JV as SpiFlashModule

            from litespi.opcodes import SpiNorFlashOpCodes
            self.mem_map["spiflash"] = 0x20000000
            self.add_spi_flash(mode="1x", module=SpiFlashModule(SpiNorFlashOpCodes.READ_1_1_1), with_master=False)


# Build --------------------------------------------------------------------------------------------

def main():
    from litex.build.parser import LiteXArgumentParser
    parser = LiteXArgumentParser(platform=colorlight_5a_75b.Platform, description="LiteX SoC on Colorlight 5A-75X.")
    parser.add_target_argument("--board",             default="5a-75b",         help="Board type (5a-75b, 5a-75e or i5a-907).")
    parser.add_target_argument("--revision",          default="7.0",            help="Board revision (6.0, 6.1, 7.0 or 8.0).")
    parser.add_target_argument("--sys-clk-freq",      default=60e6, type=float, help="System clock frequency.")
    ethopts = parser.target_group.add_mutually_exclusive_group()
    ethopts.add_argument("--with-ethernet",           action="store_true",          help="Enable Ethernet support.")
    parser.add_target_argument("--eth-ip",            default="192.168.1.50",       help="Ethernet/Etherbone IP address.")
    parser.add_target_argument("--eth-subnetmask",    default="255.255.255.0",      help="Ethernet/Etherbone Subnetmask.")
    parser.add_target_argument("--eth-mac",           default="AE:00:00:00:00:00",  help="Ethernet/Etherbone MAC.")
    parser.add_target_argument("--eth-phy",           default=0, type=int,          help="Ethernet PHY (0 or 1).")
    parser.add_target_argument("--use-internal-osc",  action="store_true",          help="Use internal oscillator.")
    parser.add_target_argument("--sdram-rate",        default="1:1",                help="SDRAM Rate (1:1 Full Rate or 1:2 Half Rate).")
    parser.add_target_argument("--with-spi-flash",    action="store_true",          help="Add SPI flash support to the SoC")
    args = parser.parse_args()

    soc = BaseSoC(board=args.board, revision=args.revision,
        sys_clk_freq     = args.sys_clk_freq,
        toolchain        = args.toolchain,
        with_ethernet    = args.with_ethernet,
        eth_ip           = args.eth_ip,
        eth_subn         = args.eth_subnetmask,
        eth_mac          = args.eth_mac,
        eth_phy          = args.eth_phy,
        use_internal_osc = args.use_internal_osc,
        sdram_rate       = args.sdram_rate,
        with_spi_flash   = args.with_spi_flash,
        **parser.soc_argdict
    )
    builder = Builder(soc, **parser.builder_argdict)

    if args.build:
        builder.build(**parser.toolchain_argdict)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(builder.get_bitstream_filename(mode="sram", ext=".svf")) # FIXME

if __name__ == "__main__":
    main()