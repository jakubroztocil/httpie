import os
import base64
import json
import mimetypes
import re
import sys
import time
import tempfile
import sysconfig

from collections import OrderedDict
from contextlib import contextmanager
from http.cookiejar import parse_ns_headers
from pathlib import Path
from pprint import pformat
from urllib.parse import urlsplit
from typing import Any, List, Optional, Tuple, Generator, Callable, Iterable, IO, TypeVar

import requests.auth

RE_COOKIE_SPLIT = re.compile(r', (?=[^ ;]+=)')
Item = Tuple[str, Any]
Items = List[Item]
T = TypeVar("T")


class JsonDictPreservingDuplicateKeys(OrderedDict):
    """A specialized JSON dict preserving duplicate keys."""

    # Python versions prior to 3.8 suffer from an issue with multiple keys with the same name.
    # `json.dumps(obj, indent=N, sort_keys=True)` will output sorted keys when they are unique, and
    # duplicate keys will be outputted as they were defined in the original data.
    # See <https://bugs.python.org/issue23493#msg400929> for the behavior change between Python versions.
    SUPPORTS_SORTING = sys.version_info >= (3, 8)

    def __init__(self, items: Items):
        self._items = items
        self._ensure_items_used()

    def _ensure_items_used(self) -> None:
        """HACK: Force `json.dumps()` to use `self.items()` instead of an empty dict.

        Two JSON encoders are available on CPython: pure-Python (1) and C (2) implementations.

        (1) The pure-python implementation will do a simple `if not dict: return '{}'`,
        and we could fake that check by implementing the `__bool__()` method.
        Source:
            - <https://github.com/python/cpython/blob/9d318ad/Lib/json/encoder.py#L334-L336>

        (2) On the other hand, the C implementation will do a check on the number of
        items contained inside the dict, using a verification on `dict->ma_used`, which
        is updated only when an item is added/removed from the dict. For that case,
        there is no workaround but to add an item into the dict.
        Sources:
            - <https://github.com/python/cpython/blob/9d318ad/Modules/_json.c#L1581-L1582>
            - <https://github.com/python/cpython/blob/9d318ad/Include/cpython/dictobject.h#L53>
            - <https://github.com/python/cpython/blob/9d318ad/Include/cpython/dictobject.h#L17-L18>

        To please both implementations, we simply add one item to the dict.

        """
        if self._items:
            self['__hack__'] = '__hack__'

    def items(self) -> Items:
        """Return all items, duplicate ones included.

        """
        return self._items


def load_json_preserve_order_and_dupe_keys(s):
    return json.loads(s, object_pairs_hook=JsonDictPreservingDuplicateKeys)


def repr_dict(d: dict) -> str:
    return pformat(d)


def humanize_bytes(n, precision=2):
    # Author: Doug Latornell
    # Licence: MIT
    # URL: https://code.activestate.com/recipes/577081/
    """Return a humanized string representation of a number of bytes.

    >>> humanize_bytes(1)
    '1 B'
    >>> humanize_bytes(1024, precision=1)
    '1.0 kB'
    >>> humanize_bytes(1024 * 123, precision=1)
    '123.0 kB'
    >>> humanize_bytes(1024 * 12342, precision=1)
    '12.1 MB'
    >>> humanize_bytes(1024 * 12342, precision=2)
    '12.05 MB'
    >>> humanize_bytes(1024 * 1234, precision=2)
    '1.21 MB'
    >>> humanize_bytes(1024 * 1234 * 1111, precision=2)
    '1.31 GB'
    >>> humanize_bytes(1024 * 1234 * 1111, precision=1)
    '1.3 GB'

    """
    abbrevs = [
        (1 << 50, 'PB'),
        (1 << 40, 'TB'),
        (1 << 30, 'GB'),
        (1 << 20, 'MB'),
        (1 << 10, 'kB'),
        (1, 'B')
    ]

    if n == 1:
        return '1 B'

    for factor, suffix in abbrevs:
        if n >= factor:
            break

    # noinspection PyUnboundLocalVariable
    return f'{n / factor:.{precision}f} {suffix}'


class ExplicitNullAuth(requests.auth.AuthBase):
    """Forces requests to ignore the ``.netrc``.
    <https://github.com/psf/requests/issues/2773#issuecomment-174312831>
    """

    def __call__(self, r):
        return r


def get_content_type(filename):
    """
    Return the content type for ``filename`` in format appropriate
    for Content-Type headers, or ``None`` if the file type is unknown
    to ``mimetypes``.

    """
    return mimetypes.guess_type(filename, strict=False)[0]


def split_cookies(cookies):
    """
    When ``requests`` stores cookies in ``response.headers['Set-Cookie']``
    it concatenates all of them through ``, ``.

    This function splits cookies apart being careful to not to
    split on ``, `` which may be part of cookie value.
    """
    if not cookies:
        return []
    return RE_COOKIE_SPLIT.split(cookies)


def get_expired_cookies(
    cookies: str,
    now: float = None
) -> List[dict]:

    now = now or time.time()

    def is_expired(expires: Optional[float]) -> bool:
        return expires is not None and expires <= now

    attr_sets: List[Tuple[str, str]] = parse_ns_headers(
        split_cookies(cookies)
    )

    # Use a dict to keep only the last value for each cookie, so that an early
    # expiration doesn't prevent a later setting in the same headers.
    cookies = {
        # The first attr name is the cookie name.
        attrs[0][0]: dict(attrs[1:], name=attrs[0][0])
        for attrs in attr_sets
    }
    cookies = list(cookies.values())

    _max_age_to_expires(cookies=cookies, now=now)

    return [
        {
            'name': cookie['name'],
            'path': cookie.get('path', '/')
        }
        for cookie in cookies
        if is_expired(expires=cookie.get('expires'))
    ]


def _max_age_to_expires(cookies, now):
    """
    Translate `max-age` into `expires` for Requests to take it into account.

    HACK/FIXME: <https://github.com/psf/requests/issues/5743>

    """
    for cookie in cookies:
        if 'expires' in cookie:
            continue
        max_age = cookie.get('max-age')
        if max_age and max_age.isdigit():
            cookie['expires'] = now + float(max_age)


def parse_content_type_header(header):
    """Borrowed from requests."""
    tokens = header.split(';')
    content_type, params = tokens[0].strip(), tokens[1:]
    params_dict = {}
    items_to_strip = "\"' "
    for param in params:
        param = param.strip()
        if param:
            key, value = param, True
            index_of_equals = param.find("=")
            if index_of_equals != -1:
                key = param[:index_of_equals].strip(items_to_strip)
                value = param[index_of_equals + 1:].strip(items_to_strip)
            params_dict[key.lower()] = value
    return content_type, params_dict


def as_site(path: Path, **extra_vars) -> Path:
    site_packages_path = sysconfig.get_path(
        'purelib',
        vars={'base': str(path), **extra_vars}
    )
    return Path(site_packages_path)


def get_site_paths(path: Path) -> Iterable[Path]:
    from httpie.compat import (
        MIN_SUPPORTED_PY_VERSION,
        MAX_SUPPORTED_PY_VERSION,
        is_frozen
    )

    if is_frozen:
        [major, min_minor] = MIN_SUPPORTED_PY_VERSION
        [major, max_minor] = MAX_SUPPORTED_PY_VERSION
        for minor in range(min_minor, max_minor + 1):
            yield as_site(
                path,
                py_version_short=f'{major}.{minor}'
            )
    else:
        yield as_site(path)


def split_iterable(iterable: Iterable[T], key: Callable[[T], bool]) -> Tuple[List[T], List[T]]:
    left, right = [], []
    for item in iterable:
        if key(item):
            left.append(item)
        else:
            right.append(item)
    return left, right


def unwrap_context(exc: Exception) -> Optional[Exception]:
    context = exc.__context__
    if isinstance(context, Exception):
        return unwrap_context(context)
    else:
        return exc


def url_as_host(url: str) -> str:
    return urlsplit(url).netloc.split('@')[-1]


class LockFileError(ValueError):
    pass


@contextmanager
def open_with_lockfile(file: Path, *args, **kwargs) -> Generator[IO[Any], None, None]:
    file_id = base64.b64encode(os.fsencode(file)).decode()
    target_file = Path(tempfile.gettempdir()) / file_id

    # Have an atomic-like touch here, so we'll tighten the possibility of
    # a race occurring between multiple processes accessing the same file.
    try:
        target_file.touch(exist_ok=False)
    except FileExistsError as exc:
        raise LockFileError("Can't modify a locked file.") from exc

    try:
        with open(file, *args, **kwargs) as stream:
            yield stream
    finally:
        target_file.unlink()


def is_version_greater(version_1: str, version_2: str) -> bool:
    # In an ideal scenario, we would depend on `packaging` in order
    # to offer PEP 440 compatible parsing. But since it might not be
    # commonly available for outside packages, and since we are only
    # going to parse HTTPie's own version it should be fine to compare
    # this in a SemVer subset fashion.

    def split_version(version: str) -> Tuple[int, ...]:
        parts = []
        for part in version.split('.')[:3]:
            try:
                parts.append(int(part))
            except ValueError:
                break
        return tuple(parts)

    return split_version(version_1) > split_version(version_2)
