import tempfile
from dataclasses import dataclass


class SourceReadError(IOError):
    pass


@dataclass(frozen=True)
class FetchedResponse:
    raw: bytes


def fetch_response(source, request, tempdir=None):
    try:
        response = source(request)
        iterator = iter((response,)) if isinstance(response, bytes) else iter(response)
        try:
            with tempfile.TemporaryFile(dir=tempdir) as output:
                for chunk in iterator:
                    if not isinstance(chunk, bytes):
                        raise TypeError("source chunks must be bytes")
                    output.write(chunk)
                output.seek(0)
                raw = output.read()
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                close()
        return FetchedResponse(raw)
    except Exception as exc:
        raise SourceReadError(str(exc)) from exc
