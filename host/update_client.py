#!/usr/bin/env python3
"""Usage: python3 update_client.py stm32_app.bin [can0]"""
import sys, time, zlib, struct
import can

REQ_ID, RESP_ID = 0x7E0, 0x7E8
APP_START = 0x08008000
APP_MAX = 0x080E0000 - APP_START
BLOCK = 64


class Tester:
    def __init__(self, channel):
        self.bus = can.interface.Bus(channel=channel, bustype="socketcan")

    def _tx(self, data):
        self.bus.send(can.Message(arbitration_id=REQ_ID, data=data, is_extended_id=False))

    def _rx(self, timeout):
        end = time.time() + timeout
        while time.time() < end:
            m = self.bus.recv(0.05)
            if m is not None and m.arbitration_id == RESP_ID:
                return bytes(m.data)
        raise TimeoutError("no response")

    def _send_isotp(self, payload):
        n = len(payload)
        if n <= 7:
            self._tx(bytes([n]) + payload)
            return
        self._tx(bytes([0x10 | (n >> 8), n & 0xFF]) + payload[:6])   # First Frame
        if self._rx(1.0)[0] >> 4 != 3:                               # wait for Flow Control
            raise RuntimeError("expected Flow Control")
        sn, pos = 1, 6
        while pos < n:                                               # Consecutive Frames
            self._tx(bytes([0x20 | (sn & 0x0F)]) + payload[pos:pos + 7])
            sn += 1
            pos += 7

    def request(self, payload, timeout=1.0):
        while self.bus.recv(0) is not None:      # flush old frames
            pass
        self._send_isotp(payload)
        r = self._rx(timeout)
        if r[0] >> 4 != 0:
            raise RuntimeError("expected a single-frame response")
        r = r[1:1 + (r[0] & 0x0F)]
        if r[0] == 0x7F:
            raise RuntimeError(f"negative response: service 0x{r[1]:02X}, code 0x{r[2]:02X}")
        return r


def main():
    path = sys.argv[1]
    channel = sys.argv[2] if len(sys.argv) > 2 else "can0"

    fw = open(path, "rb").read()
    fw += b"\xFF" * (-len(fw) % 4)               # pad to a multiple of 4
    if not 0 < len(fw) <= APP_MAX:
        sys.exit(f"invalid image size: {len(fw)} bytes (max {APP_MAX})")
    crc = zlib.crc32(fw) & 0xFFFFFFFF
    print(f"{len(fw)} bytes, CRC32 = {crc:08X}")

    t = Tester(channel)

    print("waiting for the bootloader (press RESET if needed)...")
    deadline = time.time() + 30
    while True:
        try:
            t.request(bytes([0x10, 0x02]), timeout=0.2)
            break
        except (TimeoutError, can.CanError):
            if time.time() > deadline:
                sys.exit("no response from the bootloader")
            time.sleep(0.1)
    time.sleep(0.5)                              # let pending responses drain
    t0 = time.time()
    print("programming session opened")

    t.request(bytes([0x34, 0x00, 0x44]) + struct.pack(">II", APP_START, len(fw)), timeout=30)
    print("erase done")

    blocks = (len(fw) + BLOCK - 1) // BLOCK
    for b in range(blocks):
        counter = (b + 1) & 0xFF
        r = t.request(bytes([0x36, counter]) + fw[b * BLOCK:(b + 1) * BLOCK])
        if r[0] != 0x76 or r[1] != counter:
            sys.exit(f"block {b + 1}: unexpected response {r.hex()}")
        if (b + 1) % 16 == 0 or b + 1 == blocks:
            print(f"  block {b + 1}/{blocks}")

    t.request(bytes([0x37]))
    r = t.request(bytes([0x31, 0x01, 0xFF, 0x01]) + struct.pack(">I", crc))
    if r != bytes([0x71, 0x01, 0xFF, 0x01, 0x00]):
        sys.exit(f"CRC verification failed: {r.hex()}")
    print("CRC verified, image marked as valid")

    elapsed = time.time() - t0
    print(f"update time: {elapsed:.1f} s ({len(fw) / elapsed / 1024:.1f} KB/s)")

    t.request(bytes([0x11, 0x01]))
    print("reset sent, the application should start in about 2 s")


main()
