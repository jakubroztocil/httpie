from typing import Union

from charset_normalizer import from_bytes

from .constants import UTF8


def detect_encoding(content: bytes) -> str:
    """Detect the `content` charset encoding.
    Fallback to UTF-8 when no suitable encoding found.

    """
    match = from_bytes(bytes(content)).best()
    return match.encoding if match else UTF8


def decode(content: Union[bytearray, bytes, str], encoding: str) -> str:
    """Decode `content` using the provided `encoding`.
    If no `encoding` is provided, the best effort is to guess it from `content`.

    Unicode errors are replaced.

    """
    if isinstance(content, str):
        return content

    if not encoding:
        encoding = detect_encoding(content)

    return content.decode(encoding, 'replace')
