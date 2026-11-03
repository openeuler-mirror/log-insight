"""pcap 分片端口回归：通过真实解析入口验证，不依赖 DB/Redis。"""
import struct

import pytest

from app.modules.perf.netcap import analyze_netcap


def _ipv4_packet(proto: int, fragment: int, payload: bytes) -> bytes:
    header = struct.pack(
        ">BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 42, fragment,
        64, proto, 0, b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
    )
    checksum = sum(struct.unpack(">10H", header))
    while checksum >> 16:
        checksum = (checksum & 0xFFFF) + (checksum >> 16)
    return header[:10] + struct.pack(">H", ~checksum & 0xFFFF) + header[12:] + payload


def _pcap(packets: list[bytes], network: int, endian: str) -> bytes:
    data = struct.pack(endian + "IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, network)
    for i, packet in enumerate(packets):
        if network == 1:
            packet = b"\x00" * 12 + b"\x08\x00" + packet
        data += struct.pack(endian + "IIII", 100 + i, 0, len(packet), len(packet))
        data += packet
    return data


@pytest.mark.parametrize("proto", [6, 17], ids=["tcp", "udp"])
@pytest.mark.parametrize("network", [1, 101], ids=["ethernet", "raw-ip"])
@pytest.mark.parametrize("endian", ["<", ">"], ids=["little", "big"])
@pytest.mark.parametrize("more_fragments", [False, True], ids=["last", "middle"])
def test_non_initial_fragment_does_not_invent_ports(proto, network, endian, more_fragments):
    # 后续分片承载应用数据；ABCD 不应成为端口 16706 / 17220。
    fragment = 3 | (0x2000 if more_fragments else 0)
    packet = _ipv4_packet(proto, fragment, b"ABCDEFGH")
    result = analyze_netcap(_pcap([packet], network, endian))

    assert result["packet_count"] == 1
    assert result["byte_count"] == len(packet) + (14 if network == 1 else 0)
    assert result["conversations"] == [{
        "src": "10.0.0.1", "sport": 0, "dst": "10.0.0.2", "dport": 0,
        "proto": "TCP" if proto == 6 else "UDP", "packets": 1,
        "bytes": result["byte_count"],
    }]


@pytest.mark.parametrize("proto", [6, 17], ids=["tcp", "udp"])
@pytest.mark.parametrize("fragment", [0, 0x2000, 0x4000], ids=["unfragmented", "first", "dont-fragment"])
def test_zero_offset_retains_transport_ports(proto, fragment):
    transport = (struct.pack(">HHIIHHHH", 1234, 443, 0, 0, 0x5000, 0, 0, 0)
                 if proto == 6 else struct.pack(">HHHH", 1234, 443, 16, 0))
    payload = b"ABCDEFGHIJKL" if proto == 6 else b"ABCDEFGH"
    packet = _ipv4_packet(proto, fragment, transport + payload)
    result = analyze_netcap(_pcap([packet], 1, "<"))

    assert result["conversations"][0]["sport"] == 1234
    assert result["conversations"][0]["dport"] == 443


def test_fragmented_udp_capture_retains_all_packet_totals():
    first = _ipv4_packet(17, 0x2000, struct.pack(">HHHH", 1234, 53, 24, 0) + b"ABCDEFGH")
    last = _ipv4_packet(17, 2, b"IJKLMNOP")
    result = analyze_netcap(_pcap([first, last], 101, "<"))

    assert result["packet_count"] == 2
    assert result["byte_count"] == len(first) + len(last)
    assert result["protocols"] == [{"name": "UDP", "packets": 2, "bytes": len(first) + len(last)}]
    assert {(c["sport"], c["dport"]) for c in result["conversations"]} == {(1234, 53), (0, 0)}
