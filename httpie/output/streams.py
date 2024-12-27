from abc import ABCMeta, abstractmethod
from itertools import chain
from typing import Callable, Iterable, List, Optional, Union

from .processing import Conversion, Formatting
from ..context import Environment
from ..encoding import smart_decode, smart_encode, UTF8
from ..models import HTTPMessage, OutputOptions
from ..utils import parse_content_type_header


BINARY_SUPPRESSED_NOTICE = (
    b'\n'
    b'+-----------------------------------------+\n'
    b'| NOTE: binary data not shown in terminal |\n'
    b'+-----------------------------------------+'
)


class DataSuppressedError(Exception):
    message = None


class BinarySuppressedError(DataSuppressedError):
    """An error indicating that the body is binary and won't be written,
     e.g., for terminal output)."""
    message = BINARY_SUPPRESSED_NOTICE


class BaseStream(metaclass=ABCMeta):
    """Base HTTP message output stream class."""

    def __init__(
        self,
        msg: HTTPMessage,
        output_options: OutputOptions,
        on_body_chunk_downloaded: Callable[[bytes], None] = None,
        **kwargs
    ):
        """
        :param msg: a :class:`models.HTTPMessage` subclass
        :param output_options: a :class:`OutputOptions` instance to represent
                               which parts of the message is printed.
        """
        assert output_options.any()
        self.msg = msg
        self.output_options = output_options
        self.on_body_chunk_downloaded = on_body_chunk_downloaded
        self.extra_options = kwargs

    def get_headers(self) -> bytes:
        """Return the headers' bytes."""
        return self.msg.headers.encode()

    def get_metadata(self) -> bytes:
        """Return the message metadata."""
        return self.msg.metadata.encode()

    @abstractmethod
    def iter_body(self) -> Iterable[bytes]:
        """Return an iterator over the message body."""

    def __iter__(self) -> Iterable[bytes]:
        """Return an iterator over `self.msg`."""
        if self.output_options.headers:
            yield self.get_headers()
            yield b'\r\n\r\n'

        if self.output_options.body:
            try:
                for chunk in self.iter_body():
                    yield chunk
                    if self.on_body_chunk_downloaded:
                        self.on_body_chunk_downloaded(chunk)
            except DataSuppressedError as e:
                if self.output_options.headers:
                    yield b'\n'
                yield e.message

        if self.output_options.meta:
            if self.output_options.body:
                yield b'\n\n'

            yield self.get_metadata()
            yield b'\n\n'


class RawStream(BaseStream):
    """The message is streamed in chunks with no processing."""

    CHUNK_SIZE = 1024 * 100
    CHUNK_SIZE_BY_LINE = 1

    def __init__(self, chunk_size=CHUNK_SIZE, **kwargs):
        super().__init__(**kwargs)
        self.chunk_size = chunk_size

    def iter_body(self) -> Iterable[bytes]:
        return self.msg.iter_body(self.chunk_size)


ENCODING_GUESS_THRESHOLD = 3


class EncodedStream(BaseStream):
    """Encoded HTTP message stream.

    The message bytes are converted to an encoding suitable for
    `self.env.stdout`. Unicode errors are replaced and binary data
    is suppressed. The body is always streamed by line.

    """
    CHUNK_SIZE = 1

    def __init__(
        self,
        env=Environment(),
        mime_overwrite: str = None,
        encoding_overwrite: str = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        if mime_overwrite:
            self.mime = mime_overwrite
        else:
            self.mime, _ = parse_content_type_header(self.msg.content_type)
        self._encoding = encoding_overwrite or self.msg.encoding
        self._encoding_guesses = []
        if env.stdout_isatty:
            # Use the encoding supported by the terminal.
            output_encoding = env.stdout_encoding
        else:
            # Preserve the message encoding.
            output_encoding = self.msg.encoding
        # Default to UTF-8 when unsure.
        self.output_encoding = output_encoding or UTF8

    def iter_body(self) -> Iterable[bytes]:
        for line, lf in self.msg.iter_lines(self.CHUNK_SIZE):
            if b'\0' in line:
                raise BinarySuppressedError()
            line = self.decode_chunk(line)
            yield smart_encode(line, self.output_encoding) + lf

    def decode_chunk(self, raw_chunk: str) -> str:
        chunk, guessed_encoding = smart_decode(raw_chunk, self.encoding)
        self._encoding_guesses.append(guessed_encoding)
        return chunk

    @property
    def encoding(self) -> Optional[str]:
        if self._encoding:
            return self._encoding

        # If we find a reliable (used consecutively) encoding, than
        # use it for the next iterations.
        if len(self._encoding_guesses) < ENCODING_GUESS_THRESHOLD:
            return None

        guess_1, guess_2 = self._encoding_guesses[-2:]
        if guess_1 == guess_2:
            self._encoding = guess_1
            return guess_1

    @encoding.setter
    def encoding(self, value) -> None:
        self._encoding = value


class PrettyStream(EncodedStream):
    """In addition to :class:`EncodedStream` behaviour, this stream applies
    content processing.

    Useful for long-lived HTTP responses that stream by lines
    such as the Twitter streaming API.

    """

    CHUNK_SIZE = 1

    def __init__(
        self, conversion: Conversion,
        formatting: Formatting,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.formatting = formatting
        self.conversion = conversion

    def get_headers(self) -> bytes:
        return self.formatting.format_headers(
            self.msg.headers).encode(self.output_encoding)

    def get_metadata(self) -> bytes:
        return self.formatting.format_metadata(
            self.msg.metadata).encode(self.output_encoding)

    def iter_body(self) -> Iterable[bytes]:
        first_chunk = True
        iter_lines = self.msg.iter_lines(self.CHUNK_SIZE)
        for line, lf in iter_lines:
            if b'\0' in line:
                if first_chunk:
                    converter = self.conversion.get_converter(self.mime)
                    if converter:
                        body = bytearray()
                        # noinspection PyAssignmentToLoopOrWithParameter
                        for line, lf in chain([(line, lf)], iter_lines):
                            body.extend(line)
                            body.extend(lf)
                        self.mime, body = converter.convert(body)
                        assert isinstance(body, str)
                        yield self.process_body(body)
                        return
                raise BinarySuppressedError()
            yield self.process_body(line) + lf
            first_chunk = False

    def process_body(self, chunk: Union[str, bytes]) -> bytes:
        if not isinstance(chunk, str):
            # Text when a converter has been used,
            # otherwise it will always be bytes.
            chunk = self.decode_chunk(chunk)
        chunk = self.formatting.format_body(content=chunk, mime=self.mime)
        return smart_encode(chunk, self.output_encoding)
    
class FilterStream(EncodedStream):

    CHUNK_SIZE = 1

    def __init__(
        self, 
        conversion: Conversion,
        filter: List['str'],
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.filters = filter
        self.conversion = conversion

    def get_headers(self) -> bytes:
        return self.msg.headers.encode(self.output_encoding)
            

    def get_metadata(self) -> bytes:
        return self.msg.metadata.encode(self.output_encoding)
            

    def iter_body(self) -> Iterable[bytes]:
        first_chunk = True
        iter_lines = self.msg.iter_lines(self.CHUNK_SIZE)
        for line, lf in iter_lines:
            if b'\0' in line:
                if first_chunk:
                    converter = self.conversion.get_converter(self.mime)
                    if converter:
                        body = bytearray()
                        # noinspection PyAssignmentToLoopOrWithParameter
                        for line, lf in chain([(line, lf)], iter_lines):
                            body.extend(line)
                            body.extend(lf)
                        assert isinstance(body, str)
                        yield self.process_body(body)
                        return
                raise BinarySuppressedError()
            yield self.process_body(line) + lf
            first_chunk = False

    def process_body(self, chunk: Union[str, bytes]) -> bytes:
        if not isinstance(chunk, str):
            # Text when a converter has been used,
            # otherwise it will always be bytes.
            chunk = self.decode_chunk(chunk)
        chunk_dict = eval(chunk)
        for word in self.filters:
            temp_dict = chunk_dict
            splitwords = word.split(".")
            for i in range(len(splitwords)-1):
                subword = splitwords[i]
                if subword in temp_dict:
                    temp_dict = temp_dict[subword]
                else:
                    break
            else:
                subword = splitwords[-1]
                if subword in temp_dict:
                    del temp_dict[subword]
        chunk = f'{chunk_dict}'
        return smart_encode(chunk, self.output_encoding)
    

class PrettyFilterStream(PrettyStream):

    CHUNK_SIZE = 1

    def __init__(
        self, 
        conversion: Conversion,
        formatting: Formatting,
        filter: List['str'],
        **kwargs,
    ):
        super().__init__(conversion=conversion, formatting=formatting, **kwargs)
        self.filters = filter

    def process_body(self, chunk: Union[str, bytes]) -> bytes:
        if not isinstance(chunk, str):
            # Text when a converter has been used,
            # otherwise it will always be bytes.
            chunk = self.decode_chunk(chunk)
        chunk_dict = eval(chunk)
        for word in self.filters:
            temp_dict = chunk_dict
            splitwords = word.split(".")
            for i in range(len(splitwords)-1):
                subword = splitwords[i]
                if subword in temp_dict:
                    temp_dict = temp_dict[subword]
                else:
                    break
            else:
                subword = splitwords[-1]
                if subword in temp_dict:
                    del temp_dict[subword]
        chunk = (f'{chunk_dict}').replace(" ", "").replace("'", '"')
        chunk = self.formatting.format_body(content=chunk, mime=self.mime)
        return smart_encode(chunk, self.output_encoding)



class BufferedPrettyStream(PrettyStream):
    """The same as :class:`PrettyStream` except that the body is fully
    fetched before it's processed.

    Suitable regular HTTP responses.

    """

    CHUNK_SIZE = 1024 * 10

    def iter_body(self) -> Iterable[bytes]:
        # Read the whole body before prettifying it,
        # but bail out immediately if the body is binary.
        converter = None
        body = bytearray()

        for chunk in self.msg.iter_body(self.CHUNK_SIZE):
            if not converter and b'\0' in chunk:
                converter = self.conversion.get_converter(self.mime)
                if not converter:
                    raise BinarySuppressedError()
            body.extend(chunk)

        if converter:
            self.mime, body = converter.convert(body)

        yield self.process_body(body)


class PrettyBufferedFilterStream(PrettyFilterStream):

    CHUNK_SIZE = 1024 * 10

    def iter_body(self) -> Iterable[bytes]:
        # Read the whole body before prettifying it,
        # but bail out immediately if the body is binary.
        converter = None
        body = bytearray()

        for chunk in self.msg.iter_body(self.CHUNK_SIZE):
            if not converter and b'\0' in chunk:
                converter = self.conversion.get_converter(self.mime)
                if not converter:
                    raise BinarySuppressedError()
            body.extend(chunk)

        if converter:
            self.mime, body = converter.convert(body)

        yield self.process_body(body)


class BufferedFilterStream(FilterStream):
    """The same as :class:`PrettyStream` except that the body is fully
    fetched before it's processed.

    Suitable regular HTTP responses.

    """

    CHUNK_SIZE = 1024 * 10

    def iter_body(self) -> Iterable[bytes]:
        # Read the whole body before prettifying it,
        # but bail out immediately if the body is binary.
        converter = None
        body = bytearray()

        for chunk in self.msg.iter_body(self.CHUNK_SIZE):
            if not converter and b'\0' in chunk:
                converter = self.conversion.get_converter(self.mime)
                if not converter:
                    raise BinarySuppressedError()
            body.extend(chunk)

        if converter:
            self.mime, body = converter.convert(body)

        yield self.process_body(body)