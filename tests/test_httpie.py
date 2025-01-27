"""High-level tests."""
import io
from unittest import mock

import pytest

import httpie
import httpie.__main__
from .fixtures import FILE_CONTENT, FILE_PATH
from httpie.cli.exceptions import ParseError
from httpie.context import Environment
from httpie.encoding import UTF8
from httpie.status import ExitStatus
from .utils import HTTP_OK, MockEnvironment, StdinBytesIO, http
import httpie.cli.argtemplate
import tempfile
import json


def test_main_entry_point():
    # Patch stdin to bypass pytest capture
    with mock.patch.object(Environment, 'stdin', io.StringIO()):
        assert httpie.__main__.main() == ExitStatus.ERROR.value


@mock.patch('httpie.core.main')
def test_main_entry_point_keyboard_interrupt(main):
    main.side_effect = KeyboardInterrupt()
    with mock.patch.object(Environment, 'stdin', io.StringIO()):
        assert httpie.__main__.main() == ExitStatus.ERROR_CTRL_C.value


def test_debug():
    r = http('--debug')
    assert r.exit_status == ExitStatus.SUCCESS
    assert f'HTTPie {httpie.__version__}' in r.stderr


def test_help():
    r = http('--help', tolerate_error_exit_status=True)
    assert r.exit_status == ExitStatus.SUCCESS
    assert 'https://github.com/httpie/cli/issues' in r


def test_version():
    r = http('--version', tolerate_error_exit_status=True)
    assert r.exit_status == ExitStatus.SUCCESS
    assert httpie.__version__ == r.strip()


def test_GET(httpbin_both):
    r = http('GET', httpbin_both + '/get')
    assert HTTP_OK in r


def test_path_dot_normalization():
    r = http(
        '--offline',
        'example.org/../../etc/password',
        'param==value'
    )
    assert 'GET /etc/password?param=value' in r


def test_path_as_is():
    r = http(
        '--offline',
        '--path-as-is',
        'example.org/../../etc/password',
        'param==value'
    )
    assert 'GET /../../etc/password?param=value' in r


def test_DELETE(httpbin_both):
    r = http('DELETE', httpbin_both + '/delete')
    assert HTTP_OK in r


def test_PUT(httpbin_both):
    r = http('PUT', httpbin_both + '/put', 'foo=bar')
    assert HTTP_OK in r
    assert r.json['json']['foo'] == 'bar'


def test_POST_JSON_data(httpbin_both):
    r = http('POST', httpbin_both + '/post', 'foo=bar')
    assert HTTP_OK in r
    assert r.json['json']['foo'] == 'bar'


def test_POST_form(httpbin_both):
    r = http('--form', 'POST', httpbin_both + '/post', 'foo=bar')
    assert HTTP_OK in r
    assert '"foo": "bar"' in r


def test_POST_form_multiple_values(httpbin_both):
    r = http('--form', 'POST', httpbin_both + '/post', 'foo=bar', 'foo=baz')
    assert HTTP_OK in r
    assert r.json['form'] == {
        'foo': ['bar', 'baz']
    }


def test_POST_raw(httpbin_both):
    r = http('--raw', 'foo bar', 'POST', httpbin_both + '/post')
    assert HTTP_OK in r
    assert '"foo bar"' in r


def test_POST_stdin(httpbin_both):
    env = MockEnvironment(
        stdin=StdinBytesIO(FILE_PATH.read_bytes()),
        stdin_isatty=False,
    )
    r = http('--form', 'POST', httpbin_both + '/post', env=env)
    assert HTTP_OK in r
    assert FILE_CONTENT in r


def test_POST_file(httpbin_both):
    r = http('--form', 'POST', httpbin_both + '/post', f'file@{FILE_PATH}')
    assert HTTP_OK in r
    assert FILE_CONTENT in r


def test_form_POST_file_redirected_stdin(httpbin):
    """
    <https://github.com/httpie/cli/issues/840>

    """
    with open(FILE_PATH, encoding=UTF8):
        r = http(
            '--form',
            'POST',
            httpbin + '/post',
            f'file@{FILE_PATH}',
            tolerate_error_exit_status=True,
            env=MockEnvironment(
                stdin=StdinBytesIO(FILE_PATH.read_bytes()),
                stdin_isatty=False,
            ),
        )
    assert r.exit_status == ExitStatus.ERROR
    assert 'cannot be mixed' in r.stderr


def test_raw_POST_key_values_supplied(httpbin):
    r = http(
        '--raw',
        'foo bar',
        'POST',
        httpbin + '/post',
        'foo=bar',
        tolerate_error_exit_status=True,
    )
    assert r.exit_status == ExitStatus.ERROR
    assert 'cannot be mixed' in r.stderr


def test_raw_POST_redirected_stdin(httpbin):
    r = http(
        '--raw',
        'foo bar',
        'POST',
        httpbin + '/post',
        tolerate_error_exit_status=True,
        env=MockEnvironment(
            stdin='some=value',
            stdin_isatty=False,
        ),
    )
    assert r.exit_status == ExitStatus.ERROR
    assert 'cannot be mixed' in r.stderr


def test_headers(httpbin_both):
    r = http('GET', httpbin_both + '/headers', 'Foo:bar')
    assert HTTP_OK in r
    assert '"User-Agent": "HTTPie' in r, r
    assert '"Foo": "bar"' in r


def test_headers_unset(httpbin_both):
    r = http('GET', httpbin_both + '/headers')
    assert 'Accept' in r.json['headers']  # default Accept present

    r = http('GET', httpbin_both + '/headers', 'Accept:')
    assert 'Accept' not in r.json['headers']  # default Accept unset


@pytest.mark.skip('unimplemented')
def test_unset_host_header(httpbin_both):
    r = http('GET', httpbin_both + '/headers')
    assert 'Host' in r.json['headers']  # default Host present

    r = http('GET', httpbin_both + '/headers', 'Host:')
    assert 'Host' not in r.json['headers']  # default Host unset


def test_unset_useragent_header(httpbin_both):
    r = http('GET', httpbin_both + '/headers')
    assert 'User-Agent' in r.json['headers']  # default User-Agent present

    r = http('GET', httpbin_both + '/headers', 'User-Agent:')
    assert 'User-Agent' not in r.json['headers']  # default User-Agent unset


def test_headers_empty_value(httpbin_both):
    r = http('GET', httpbin_both + '/headers')
    assert r.json['headers']['Accept']  # default Accept has value

    r = http('GET', httpbin_both + '/headers', 'Accept;')
    assert r.json['headers']['Accept'] == ''  # Accept has no value


def test_headers_empty_value_with_value_gives_error(httpbin):
    with pytest.raises(ParseError):
        http('GET', httpbin + '/headers', 'Accept;SYNTAX_ERROR')


def test_headers_omit(httpbin_both):
    r = http('GET', httpbin_both + '/headers', 'Accept:')
    assert 'Accept' not in r.json['headers']


def test_headers_multiple_omit(httpbin_both):
    r = http('GET', httpbin_both + '/headers', 'Foo:bar', 'Bar:baz',
             'Foo:', 'Baz:quux')
    assert 'Foo' not in r.json['headers']
    assert r.json['headers']['Bar'] == 'baz'
    assert r.json['headers']['Baz'] == 'quux'


def test_headers_same_after_omit(httpbin_both):
    r = http('GET', httpbin_both + '/headers', 'Foo:bar', 'Foo:',
             'Foo:quux')
    assert r.json['headers']['Foo'] == 'quux'


def test_headers_fully_omit(httpbin_both):
    r = http('GET', httpbin_both + '/headers', 'Foo:bar', 'Foo:baz',
             'Foo:')
    assert 'Foo' not in r.json['headers']


def test_headers_multiple_values(httpbin_both):
    r = http('GET', httpbin_both + '/headers', 'Foo:bar', 'Foo:baz')
    assert r.json['headers']['Foo'] == 'bar,baz'


def test_headers_multiple_values_repeated(httpbin_both):
    r = http('GET', httpbin_both + '/headers', 'Foo:bar', 'Foo:baz',
             'Foo:bar')
    assert r.json['headers']['Foo'] == 'bar,baz,bar'


@pytest.mark.parametrize("headers, expected", [
    (
        ["Foo;", "Foo:bar"],
        ",bar"
    ),
    (
        ["Foo:bar", "Foo;"],
        "bar,"
    ),
    (
        ["Foo:bar", "Foo;", "Foo:baz"],
        "bar,,baz"
    ),
])
def test_headers_multiple_values_with_empty(httpbin_both, headers, expected):
    r = http('GET', httpbin_both + '/headers', *headers)
    assert r.json['headers']['Foo'] == expected


def test_headers_multiple_values_mixed(httpbin_both):
    r = http('GET', httpbin_both + '/headers', 'Foo:bar', 'Vary:XXX',
             'Foo:baz', 'Vary:YYY', 'Foo:quux')
    assert r.json['headers']['Vary'] == 'XXX,YYY'
    assert r.json['headers']['Foo'] == 'bar,baz,quux'


def test_headers_preserve_prepared_headers(httpbin_both):
    r = http('POST', httpbin_both + '/post', 'Content-Length:0',
             '--raw', 'foo')
    assert r.json['headers']['Content-Length'] == '3'


@pytest.mark.parametrize('pretty', ['format', 'none'])
def test_headers_multiple_headers_representation(httpbin_both, pretty):
    r = http('--offline', '--pretty', pretty, 'example.org',
             'A:A', 'A:B', 'A:C', 'B:A', 'B:B', 'C:C', 'c:c')

    assert 'A: A' in r
    assert 'A: B' in r
    assert 'A: C' in r
    assert 'B: A' in r
    assert 'B: B' in r
    assert 'C: C' in r
    assert 'c: c' in r


def test_response_headers_multiple(http_server):
    r = http('GET', http_server + '/headers', 'Foo:bar', 'Foo:baz')
    assert 'Foo: bar' in r
    assert 'Foo: baz' in r


def test_response_headers_multiple_repeated(http_server):
    r = http('GET', http_server + '/headers', 'Foo:bar', 'Foo:baz',
             'Foo:bar')
    assert r.count('Foo: bar') == 2
    assert 'Foo: baz' in r


@pytest.mark.parametrize('pretty', ['format', 'none'])
def test_response_headers_multiple_representation(http_server, pretty):
    r = http('--pretty', pretty, http_server + '/headers',
             'A:A', 'A:B', 'A:C', 'B:A', 'B:B', 'C:C', 'C:c')

    assert 'A: A' in r
    assert 'A: B' in r
    assert 'A: C' in r
    assert 'B: A' in r
    assert 'B: B' in r
    assert 'C: C' in r
    assert 'C: c' in r


def test_json_input_preserve_order(httpbin_both):
    r = http('PATCH', httpbin_both + '/patch',
             'order:={"map":{"1":"first","2":"second"}}')
    assert HTTP_OK in r
    assert r.json['data'] == \
        '{"order": {"map": {"1": "first", "2": "second"}}}'


@pytest.mark.parametrize('extra_args, expected_content_length', [
    (
        ['Content-Length:0'],
        '0'
    ),
    (
        ['Content-Length:xxx'],
        'xxx',
    ),
    (
        ['--raw=data'],
        '4'
    ),
    (
        ['query[param]=something'],
        '33'
    )
])
def test_options_content_length_preservation(httpbin, extra_args, expected_content_length):
    r = http(
        '--offline',
        'OPTIONS',
        httpbin + '/anything',
        *extra_args
    )
    assert f'Content-Length: {expected_content_length}' in r


@pytest.mark.parametrize('method', ['options', 'Options', 'OPTIONS'])
def test_options_dropping_redundant_content_length(httpbin, method):
    r = http(
        '--offline',
        method,
        httpbin + '/anything'
    )
    assert 'Content-Length' not in r


def test_store_template():
    """
    Tests that a template can be stored with the command 'http template <name> <method> <url> ...'
    """
    with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
        httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

        http('template', 'test_template', 'GET', 'https://catfact.ninja/fact', '--traceback', '--timeout=3')

        template = json.load(temp_fp)
        assert 'test_template' in template


def test_run_template(httpbin_both):
    """
    Tests that a template can be loaded with the command 'http runt <template>'
    """
    with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
        httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name

        r = http('template', 'test_template', 'GET', httpbin_both + '/get', '--debug')  # store template

        r = http('runt', 'test_template', '--traceback', '--timeout=3', tolerate_error_exit_status=True)  # run it

        assert HTTP_OK in r


def test_delete_template():
    """
    Tests that a template can be deleted with the command 'http delt <template>'
    """
    with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
        httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name
        http('template', 'test_template', 'GET', 'https://catfact.ninja/fact', '--traceback', '--timeout=3')  # store template

        http('delt', 'test_template', '--traceback', '--timeout=3', tolerate_error_exit_status=True)  # delete it

        stored_templates = json.load(temp_fp)
        assert 'test_template' not in stored_templates


def test_edit_template():
    """
    Tests that a template can be edited with the command 'http editt <template>'
    """
    with tempfile.NamedTemporaryFile('w+', delete=False) as temp_fp:
        httpie.cli.argtemplate.TEMPLATE_FILE = temp_fp.name
        http('template', 'test_template', 'GET', 'https://catfact.ninja/fact', '--traceback', '--timeout=3')  # store template

        http('editt', 'test_template', 'param', 'value', '--debug')  # edit it
        stored_templates = json.load(temp_fp)
        assert 'param' in stored_templates['test_template']['data']
