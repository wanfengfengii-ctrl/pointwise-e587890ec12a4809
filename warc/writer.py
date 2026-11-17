from warc.headers import render_warc_headers


class WarcWriteError(OSError):
    pass


def iter_response_record(record):
    yield render_warc_headers(record.headers, len(record.raw))
    yield record.raw
    yield b"\r\n\r\n"


def write_all(sink, data):
    view = memoryview(data)
    position = 0
    while position < len(view):
        written = sink.write(view[position:])
        if type(written) is not int or written <= 0 or written > len(view) - position:
            raise WarcWriteError("sink made no valid progress")
        position += written
    return position


def write_response_record(record, sink):
    return sum(write_all(sink, part) for part in iter_response_record(record))
