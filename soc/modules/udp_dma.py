#
# This file is part of LiteX.
#
# Copyright (c) 2020-2021 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

"""UDP Direct Memory Access (DMA) TX."""

from migen import *

from litex.gen import *
from litex.gen.common import reverse_bytes

from litex.soc.interconnect.csr import *
from litex.soc.interconnect import stream
from litex.soc.interconnect import wishbone

from modules.udp_core import udp_stream_descr

# Helpers ------------------------------------------------------------------------------------------

def format_bytes(s, endianness):
    return {"big": s, "little": reverse_bytes(s)}[endianness]

# UdpWishboneDMAReader --------------------------------------------------------------------------------

class UdpWishboneDMAReader(LiteXModule):
    """Read data from Wishbone MMAP memory.

    For every address written to the sink, one word will be produced on the source.

    Parameters
    ----------
    bus : bus
        Wishbone bus of the SoC to read from.

    Attributes
    ----------
    sink : Record("address")
        Sink for MMAP addresses to be read.

    source : Record("data")
        Source for MMAP word results from reading.
    """
    def __init__(self, bus, endianness="little", fifo_depth=16):
        assert isinstance(bus, wishbone.Interface)
        self.bus            = bus
        self.sink           = sink          = stream.Endpoint([("address", bus.adr_width, ("last", 1))])
        self.source_fifo    = source_fifo   = stream.Endpoint([("data",    bus.data_width)])
        self.source         = source        = stream.Endpoint(udp_stream_descr())

        self.add_csr()

        # # #

        # FIFO..
        self.fifo = fifo = stream.SyncFIFO([("data", bus.data_width)], depth=fifo_depth)

        # Reads -> FIFO.
        self.comb += [
            bus.stb.eq(sink.valid & fifo.sink.ready),
            bus.cyc.eq(sink.valid & fifo.sink.ready),
            bus.we.eq(0),
            bus.sel.eq(2**(bus.data_width//8)-1),
            bus.adr.eq(sink.address),
            fifo.sink.last.eq(sink.last),
            fifo.sink.data.eq(format_bytes(bus.dat_r, endianness)),
            If(bus.stb & bus.ack,
                sink.ready.eq(1),
                fifo.sink.valid.eq(1),
            ),
        ]

        # FIFO -> Output.
        self.comb += fifo.source.connect(source_fifo)

        # Add parameter to Endpoint
        #self.comb += source.connect(source_fifo, omit=[]) # TODO use omit
        self.comb += [
            source.valid.eq(source_fifo.valid),
            source.ready.eq(source_fifo.ready),
            source.first.eq(source_fifo.first),
            source.last.eq(source_fifo.last),
            source.payload.data.eq(source_fifo.payload.data),
            
            source.param.src_port.eq(self._srcdst_port.storage[ 0:15]),
            source.param.dst_port.eq(self._srcdst_port.storage[16:31]),
            source.param.ip_address.eq(self._dst_ip.storage),
            source.param.length.eq(self._length.storage)
        ]

    def add_csr(self, default_base=0, default_length=0, default_enable=0, default_loop=0):
        self._base          = CSRStorage(64, reset=default_base)
        self._length        = CSRStorage(32, reset=default_length)
        self._enable        = CSRStorage(reset=default_enable)
        self._done          = CSRStatus()
        self._loop          = CSRStorage(reset=default_loop)
        self._offset        = CSRStatus(32)
        self._srcdst_port   = CSRStorage(32)
        self._dst_ip        = CSRStorage(32)
        
        # # #

        shift   = log2_int(self.bus.data_width//8)
        base    = Signal(self.bus.adr_width)
        offset  = Signal(self.bus.adr_width)
        length  = Signal(self.bus.adr_width)
        self.comb += base.eq(self._base.storage[shift:])
        self.comb += length.eq(self._length.storage[shift:])

        self.comb += self._offset.status.eq(offset)

        self.fsm = fsm = ResetInserter()(FSM(reset_state="IDLE"))
        self.comb += fsm.reset.eq(~self._enable.storage)
        fsm.act("IDLE",
            NextValue(offset, 0),
            NextState("RUN"),
        )
        fsm.act("RUN",
            self.sink.valid.eq(1),
            self.sink.last.eq(offset == (length - 1)),
            self.sink.address.eq(base + offset),
            If(self.sink.ready,
                NextValue(offset, offset + 1),
                If(self.sink.last,
                    If(self._loop.storage,
                        NextValue(offset, 0)
                    ).Else(
                        NextState("DONE")
                    )
                )
            )
        )
        fsm.act("DONE", self._done.status.eq(1))

