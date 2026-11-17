from email.message import Message


class TextDecodeError(ValueError):
    pass


def declared_charset(content_type=None):
    if content_type is None:
        return "utf-8"
    message = Message()
    message["content-type"] = content_type
    params = message.get_params(header="content-type", failobj=[])
    charsets = [value for name, value in params[1:] if name.lower() == "charset"]
    if len(charsets) > 1:
        raise TextDecodeError("duplicate charset")
    charset = charsets[0] if charsets else "utf-8"
    if not isinstance(charset, str) or charset.lower() != "utf-8":
        raise TextDecodeError("only UTF-8 is supported")
    return "utf-8"


def extract_text(body, content_type=None):
    charset = declared_charset(content_type)
    try:
        return body.decode(charset, errors="strict")
    except UnicodeError as exc:
        raise TextDecodeError("invalid UTF-8 text") from exc
