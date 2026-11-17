import hashlib
from dataclasses import dataclass


class TextConsistencyError(ValueError):
    pass


@dataclass(frozen=True)
class TextView:
    text: str
    byte_length: int
    digest: str


def decode_stored_text(data, evidence):
    if not isinstance(data, bytes):
        raise TextConsistencyError("stored text is not bytes")
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != evidence["text_size"] or digest != evidence["text_digest"]:
        raise TextConsistencyError("stored text evidence mismatch")
    try:
        return TextView(data.decode("utf-8"), len(data), digest)
    except UnicodeError as exc:
        raise TextConsistencyError("stored text is not UTF-8") from exc


def compare_text(stored, evidence, actual):
    view = decode_stored_text(stored, evidence)
    if view.text != actual:
        raise TextConsistencyError("stored text differs from source")
    return view
