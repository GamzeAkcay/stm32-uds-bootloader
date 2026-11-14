#!/usr/bin/env python3
"""Kullanım: python3 update_client.py stm32_app.bin [can0]"""
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
        raise TimeoutError("cevap gelmedi")

    def _send_isotp(self, payload):
        n = len(payload)
        if n <= 7:
            self._tx(bytes([n]) + payload)
            return
        self._tx(bytes([0x10 | (n >> 8), n & 0xFF]) + payload[:6])   # First Frame
        if self._rx(1.0)[0] >> 4 != 3:                               # Flow Control bekle
            raise RuntimeError("FC bekleniyordu")
        sn, pos = 1, 6
        while pos < n:                                               # Consecutive Frame'ler
            self._tx(bytes([0x20 | (sn & 0x0F)]) + payload[pos:pos + 7])
            sn += 1
            pos += 7
            time.sleep(0.001)

    def request(self, payload, timeout=1.0):
        while self.bus.recv(0) is not None:      # eski kareleri temizle
            pass
        self._send_isotp(payload)
        r = self._rx(timeout)
        if r[0] >> 4 != 0:
            raise RuntimeError("tek parça cevap bekleniyordu")
        r = r[1:1 + (r[0] & 0x0F)]
        if r[0] == 0x7F:
            raise RuntimeError(f"negatif cevap: servis 0x{r[1]:02X}, kod 0x{r[2]:02X}")
        return r


def main():
    path = sys.argv[1]
    channel = sys.argv[2] if len(sys.argv) > 2 else "can0"

    fw = open(path, "rb").read()
    fw += b"\xFF" * (-len(fw) % 4)               # 4'ün katına tamamla
    if not 0 < len(fw) <= APP_MAX:
        sys.exit(f"imaj boyutu geçersiz: {len(fw)} byte (en fazla {APP_MAX})")
    crc = zlib.crc32(fw) & 0xFFFFFFFF
    print(f"{len(fw)} byte, CRC32 = {crc:08X}")

    t = Tester(channel)

    print("bootloader bekleniyor (gerekirse kartı RESET'le)...")
    deadline = time.time() + 30
    while True:
        try:
            t.request(bytes([0x10, 0x02]), timeout=0.2)
            break
        except (TimeoutError, can.CanError):
            if time.time() > deadline:
                sys.exit("bootloader cevap vermedi")
            time.sleep(0.1)
    time.sleep(0.5)                              # birikmiş cevaplar bitsin
    t0 = time.time()
    print("programlama oturumu açıldı")

    t.request(bytes([0x34, 0x00, 0x44]) + struct.pack(">II", APP_START, len(fw)), timeout=30)
    print("silme tamam")

    blocks = (len(fw) + BLOCK - 1) // BLOCK
    for b in range(blocks):
        counter = (b + 1) & 0xFF
        r = t.request(bytes([0x36, counter]) + fw[b * BLOCK:(b + 1) * BLOCK])
        if r[0] != 0x76 or r[1] != counter:
            sys.exit(f"blok {b + 1}: beklenmeyen cevap {r.hex()}")
        if (b + 1) % 16 == 0 or b + 1 == blocks:
            print(f"  blok {b + 1}/{blocks}")

    t.request(bytes([0x37]))
    r = t.request(bytes([0x31, 0x01, 0xFF, 0x01]) + struct.pack(">I", crc))
    if r != bytes([0x71, 0x01, 0xFF, 0x01, 0x00]):
        sys.exit(f"CRC doğrulaması başarısız: {r.hex()}")
    print("CRC doğrulandı, imaj geçerli olarak işaretlendi")

    elapsed = time.time() - t0
    print(f"güncelleme süresi: {elapsed:.1f} s ({len(fw) / elapsed / 1024:.1f} KB/s)")

    t.request(bytes([0x11, 0x01]))
    print("reset gönderildi, uygulama yaklaşık 2 s içinde başlamalı")


main()
