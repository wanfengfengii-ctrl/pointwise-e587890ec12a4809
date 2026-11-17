import re
from httpdata.response import body_framing, parse_field


class TransferDecodeError(ValueError):
    pass


def decode_chunked(wire):
    output = bytearray()
    position = 0
    while True:
        end = wire.find(b"\r\n", position)
        if end < 0 or not re.fullmatch(rb"[0-9A-Fa-f]+", wire[position:end]):
            raise TransferDecodeError("invalid chunk size")
        size = int(wire[position:end], 16)
        position = end + 2
        if size == 0:
            while True:
                end = wire.find(b"\r\n", position)
                if end < 0:
                    raise TransferDecodeError("truncated trailer")
                line = wire[position:end]
                position = end + 2
                if not line:
                    if position != len(wire):
                        raise TransferDecodeError("bytes after chunked body")
                    return bytes(output)
                name, _ = parse_field(line)
                if name.lower() in {"content-length", "transfer-encoding", "content-encoding", "content-type"}:
                    raise TransferDecodeError("framing or interpretation field in trailer")
        if size > len(wire) - position or wire[position + size:position + size + 2] != b"\r\n":
            raise TransferDecodeError("truncated chunk or missing CRLF")
        output.extend(wire[position:position + size])
        position += size + 2


def decode_transfer(response):
    kind, length = body_framing(response.headers)
    if kind == "chunked":
        return decode_chunked(response.wire_body)
    if len(response.wire_body) != length:
        raise TransferDecodeError("HTTP body length mismatch")
    return response.wire_body
