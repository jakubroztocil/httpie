"""Miscellaneous regression tests"""
import pytest

from httpie.cli.argtypes import KeyValueArgType
from httpie.cli.constants import SEPARATOR_HEADER, SEPARATOR_QUERY_PARAM, SEPARATOR_DATA_STRING
from httpie.cli.requestitems import RequestItems
from httpie.compat import is_windows
from .utils import HTTP_OK, MockEnvironment, http
from .utils.matching import assert_output_matches, Expect


def test_Host_header_overwrite(httpbin):
    """
    https://github.com/httpie/cli/issues/235

    """
    host = 'pie.dev'
    url = httpbin + '/get'
    r = http('--print=hH', url, f'host:{host}')
    assert HTTP_OK in r
    assert r.lower().count('host:') == 1
    assert f'host: {host}' in r


@pytest.mark.skipif(is_windows, reason='Unix-only')
def test_output_devnull(httpbin):
    """
    https://github.com/httpie/cli/issues/252

    """
    http('--output=/dev/null', httpbin + '/get')


def test_verbose_redirected_stdout_separator(httpbin):
    """

    <https://github.com/httpie/cli/issues/1006>
    """
    r = http(
        '-v',
        httpbin + '/post',
        'a=b',
        env=MockEnvironment(stdout_isatty=False),
    )
    assert '}HTTP/' not in r
    assert_output_matches(r, [
        Expect.REQUEST_HEADERS,
        Expect.BODY,
        Expect.SEPARATOR,
        Expect.RESPONSE_HEADERS,
        Expect.BODY,
    ])


@pytest.mark.parametrize(['separator', 'target'], [
    (SEPARATOR_HEADER, 'headers'),
    (SEPARATOR_QUERY_PARAM, 'params'),
    (SEPARATOR_DATA_STRING, 'data'),
])
def test_initial_backslash_number(separator, target):
    """
    <https://github.com/httpie/httpie/issues/1408>
    """
    back_digit = r'\0'
    raw_arg = back_digit + separator + back_digit
    expected_parsed_data = {back_digit: back_digit}
    parsed_arg = KeyValueArgType(separator)(raw_arg)
    items = RequestItems.from_args([parsed_arg])
    parsed_data = getattr(items, target)
    assert parsed_data == expected_parsed_data
