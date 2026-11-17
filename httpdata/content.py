import gzip
import zlib


class ContentDecodeError(ValueError):
    pass


def decode_content(payload, encoding="identity"):
    encoding = encoding.strip().lower()
    if encoding == "identity":
        return payload
    if encoding != "gzip":
        raise ContentDecodeError("unsupported content encoding")
    if not payload.startswith(b"\x1f\x8b"):
        raise ContentDecodeError("missing gzip member")
    try:
        return gzip.decompress(payload)
    except (OSError, EOFError, zlib.error) as exc:
        raise ContentDecodeError("invalid or incomplete gzip") from exc
