"""Regenerate harmless PCAPs using documentation addresses and .test names."""
import ipaddress
import ssl
import struct


def checksum(data):
    if len(data) % 2:
        data += b"\0"
    value = sum(struct.unpack("!" + str(len(data) // 2) + "H", data))
    while value >> 16:
        value = (value & 0xffff) + (value >> 16)
    return (~value) & 0xffff


def frame(src, dst, protocol, payload, identifier=1):
    source, target = ipaddress.ip_address(src).packed, ipaddress.ip_address(dst).packed
    require_ipv4 = len(source) == len(target) == 4
    if require_ipv4:
        ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), identifier, 0, 64, protocol, 0, source, target)
        ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
        kind = b"\x08\x00"
    else:
        assert len(source) == len(target) == 16
        ip = struct.pack("!IHBB16s16s", 6 << 28, len(payload), protocol, 64, source, target)
        kind = b"\x86\xdd"
    return bytes.fromhex("020000000002020000000001") + kind + ip + payload


def transport(src, dst, sport, dport, payload, seq=None, ack=0, flags=0x18):
    source, target = ipaddress.ip_address(src).packed, ipaddress.ip_address(dst).packed
    protocol = 17 if seq is None else 6
    if protocol == 17:
        packet = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload
        offset = 6
    else:
        packet = struct.pack("!HHIIBBHHH", sport, dport, seq, ack, 5 << 4, flags, 32768, 0, 0) + payload
        offset = 16
    pseudo = source + target + (struct.pack("!BBH", 0, protocol, len(packet)) if len(source) == 4
                                else struct.pack("!I3xB", len(packet), protocol))
    value = checksum(pseudo + packet) or 0xffff
    return packet[:offset] + struct.pack("!H", value) + packet[offset + 2:]


def pcap(frames):
    value = struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1)
    for index, packet in enumerate(frames):
        value += struct.pack("<IIII", 1_700_000_000 + index // 100, (index % 100) * 1000, len(packet), len(packet)) + packet
    return value


def tcp_flow(client, server, port, request, response=b""):
    packets = []
    def append(src, dst, sport, dport, seq, ack, flags, body=b""):
        packets.append(frame(src, dst, 6, transport(src, dst, sport, dport, body, seq, ack, flags), len(packets) + 10))
    append(client, server, 53001, port, 100, 0, 0x02)
    append(server, client, port, 53001, 200, 101, 0x12)
    append(client, server, 53001, port, 101, 201, 0x10)
    append(client, server, 53001, port, 101, 201, 0x18, request)
    if response:
        append(server, client, port, 53001, 201, 101 + len(request), 0x18, response)
    append(client, server, 53001, port, 101 + len(request), 201 + len(response), 0x11)
    append(server, client, port, 53001, 201 + len(response), 102 + len(request), 0x11)
    append(client, server, 53001, port, 102 + len(request), 202 + len(response), 0x10)
    return packets


def samples():
    echo = struct.pack("!BBHHH", 8, 0, 0, 1, 1) + b"harmless-fixture"
    echo = echo[:2] + struct.pack("!H", checksum(echo)) + echo[4:]
    positive = [frame("198.51.100.5", "192.0.2.10", 1, echo)]
    for i in range(14):
        positive.append(frame("198.51.100.6", "192.0.2.10", 6,
                              transport("198.51.100.6", "192.0.2.10", 54000 + i, 22, b"", 10 + i, 0, 0x02), i + 1))
    question = b"".join(bytes([len(x)]) + x.encode() for x in "fixture.example.test".split(".")) + b"\0" + struct.pack("!HH", 1, 1)
    query = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0) + question
    response = (struct.pack("!HHHHHH", 0x1234, 0x8180, 1, 1, 0, 0) + question +
                b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + bytes([192, 0, 2, 99]))
    client, server = "192.0.2.10", "192.0.2.53"
    negative = [frame(client, server, 17, transport(client, server, 53000, 53, query)),
                frame(server, client, 17, transport(server, client, 53, 53000, response))]
    negative += tcp_flow(client, "192.0.2.80", 80, b"GET / HTTP/1.1\r\nHost: fixture.example.test\r\nConnection: close\r\n\r\n",
                         b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
    # TLS is generated locally through memory BIOs. No socket or remote handshake.
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    context = ssl.create_default_context()
    connection = context.wrap_bio(incoming, outgoing, server_side=False, server_hostname="fixture.example.test")
    try:
        connection.do_handshake()
    except ssl.SSLWantReadError:
        pass
    negative += tcp_flow(client, "192.0.2.81", 443, outgoing.read())
    src6, dst6 = "2001:db8::10", "2001:db8::53"
    negative += [frame(src6, dst6, 17, transport(src6, dst6, 53000, 53, query))]
    return {"positive": pcap(positive), "negative": pcap(negative)}
