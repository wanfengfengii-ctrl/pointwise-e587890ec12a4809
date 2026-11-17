class WarcStreamError(ValueError):
    pass


class ChunkReader:
    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self._buffer = b""
        self._position = 0
        self._ended = False

    def _available(self):
        while self._position == len(self._buffer) and not self._ended:
            try:
                chunk = next(self._chunks)
            except StopIteration:
                self._ended = True
                self._buffer = b""
                self._position = 0
                break
            if not isinstance(chunk, bytes):
                raise WarcStreamError("WARC chunks must be bytes")
            self._buffer = chunk
            self._position = 0
        return len(self._buffer) - self._position

    def at_eof(self):
        return self._available() == 0

    def read_exact(self, size):
        if type(size) is not int or size < 0:
            raise WarcStreamError("invalid read length")
        result = bytearray()
        while size:
            available = self._available()
            if not available:
                raise WarcStreamError("truncated WARC stream")
            take = min(size, available)
            result.extend(self._buffer[self._position:self._position + take])
            self._position += take
            size -= take
        return bytes(result)

    def read_line(self):
        result = bytearray()
        while True:
            if self.at_eof():
                raise WarcStreamError("truncated WARC line")
            available_end = self._buffer.find(b"\n", self._position)
            if available_end < 0:
                result.extend(self._buffer[self._position:])
                self._position = len(self._buffer)
                continue
            result.extend(self._buffer[self._position:available_end + 1])
            self._position = available_end + 1
            if not result.endswith(b"\r\n") or b"\r" in result[:-2] or b"\n" in result[:-2]:
                raise WarcStreamError("WARC requires CRLF")
            return bytes(result[:-2])

    def read_block(self, size):
        return self.read_exact(size)
