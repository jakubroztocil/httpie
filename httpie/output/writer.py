import errno
import requests
from typing import Any, Dict, IO, Optional, TextIO, Tuple, Type, Union

from ..cli.dicts import HTTPHeadersDict
from ..context import Environment
from ..models import (
    HTTPRequest,
    HTTPResponse,
    HTTPMessage,
    RequestsMessage,
    RequestsMessageKind,
    OutputOptions,
)
from .models import ProcessingOptions
from .processing import Conversion, Formatting
from .streams import (
    BaseStream,
    BufferedPrettyStream,
    EncodedStream,
    PrettyStream,
    RawStream,
    FilterStream,
    PrettyFilterStream,
    PrettyBufferedFilterStream,
    BufferedFilterStream,
)
from ..utils import parse_content_type_header


MESSAGE_SEPARATOR = '\n\n'
MESSAGE_SEPARATOR_BYTES = MESSAGE_SEPARATOR.encode()


def write_message(
    requests_message: RequestsMessage,
    env: Environment,
    output_options: OutputOptions,
    processing_options: ProcessingOptions,
    extra_stream_kwargs: Optional[Dict[str, Any]] = None
):
    if not output_options.any():
        return
    write_stream_kwargs = {
        'stream': build_output_stream_for_message(
            env=env,
            requests_message=requests_message,
            output_options=output_options,
            processing_options=processing_options,
            extra_stream_kwargs=extra_stream_kwargs
        ),
        # NOTE: `env.stdout` will in fact be `stderr` with `--download`
        'outfile': env.stdout,
        'flush': env.stdout_isatty or processing_options.stream
    }
    try:
        if env.is_windows and 'colors' in processing_options.get_prettify(env):
            write_stream_with_colors_win(**write_stream_kwargs)
        else:
            write_stream(**write_stream_kwargs)
    except OSError as e:
        if processing_options.show_traceback and e.errno == errno.EPIPE:
            # Ignore broken pipes unless --traceback.
            env.stderr.write('\n')
        else:
            raise


def write_stream(
    stream: BaseStream,
    outfile: Union[IO, TextIO],
    flush: bool
):
    """Write the output stream."""
    try:
        # Writing bytes so we use the buffer interface.
        buf = outfile.buffer
    except AttributeError:
        buf = outfile

    for chunk in stream:
        buf.write(chunk)
        if flush:
            outfile.flush()


def write_stream_with_colors_win(
    stream: 'BaseStream',
    outfile: TextIO,
    flush: bool
):
    """Like `write`, but colorized chunks are written as text
    directly to `outfile` to ensure it gets processed by colorama.
    Applies only to Windows and colorized terminal output.

    """
    color = b'\x1b['
    encoding = outfile.encoding
    for chunk in stream:
        if color in chunk:
            outfile.write(chunk.decode(encoding))
        else:
            outfile.buffer.write(chunk)
        if flush:
            outfile.flush()


def write_raw_data(
    env: Environment,
    data: Any,
    *,
    processing_options: Optional[ProcessingOptions] = None,
    headers: Optional[HTTPHeadersDict] = None,
    stream_kwargs: Optional[Dict[str, Any]] = None
):
    msg = requests.PreparedRequest()
    msg.is_body_upload_chunk = True
    msg.body = data
    msg.headers = headers or HTTPHeadersDict()
    msg_output_options = OutputOptions.from_message(msg, body=True, headers=False)
    return write_message(
        requests_message=msg,
        env=env,
        output_options=msg_output_options,
        processing_options=processing_options or ProcessingOptions(),
        extra_stream_kwargs=stream_kwargs
    )


def build_output_stream_for_message(
    env: Environment,
    requests_message: RequestsMessage,
    output_options: OutputOptions,
    processing_options: ProcessingOptions,
    extra_stream_kwargs: Optional[Dict[str, Any]] = None
):
    message_type = {
        RequestsMessageKind.REQUEST: HTTPRequest,
        RequestsMessageKind.RESPONSE: HTTPResponse,
    }[output_options.kind]
    stream_class, stream_kwargs = get_stream_type_and_kwargs(
        env=env,
        processing_options=processing_options,
        message_type=message_type,
        headers=requests_message.headers
    )
    if extra_stream_kwargs:
        stream_kwargs.update(extra_stream_kwargs)
    yield from stream_class(
        msg=message_type(requests_message),
        output_options=output_options,
        **stream_kwargs,
    )
    if (env.stdout_isatty and output_options.body and not output_options.meta
            and not getattr(requests_message, 'is_body_upload_chunk', False)):
        # Ensure a blank line after the response body.
        # For terminal output only.
        yield MESSAGE_SEPARATOR_BYTES


def get_stream_type_and_kwargs(
    env: Environment,
    processing_options: ProcessingOptions,
    message_type: Type[HTTPMessage],
    headers: HTTPHeadersDict,
) -> Tuple[Type['BaseStream'], dict]:
    """Pick the right stream type and kwargs for it based on `env` and `args`.

    """
    is_stream = processing_options.stream
    prettify_groups = processing_options.get_prettify(env)
    filtery_groups = processing_options.get_filtery(env)
    if not is_stream and message_type is HTTPResponse:
        # If this is a response, then check the headers for determining
        # auto-streaming.
        raw_content_type_header = headers.get('Content-Type', None)
        if raw_content_type_header:
            content_type_header, _ = parse_content_type_header(raw_content_type_header)
            is_stream = (content_type_header == 'text/event-stream')

    if not env.stdout_isatty and not prettify_groups:
        stream_class = RawStream
        stream_kwargs = {
            'chunk_size': (
                RawStream.CHUNK_SIZE_BY_LINE
                if is_stream
                else RawStream.CHUNK_SIZE
            )
        }
    else:
        stream_class = EncodedStream
        stream_kwargs = {
            'env': env,
        }
        if message_type is HTTPResponse:
            stream_kwargs.update({
                'mime_overwrite': processing_options.response_mime,
                'encoding_overwrite': processing_options.response_charset,
            })
        if prettify_groups:
            stream_class = PrettyStream if is_stream else BufferedPrettyStream
            stream_kwargs.update({
                'conversion': Conversion(),
                'formatting': Formatting(
                    env=env,
                    groups=prettify_groups,
                    color_scheme=processing_options.style,
                    explicit_json=processing_options.json,
                    format_options=processing_options.format_options,
                )
            })

        if filtery_groups:
            stream_class = FilterStream if is_stream else BufferedFilterStream
            stream_kwargs.update({
                'conversion': Conversion(),
                'filter': filtery_groups
            })

        if prettify_groups and filtery_groups:
            stream_class = PrettyFilterStream if is_stream else PrettyBufferedFilterStream

    return stream_class, stream_kwargs
