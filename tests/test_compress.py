"""
We test against httpbin which doesn't return the request data in a
consistent way:

1. Non-form requests: the `data` field contains base64 encoded version of
our zlib-encoded request data.

2. Form requests: `form` contains a messed up version of the data.

"""
import base64
import zlib

from .fixtures import FILE_PATH, FILE_CONTENT, TINY_FILE_PATH_ARG
from httpie.status import ExitStatus
from .utils import StdinBytesIO, http, HTTP_OK, MockEnvironment


def assert_decompressed_equal(base64_compressed_data, expected_str):
    compressed_data = base64.b64decode(
        base64_compressed_data.split(',', 1)[1])
    data = zlib.decompress(compressed_data)
    actual_str = data.decode()

    # FIXME: contains a trailing linebreak with an uploaded file
    actual_str = actual_str.rstrip()

    assert actual_str == expected_str


def test_cannot_combine_compress_with_chunked(httpbin):
    r = http('--compress', '--chunked', httpbin + '/get',
             tolerate_error_exit_status=True)
    assert r.exit_status == ExitStatus.ERROR
    assert 'cannot combine --compress and --chunked' in r.stderr


def test_cannot_combine_compress_with_multipart(httpbin):
    r = http('--compress', '--multipart', httpbin + '/get',
             tolerate_error_exit_status=True)
    assert r.exit_status == ExitStatus.ERROR
    assert 'cannot combine --compress and --multipart' in r.stderr


def test_compress_skip_negative_ratio(httpbin_both):
    r = http(
        '--compress',
        httpbin_both + '/post',
        'foo=bar',
    )
    assert HTTP_OK in r
    assert 'Content-Encoding' not in r.json['headers']
    assert r.json['json'] == {'foo': 'bar'}


def test_compress_force_with_negative_ratio(httpbin_both):
    r = http(
        '--compress',
        '--compress',
        httpbin_both + '/post',
        'foo=bar',
    )
    assert HTTP_OK in r
    assert r.json['headers']['Content-Encoding'] == 'deflate'
    assert_decompressed_equal(r.json['data'], '{"foo": "bar"}')


def test_compress_json(httpbin_both):
    r = http(
        '--compress',
        '--compress',
        httpbin_both + '/post',
        'foo=bar',
    )
    assert HTTP_OK in r
    assert r.json['headers']['Content-Encoding'] == 'deflate'
    assert_decompressed_equal(r.json['data'], '{"foo": "bar"}')
    assert r.json['json'] is None


def test_compress_form(httpbin_both):
    r = http(
        '--form',
        '--compress',
        '--compress',
        httpbin_both + '/post',
        'foo=bar',
    )
    assert HTTP_OK in r
    assert r.json['headers']['Content-Encoding'] == 'deflate'
    assert r.json['data'] == ""
    assert '"foo": "bar"' not in r


def test_compress_raw(httpbin_both):
    r = http(
        '--raw',
        FILE_CONTENT,
        '--compress',
        '--compress',
        httpbin_both + '/post',
    )
    assert HTTP_OK in r
    assert r.json['headers']['Content-Encoding'] == 'deflate'
    assert_decompressed_equal(r.json['data'], FILE_CONTENT.strip())


def test_compress_stdin(httpbin_both):
    env = MockEnvironment(
        stdin=StdinBytesIO(FILE_PATH.read_bytes()),
        stdin_isatty=False,
    )
    r = http(
        '--compress',
        '--compress',
        'PATCH',
        httpbin_both + '/patch',
        env=env,
    )
    assert HTTP_OK in r
    assert r.json['headers']['Content-Encoding'] == 'deflate'
    assert_decompressed_equal(r.json['data'], FILE_CONTENT.strip())
    assert not r.json['json']


def test_compress_file(httpbin_both):
    r = http(
        '--form',
        '--compress',
        '--compress',
        'PUT',
        httpbin_both + '/put',
        f'file@{FILE_PATH}',
    )
    assert HTTP_OK in r
    assert r.json['headers']['Content-Encoding'] == 'deflate'
    assert r.json['headers']['Content-Type'].startswith(
        'multipart/form-data; boundary=')
    assert r.json['files'] == {}
    assert FILE_CONTENT not in r


def test_compress_skip_negative_ratio_file_upload(httpbin):
    """Reading the upload body but not compressing it doesn't cause a hang."""
    r = http(
        '--compress',
        'PUT',
        httpbin + '/put',
        f'@{TINY_FILE_PATH_ARG}',
    )
    assert HTTP_OK in r
    assert 'Content-Encoding' not in r.json['headers']
    assert r.json['data'].strip() == 'test'
