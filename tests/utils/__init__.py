"""Utilities for HTTPie test suite."""
import re
import shlex
import os
import sys
import time
import json
import tempfile
import warnings
import pytest
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from typing import Any, Optional, Union, List, Iterable

import httpie.core as core
import httpie.manager.__main__ as manager

from httpie.status import ExitStatus
from httpie.config import Config
from httpie.encoding import UTF8
from httpie.context import Environment
from httpie.utils import url_as_host


REMOTE_HTTPBIN_DOMAIN = 'pie.dev'

# pytest-httpbin currently does not support chunked requests:
# <https://github.com/kevin1024/pytest-httpbin/issues/33>
# <https://github.com/kevin1024/pytest-httpbin/issues/28>
HTTPBIN_WITH_CHUNKED_SUPPORT_DOMAIN = 'pie.dev'
HTTPBIN_WITH_CHUNKED_SUPPORT = 'http://' + HTTPBIN_WITH_CHUNKED_SUPPORT_DOMAIN

IS_PYOPENSSL = os.getenv('HTTPIE_TEST_WITH_PYOPENSSL', '0') == '1'

TESTS_ROOT = Path(__file__).parent.parent
CRLF = '\r\n'
COLOR = '\x1b['
COLOR_RE = re.compile(r'\x1b\[\d+(;\d+)*?m', re.MULTILINE)

HTTP_OK = '200 OK'
# noinspection GrazieInspection
HTTP_OK_COLOR = (
    'HTTP\x1b[39m\x1b[38;5;245m/\x1b[39m\x1b'
    '[38;5;37m1.1\x1b[39m\x1b[38;5;245m \x1b[39m\x1b[38;5;37m200'
    '\x1b[39m\x1b[38;5;245m \x1b[39m\x1b[38;5;136mOK'
)

DUMMY_URL = 'http://this-should.never-resolve'  # Note: URL never fetched
DUMMY_HOST = url_as_host(DUMMY_URL)

# We don't want hundreds of subprocesses trying to access GitHub API
# during the tests.
Config.DEFAULTS['disable_update_warnings'] = True


def strip_colors(colorized_msg: str) -> str:
    return COLOR_RE.sub('', colorized_msg)


def mk_config_dir() -> Path:
    dirname = tempfile.mkdtemp(prefix='httpie_config_')
    return Path(dirname)


def add_auth(url, auth):
    proto, rest = url.split('://', 1)
    return f'{proto}://{auth}@{rest}'


class Encoder:
    """
    Encode binary fragments into a text stream. This is used
    to embed raw binary data (which can't be decoded) into the
    fake standard output we use on MockEnvironment.

    Each data fragment is embedded by it's hash:
        "Some data hash(XXX) more data."

    Which then later converted back to a bytes object:
        b"Some data <real data> more data."
    """

    TEMPLATE = 'hash({})'

    STR_PATTERN = re.compile(r'hash\((.*)\)')
    BYTES_PATTERN = re.compile(rb'hash\((.*)\)')

    def __init__(self):
        self.substitutions = {}

    def substitute(self, data: bytes) -> str:
        idx = hash(data)
        self.substitutions[idx] = data
        return self.TEMPLATE.format(idx)

    def decode(self, data: str) -> Union[str, bytes]:
        if self.STR_PATTERN.search(data) is None:
            return data

        raw_data = data.encode()
        return self.BYTES_PATTERN.sub(
            lambda match: self.substitutions[int(match.group(1))],
            raw_data
        )


class FakeBytesIOBuffer(BytesIO):

    def __init__(self, original, encoder, *args, **kwargs):
        self.original_buffer = original
        self.encoder = encoder
        super().__init__(*args, **kwargs)

    def write(self, data):
        try:
            self.original_buffer.write(data.decode(UTF8))
        except UnicodeDecodeError:
            self.original_buffer.write(self.encoder.substitute(data))
        finally:
            self.original_buffer.flush()


class StdinBytesIO(BytesIO):
    """To be used for `MockEnvironment.stdin`"""
    len = 0  # See `prepare_request_body()`


class MockEnvironment(Environment):
    """Environment subclass with reasonable defaults for testing."""
    colors = 0  # For easier debugging
    stdin_isatty = True
    stdout_isatty = True
    is_windows = False
    show_displays = False

    def __init__(self, create_temp_config_dir=True, **kwargs):
        self._encoder = Encoder()
        if 'stdout' not in kwargs:
            kwargs['stdout'] = tempfile.NamedTemporaryFile(
                mode='w+t',
                prefix='httpie_stderr',
                newline='',
                encoding=UTF8,
            )
            kwargs['stdout'].buffer = FakeBytesIOBuffer(kwargs['stdout'], self._encoder)
        if 'stderr' not in kwargs:
            kwargs['stderr'] = tempfile.TemporaryFile(
                mode='w+t',
                prefix='httpie_stderr',
                encoding=UTF8,
            )
        super().__init__(**kwargs)
        self._create_temp_config_dir = create_temp_config_dir
        self._delete_config_dir = False
        self._temp_dir = Path(tempfile.gettempdir())

    @property
    def config(self) -> Config:
        if (self._create_temp_config_dir
                and self._temp_dir not in self.config_dir.parents):
            self.create_temp_config_dir()
        return super().config

    def create_temp_config_dir(self):
        self.config_dir = mk_config_dir()
        self._delete_config_dir = True

    def cleanup(self):
        self.devnull.close()
        self.stdout.close()
        self.stderr.close()
        warnings.resetwarnings()
        if self._delete_config_dir:
            assert self._temp_dir in self.config_dir.parents
            from shutil import rmtree
            rmtree(self.config_dir, ignore_errors=True)

    def __del__(self):
        # noinspection PyBroadException
        try:
            self.cleanup()
        except Exception:
            pass


class PersistentMockEnvironment(MockEnvironment):
    def cleanup(self):
        pass


class BaseCLIResponse:
    """
    Represents the result of simulated `$ http' invocation via `http()`.

    Holds and provides access to:

        - stdout output: print(self)
        - stderr output: print(self.stderr)
        - devnull output: print(self.devnull)
        - exit_status output: print(self.exit_status)

    """
    stderr: str = None
    devnull: str = None
    json: dict = None
    exit_status: ExitStatus = None
    args: List[str] = []
    complete_args: List[str] = []

    @property
    def command(self):  # noqa: F811
        cmd = ' '.join(shlex.quote(arg) for arg in ['http', *self.args])
        # pytest-httpbin to real httpbin.
        return re.sub(r'127\.0\.0\.1:\d+', 'httpbin.org', cmd)

    @classmethod
    def from_raw_data(self, data: Union[str, bytes]) -> 'BaseCLIResponse':
        if isinstance(data, bytes):
            with suppress(UnicodeDecodeError):
                data = data.decode()

        if isinstance(data, bytes):
            return BytesCLIResponse(data)
        else:
            return StrCLIResponse(data)


class BytesCLIResponse(bytes, BaseCLIResponse):
    """
    Used as a fallback when a StrCLIResponse cannot be used.

    E.g. when the output contains binary data or when it is colorized.

    `.json` will always be None.

    """


class StrCLIResponse(str, BaseCLIResponse):

    @property
    def json(self) -> Optional[dict]:
        """
        Return deserialized the request or response JSON body,
        if one (and only one) included in the output and is parsable.

        """
        if not hasattr(self, '_json'):
            self._json = None
            # De-serialize JSON body if possible.
            if COLOR in self:
                # Colorized output cannot be parsed.
                pass
            elif self.strip().startswith('{'):
                # Looks like JSON body.
                self._json = json.loads(self)
            elif self.count('Content-Type:') == 1:
                # Looks like a HTTP message,
                # try to extract JSON from its body.
                try:
                    j = self.strip()[self.strip().rindex('\r\n\r\n'):]
                except ValueError:
                    pass
                else:
                    try:
                        # noinspection PyAttributeOutsideInit
                        self._json = json.loads(j)
                    except ValueError:
                        pass
        return self._json


class ExitStatusError(Exception):
    pass


@pytest.fixture
def mock_env() -> MockEnvironment:
    env = MockEnvironment()
    yield env
    env.cleanup()


def normalize_args(args: Iterable[Any]) -> List[str]:
    return [str(arg) for arg in args]


def httpie(
    *args,
    **kwargs
) -> StrCLIResponse:
    """
    Run HTTPie manager command with the given
    args/kwargs, and capture stderr/out and exit
    status.
    """

    env = kwargs.setdefault('env', MockEnvironment())
    cli_args = ['httpie']
    if not kwargs.pop('no_debug', False):
        cli_args.append('--debug')
    cli_args += normalize_args(args)
    exit_status = manager.main(
        args=cli_args,
        **kwargs
    )

    env.stdout.seek(0)
    env.stderr.seek(0)
    try:
        response = BaseCLIResponse.from_raw_data(env.stdout.read())
        response.stderr = env.stderr.read()
        response.exit_status = exit_status
        response.args = cli_args
    finally:
        env.stdout.truncate(0)
        env.stderr.truncate(0)
        env.stdout.seek(0)
        env.stderr.seek(0)

    return response


def http(
    *args,
    program_name='http',
    tolerate_error_exit_status=False,
    **kwargs,
) -> Union[StrCLIResponse, BytesCLIResponse]:
    # noinspection PyUnresolvedReferences
    """
    Run HTTPie and capture stderr/out and exit status.
    Content written to devnull will be captured only if
    env.devnull is set manually.

    Invoke `httpie.core.main()` with `args` and `kwargs`,
    and return a `CLIResponse` subclass instance.

    The return value is either a `StrCLIResponse`, or `BytesCLIResponse`
    if unable to decode the output. Devnull is string when possible,
    bytes otherwise.

    The response has the following attributes:

        `stdout` is represented by the instance itself (print r)
        `stderr`: text written to stderr
        `devnull` text written to devnull.
        `exit_status`: the exit status
        `json`: decoded JSON (if possible) or `None`

    Exceptions are propagated.

    If you pass ``tolerate_error_exit_status=True``, then error exit statuses
    won't result into an exception.

    Example:

    $ http --auth=user:password GET pie.dev/basic-auth/user/password

        >>> httpbin = getfixture('httpbin')
        >>> r = http('-a', 'user:pw', httpbin + '/basic-auth/user/pw')
        >>> type(r) == StrCLIResponse
        True
        >>> r.exit_status is ExitStatus.SUCCESS
        True
        >>> r.stderr
        ''
        >>> 'HTTP/1.1 200 OK' in r
        True
        >>> r.json == {'authenticated': True, 'user': 'user'}
        True

    """
    env = kwargs.get('env')
    if not env:
        env = kwargs['env'] = MockEnvironment()

    stdout = env.stdout
    stderr = env.stderr
    devnull = env.devnull

    args = list(args)
    args_with_config_defaults = args + env.config.default_options
    add_to_args = []
    if '--debug' not in args_with_config_defaults:
        if (not tolerate_error_exit_status
                and '--traceback' not in args_with_config_defaults):
            add_to_args.append('--traceback')
        if not any('--timeout' in arg for arg in args_with_config_defaults):
            add_to_args.append('--timeout=3')

    complete_args = [program_name, *add_to_args, *args]
    # print(' '.join(complete_args))

    def dump_stderr():
        stderr.seek(0)
        sys.stderr.write(stderr.read())

    try:
        try:
            exit_status = core.main(args=complete_args, **kwargs)
            if '--download' in args:
                # Let the progress reporter thread finish.
                time.sleep(.5)
        except SystemExit:
            if tolerate_error_exit_status:
                exit_status = ExitStatus.ERROR
            else:
                dump_stderr()
                raise
        except Exception:
            stderr.seek(0)
            sys.stderr.write(stderr.read())
            raise
        else:
            if (not tolerate_error_exit_status
                    and exit_status != ExitStatus.SUCCESS):
                dump_stderr()
                raise ExitStatusError(
                    'httpie.core.main() unexpectedly returned'
                    f' a non-zero exit status: {exit_status}'
                )

        stdout.seek(0)
        stderr.seek(0)
        devnull.seek(0)
        output = stdout.read()
        devnull_output = devnull.read()

        if hasattr(env, '_encoder'):
            output = env._encoder.decode(output)

        r = BaseCLIResponse.from_raw_data(output)

        try:
            devnull_output = devnull_output.decode()
        except Exception:
            pass

        r.devnull = devnull_output
        r.stderr = stderr.read()
        r.exit_status = exit_status
        r.args = args
        r.complete_args = ' '.join(complete_args)

        if r.exit_status != ExitStatus.SUCCESS:
            sys.stderr.write(r.stderr)

        # print(f'\n\n$ {r.command}\n')
        return r

    finally:
        env.cleanup()
