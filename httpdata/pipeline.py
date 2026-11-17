import hashlib
import json
from dataclasses import dataclass
from httpdata.response import parse_response, header_values, body_framing, HttpResponseError
from httpdata.transfer import decode_transfer
from httpdata.content import decode_content
from httpdata.text import extract_text


def sha256(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class InterpretedResponse:
    raw: bytes
    status: int
    headers: tuple
    transfer_body: bytes
    body: bytes
    text: str

    def evidence(self):
        text_bytes = self.text.encode("utf-8")
        return {
            "status": self.status, "headers": self.headers,
            "raw_size": len(self.raw), "raw_digest": sha256(self.raw),
            "transfer_size": len(self.transfer_body), "transfer_digest": sha256(self.transfer_body),
            "body_size": len(self.body), "body_digest": sha256(self.body),
            "text_size": len(text_bytes), "text_digest": sha256(text_bytes),
        }


def interpretation_fingerprint(value):
    evidence = value.evidence() if isinstance(value, InterpretedResponse) else value
    return sha256(json.dumps(evidence, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii"))


def interpret_response(raw):
    response = parse_response(raw)
    encodings = header_values(response.headers, "content-encoding")
    types = header_values(response.headers, "content-type")
    if len(encodings) > 1 or len(types) > 1:
        raise HttpResponseError("duplicate interpretation field")
    transfer = decode_transfer(response)
    body = decode_content(transfer, encodings[0] if encodings else "identity")
    text = extract_text(body, types[0] if types else None)
    return InterpretedResponse(raw, response.status, response.headers, transfer, body, text)


def interpret_revisit(raw, transfer_body):
    if not isinstance(transfer_body, bytes):
        raise HttpResponseError("inherited body must be bytes")
    response = parse_response(raw)
    if response.wire_body:
        raise HttpResponseError("revisit record carries body bytes")
    kind, length = body_framing(response.headers)
    if kind != "length":
        raise HttpResponseError("revisit boundary must be Content-Length")
    if length != len(transfer_body):
        raise HttpResponseError("revisit length differs from inherited body")
    encodings = header_values(response.headers, "content-encoding")
    types = header_values(response.headers, "content-type")
    if len(encodings) > 1 or len(types) > 1:
        raise HttpResponseError("duplicate interpretation field")
    body = decode_content(transfer_body, encodings[0] if encodings else "identity")
    text = extract_text(body, types[0] if types else None)
    return InterpretedResponse(raw, response.status, response.headers, transfer_body, body, text)

