import sys
import os
import zlib
import functools
import threading
from typing import Any, Callable, IO, Iterable, Optional, Tuple, Union, TYPE_CHECKING
from urllib.parse import urlencode

import requests
from requests.utils import super_len

if TYPE_CHECKING:
    from requests_toolbelt import MultipartEncoder

from .context import Environment
from .cli.dicts import MultipartRequestDataDict, RequestDataDict
from .compat import is_windows


class ChunkedStream:
    def __iter__(self) -> Iterable[Union[str, bytes]]:
        raise NotImplementedError


class ChunkedUploadStream(ChunkedStream):
    def __init__(
        self,
        stream: Iterable,
        callback: Callable,
        event: Optional[threading.Event] = None
    ) -> None:
        self.callback = callback
        self.stream = stream
        self.event = event

    def __iter__(self) -> Iterable[Union[str, bytes]]:
        for chunk in self.stream:
            if self.event:
                self.event.set()
            self.callback(chunk)
            yield chunk


class ChunkedMultipartUploadStream(ChunkedStream):
    chunk_size = 100 * 1024

    def __init__(
        self,
        encoder: 'MultipartEncoder',
        event: Optional[threading.Event] = None
    ) -> None:
        self.encoder = encoder
        self.event = event

    def __iter__(self) -> Iterable[Union[str, bytes]]:
        while True:
            chunk = self.encoder.read(self.chunk_size)
            if self.event:
                self.event.set()
            if not chunk:
                break
            yield chunk


def as_bytes(data: Union[str, bytes]) -> bytes:
    if isinstance(data, str):
        return data.encode()
    else:
        return data


CallbackT = Callable[[bytes], bytes]


def _wrap_function_with_callback(
    func: Callable[..., Any],
    callback: CallbackT
) -> Callable[..., Any]:
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        chunk = func(*args, **kwargs)
        callback(chunk)
        return chunk
    return wrapped


def is_stdin(file: IO) -> bool:
    try:
        file_no = file.fileno()
    except Exception:
        return False
    else:
        return file_no == sys.stdin.fileno()


READ_THRESHOLD = float(os.getenv('HTTPIE_STDIN_READ_WARN_THRESHOLD', 10.0))


def observe_stdin_for_data_thread(env: Environment, file: IO, read_event: threading.Event) -> None:
    # Windows unfortunately does not support select() operation
    # on regular files, like stdin in our use case.
    # https://docs.python.org/3/library/select.html#select.select
    if is_windows:
        return None

    # If the user configures READ_THRESHOLD to be 0, then
    # disable this warning.
    if READ_THRESHOLD == 0:
        return None

    def worker(event: threading.Event) -> None:
        if not event.wait(timeout=READ_THRESHOLD):
            env.stderr.write(
                f'> warning: no stdin data read in {READ_THRESHOLD}s '
                f'(perhaps you want to --ignore-stdin)\n'
                f'> See: https://httpie.io/docs/cli/best-practices\n'
            )

    # Making it a daemon ensures that if the user exits from the main program
    # (e.g. either regularly or with Ctrl-C), the thread will not
    # block them.
    thread = threading.Thread(
        target=worker,
        args=(read_event,),
        daemon=True
    )
    thread.start()


def _read_file_with_selectors(file: IO, read_event: threading.Event) -> bytes:
    if is_windows or not is_stdin(file):
        return as_bytes(file.read())

    import select

    # Try checking whether there is any incoming data for READ_THRESHOLD
    # seconds. If there isn't anything in the given period, issue
    # a warning about a misusage.
    read_selectors, _, _ = select.select([file], [], [], READ_THRESHOLD)
    if read_selectors:
        read_event.set()

    return as_bytes(file.read())


def _prepare_file_for_upload(
    env: Environment,
    file: Union[IO, 'MultipartEncoder'],
    callback: CallbackT,
    chunked: bool = False,
    content_length_header_value: Optional[int] = None,
) -> Union[bytes, IO, ChunkedStream]:
    read_event = threading.Event()
    if not super_len(file):
        if is_stdin(file):
            observe_stdin_for_data_thread(env, file, read_event)

        # Zero-length -> assume stdin.
        if content_length_header_value is None and not chunked:
            # Read the whole stdin to determine `Content-Length`.
            #
            # TODO: Instead of opt-in --chunked, consider making
            #   `Transfer-Encoding: chunked` for STDIN opt-out via
            #   something like --no-chunked.
            #   This would be backwards-incompatible so wait until v3.0.0.
            #
            file = _read_file_with_selectors(file, read_event)
    else:
        file.read = _wrap_function_with_callback(
            file.read,
            callback
        )

    if chunked:
        from requests_toolbelt import MultipartEncoder
        if isinstance(file, MultipartEncoder):
            return ChunkedMultipartUploadStream(
                encoder=file,
                event=read_event,
            )
        else:
            return ChunkedUploadStream(
                stream=file,
                callback=callback,
                event=read_event
            )
    else:
        return file


def prepare_request_body(
    env: Environment,
    raw_body: Union[str, bytes, IO, 'MultipartEncoder', RequestDataDict],
    body_read_callback: CallbackT,
    offline: bool = False,
    chunked: bool = False,
    content_length_header_value: Optional[int] = None,
) -> Union[bytes, IO, 'MultipartEncoder', ChunkedStream]:
    is_file_like = hasattr(raw_body, 'read')
    if isinstance(raw_body, (bytes, str)):
        body = as_bytes(raw_body)
    elif isinstance(raw_body, RequestDataDict):
        body = as_bytes(urlencode(raw_body, doseq=True))
    else:
        body = raw_body

    if offline:
        if is_file_like:
            return as_bytes(raw_body.read())
        else:
            return body

    if is_file_like:
        return _prepare_file_for_upload(
            env,
            body,
            chunked=chunked,
            callback=body_read_callback,
            content_length_header_value=content_length_header_value
        )
    elif chunked:
        return ChunkedUploadStream(
            stream=iter([body]),
            callback=body_read_callback
        )
    else:
        return body


def get_multipart_data_and_content_type(
    data: MultipartRequestDataDict,
    boundary: str = None,
    content_type: str = None,
) -> Tuple['MultipartEncoder', str]:
    from requests_toolbelt import MultipartEncoder

    encoder = MultipartEncoder(
        fields=data.items(),
        boundary=boundary,
    )
    if content_type:
        content_type = content_type.strip()
        if 'boundary=' not in content_type:
            content_type = f'{content_type}; boundary={encoder.boundary_value}'
    else:
        content_type = encoder.content_type

    data = encoder
    return data, content_type


def compress_request(
    request: requests.PreparedRequest,
    always: bool,
):
    deflater = zlib.compressobj()
    if isinstance(request.body, str):
        body_bytes = request.body.encode()
    elif hasattr(request.body, 'read'):
        body_bytes = request.body.read()
        # The body is consumed now, so if we don't end up compressing we should
        # use the bytes we just read
        request.body = body_bytes
    else:
        body_bytes = request.body
    deflated_data = deflater.compress(body_bytes)
    deflated_data += deflater.flush()
    is_economical = len(deflated_data) < len(body_bytes)
    if is_economical or always:
        request.body = deflated_data
        request.headers['Content-Encoding'] = 'deflate'
        request.headers['Content-Length'] = str(len(deflated_data))
