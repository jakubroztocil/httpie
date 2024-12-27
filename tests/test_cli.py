"""CLI argument parsing related tests."""
import argparse

import pytest
from requests.exceptions import InvalidSchema

import httpie.cli.argparser
from httpie.cli import constants
from httpie.cli.definition import parser
from httpie.cli.argtypes import KeyValueArg, KeyValueArgType
from httpie.cli.requestitems import RequestItems
from httpie.status import ExitStatus
from httpie.utils import load_json_preserve_order_and_dupe_keys

from .fixtures import (
    FILE_CONTENT, FILE_PATH, FILE_PATH_ARG, JSON_FILE_CONTENT,
    JSON_FILE_PATH_ARG,
)
from .utils import HTTP_OK, MockEnvironment, StdinBytesIO, http


class TestItemParsing:
    key_value_arg = KeyValueArgType(*constants.SEPARATOR_GROUP_ALL_ITEMS)

    def test_invalid_items(self):
        items = ['no-separator']
        for item in items:
            pytest.raises(argparse.ArgumentTypeError, self.key_value_arg, item)

    def test_escape_separator(self):
        items = RequestItems.from_args([
            # headers
            self.key_value_arg(r'foo\:bar:baz'),
            self.key_value_arg(r'jack\@jill:hill'),

            # data
            self.key_value_arg(r'baz\=bar=foo'),

            # files
            self.key_value_arg(fr'bar\@baz@{FILE_PATH_ARG}'),
        ])
        # `HTTPHeadersDict` => `dict`
        headers = dict(items.headers)

        assert headers == {
            'foo:bar': 'baz',
            'jack@jill': 'hill',
        }
        assert items.data == {
            'baz=bar': 'foo'
        }
        assert 'bar@baz' in items.files

    @pytest.mark.parametrize('string, key, sep, value', [
        ('path=c:\\windows', 'path', '=', 'c:\\windows'),
        ('path=c:\\windows\\', 'path', '=', 'c:\\windows\\'),
        ('path\\==c:\\windows', 'path=', '=', 'c:\\windows'),
    ])
    def test_backslash_before_non_special_character_does_not_escape(
        self, string, key, sep, value
    ):
        expected = KeyValueArg(orig=string, key=key, sep=sep, value=value)
        actual = self.key_value_arg(string)
        assert actual == expected

    def test_escape_longsep(self):
        items = RequestItems.from_args([
            self.key_value_arg(r'bob\:==foo'),
        ])
        assert items.params == {
            'bob:': 'foo'
        }

    def test_valid_items(self):
        items = RequestItems.from_args([
            self.key_value_arg('string=value'),
            self.key_value_arg('Header:value'),
            self.key_value_arg('Unset-Header:'),
            self.key_value_arg('Empty-Header;'),
            self.key_value_arg('list:=["a", 1, {}, false]'),
            self.key_value_arg('obj:={"a": "b"}'),
            self.key_value_arg(r'nested\[2\][a][]=1'),
            self.key_value_arg('nested[2][a][]:=1'),
            self.key_value_arg('ed='),
            self.key_value_arg('bool:=true'),
            self.key_value_arg('file@' + FILE_PATH_ARG),
            self.key_value_arg('query==value'),
            self.key_value_arg('Embedded-Header:@' + FILE_PATH_ARG),
            self.key_value_arg('string-embed=@' + FILE_PATH_ARG),
            self.key_value_arg('param-embed==@' + FILE_PATH_ARG),
            self.key_value_arg('raw-json-embed:=@' + JSON_FILE_PATH_ARG),
        ])

        # Parsed headers
        # `HTTPHeadersDict` => `dict`
        headers = dict(items.headers)
        assert headers == {
            'Header': 'value',
            'Unset-Header': None,
            'Empty-Header': '',
            'Embedded-Header': FILE_CONTENT.rstrip('\n')
        }

        # Parsed data
        raw_json_embed = items.data.pop('raw-json-embed')
        assert raw_json_embed == load_json_preserve_order_and_dupe_keys(JSON_FILE_CONTENT)
        items.data['string-embed'] = items.data['string-embed'].strip()
        assert dict(items.data) == {
            'ed': '',
            'string': 'value',
            'bool': True,
            'list': ['a', 1, load_json_preserve_order_and_dupe_keys('{}'), False],
            'nested[2]': {'a': ['1']},
            'nested': [None, None, {'a': [1]}],
            'obj': load_json_preserve_order_and_dupe_keys('{"a": "b"}'),
            'string-embed': FILE_CONTENT
        }

        # Parsed query string parameters
        assert items.params == {
            'query': 'value',
            'param-embed': FILE_CONTENT.rstrip('\n')
        }

        # Parsed file fields
        assert 'file' in items.files
        assert (items.files['file'][1].read().strip().
                decode() == FILE_CONTENT)

    def test_multiple_file_fields_with_same_field_name(self):
        items = RequestItems.from_args([
            self.key_value_arg('file_field@' + FILE_PATH_ARG),
            self.key_value_arg('file_field@' + FILE_PATH_ARG),
        ])
        assert len(items.files['file_field']) == 2

    def test_multiple_text_fields_with_same_field_name(self):
        items = RequestItems.from_args(
            request_item_args=[
                self.key_value_arg('text_field=a'),
                self.key_value_arg('text_field=b')
            ],
            request_type=constants.RequestType.FORM,
        )
        assert items.data['text_field'] == ['a', 'b']
        assert list(items.data.items()) == [
            ('text_field', 'a'),
            ('text_field', 'b'),
        ]


class TestQuerystring:
    def test_query_string_params_in_url(self, httpbin):
        r = http('--print=Hhb', 'GET', httpbin + '/get?a=1&b=2')
        path = '/get?a=1&b=2'
        url = httpbin + path
        assert HTTP_OK in r
        assert f'GET {path} HTTP/1.1' in r
        assert f'"url": "{url}"' in r

    def test_query_string_params_items(self, httpbin):
        r = http('--print=Hhb', 'GET', httpbin + '/get', 'a==1')
        path = '/get?a=1'
        url = httpbin + path
        assert HTTP_OK in r
        assert f'GET {path} HTTP/1.1' in r
        assert f'"url": "{url}"' in r

    def test_query_string_params_in_url_and_items_with_duplicates(self,
                                                                  httpbin):
        r = http('--print=Hhb', 'GET',
                 httpbin + '/get?a=1&a=1', 'a==1', 'a==1')
        path = '/get?a=1&a=1&a=1&a=1'
        url = httpbin + path
        assert HTTP_OK in r
        assert f'GET {path} HTTP/1.1' in r
        assert f'"url": "{url}"' in r


@pytest.mark.parametrize(['program_name', 'url_arg', 'parsed_url'], [
    ('http', '://pie.dev/get', 'http://pie.dev/get'),
    ('https', '://pie.dev/get', 'https://pie.dev/get'),
])
def test_url_leading_colon_slash_slash(program_name, url_arg, parsed_url):
    env = MockEnvironment(program_name=program_name)
    args = parser.parse_args(args=[url_arg], env=env)
    assert args.url == parsed_url


def test_url_colon_slash_slash_only():
    r = http('://', tolerate_error_exit_status=True)
    assert r.stderr.strip() == "http: error: InvalidURL: Invalid URL 'http://': No host supplied"


class TestLocalhostShorthand:
    def test_expand_localhost_shorthand(self):
        args = parser.parse_args(args=[':'], env=MockEnvironment())
        assert args.url == 'http://localhost'

    def test_expand_localhost_shorthand_with_slash(self):
        args = parser.parse_args(args=[':/'], env=MockEnvironment())
        assert args.url == 'http://localhost/'

    def test_expand_localhost_shorthand_with_port(self):
        args = parser.parse_args(args=[':3000'], env=MockEnvironment())
        assert args.url == 'http://localhost:3000'

    def test_expand_localhost_shorthand_with_path(self):
        args = parser.parse_args(args=[':/path'], env=MockEnvironment())
        assert args.url == 'http://localhost/path'

    def test_expand_localhost_shorthand_with_port_and_slash(self):
        args = parser.parse_args(args=[':3000/'], env=MockEnvironment())
        assert args.url == 'http://localhost:3000/'

    def test_expand_localhost_shorthand_with_port_and_path(self):
        args = parser.parse_args(args=[':3000/path'], env=MockEnvironment())
        assert args.url == 'http://localhost:3000/path'

    def test_dont_expand_shorthand_ipv6_as_shorthand(self):
        args = parser.parse_args(args=['::1'], env=MockEnvironment())
        assert args.url == 'http://::1'

    def test_dont_expand_longer_ipv6_as_shorthand(self):
        args = parser.parse_args(
            args=['::ffff:c000:0280'],
            env=MockEnvironment()
        )
        assert args.url == 'http://::ffff:c000:0280'

    def test_dont_expand_full_ipv6_as_shorthand(self):
        args = parser.parse_args(
            args=['0000:0000:0000:0000:0000:0000:0000:0001'],
            env=MockEnvironment()
        )
        assert args.url == 'http://0000:0000:0000:0000:0000:0000:0000:0001'


class TestArgumentParser:

    def setup_method(self, method):
        self.parser = httpie.cli.argparser.HTTPieArgumentParser()

    def test_guess_when_method_set_and_valid(self):
        self.parser.args = argparse.Namespace()
        self.parser.args.method = 'GET'
        self.parser.args.url = 'http://example.com/'
        self.parser.args.request_items = []
        self.parser.args.ignore_stdin = False
        self.parser.env = MockEnvironment()
        self.parser._guess_method()
        assert self.parser.args.method == 'GET'
        assert self.parser.args.url == 'http://example.com/'
        assert self.parser.args.request_items == []

    def test_guess_when_method_not_set(self):
        self.parser.args = argparse.Namespace()
        self.parser.args.method = None
        self.parser.args.url = 'http://example.com/'
        self.parser.args.request_items = []
        self.parser.args.ignore_stdin = False
        self.parser.env = MockEnvironment()
        self.parser._guess_method()
        assert self.parser.args.method == 'GET'
        assert self.parser.args.url == 'http://example.com/'
        assert self.parser.args.request_items == []

    def test_guess_when_method_set_but_invalid_and_data_field(self):
        self.parser.args = argparse.Namespace()
        self.parser.args.method = 'http://example.com/'
        self.parser.args.url = 'data=field'
        self.parser.args.request_items = []
        self.parser.args.ignore_stdin = False
        self.parser.env = MockEnvironment()
        self.parser._guess_method()
        assert self.parser.args.method == 'POST'
        assert self.parser.args.url == 'http://example.com/'
        assert self.parser.args.request_items == [
            KeyValueArg(key='data',
                        value='field',
                        sep='=',
                        orig='data=field')
        ]

    def test_guess_when_method_set_but_invalid_and_header_field(self):
        self.parser.args = argparse.Namespace()
        self.parser.args.method = 'http://example.com/'
        self.parser.args.url = 'test:header'
        self.parser.args.request_items = []
        self.parser.args.ignore_stdin = False
        self.parser.env = MockEnvironment()
        self.parser._guess_method()
        assert self.parser.args.method == 'GET'
        assert self.parser.args.url == 'http://example.com/'
        assert self.parser.args.request_items, [
            KeyValueArg(key='test',
                        value='header',
                        sep=':',
                        orig='test:header')
        ]

    def test_guess_when_method_set_but_invalid_and_item_exists(self):
        self.parser.args = argparse.Namespace()
        self.parser.args.method = 'http://example.com/'
        self.parser.args.url = 'new_item=a'
        self.parser.args.request_items = [
            KeyValueArg(
                key='old_item', value='b', sep='=', orig='old_item=b')
        ]
        self.parser.args.ignore_stdin = False
        self.parser.env = MockEnvironment()
        self.parser._guess_method()
        assert self.parser.args.request_items, [
            KeyValueArg(key='new_item', value='a', sep='=', orig='new_item=a'),
            KeyValueArg(
                key='old_item', value='b', sep='=', orig='old_item=b'),
        ]


class TestNoOptions:

    def test_valid_no_options(self, httpbin):
        r = http('--verbose', '--no-verbose', 'GET', httpbin + '/get')
        assert 'GET /get HTTP/1.1' not in r

    def test_invalid_no_options(self, httpbin):
        r = http('--no-war', 'GET', httpbin + '/get',
                 tolerate_error_exit_status=True)
        assert r.exit_status == ExitStatus.ERROR
        assert 'unrecognized arguments: --no-war' in r.stderr
        assert 'GET /get HTTP/1.1' not in r


class TestStdin:

    def test_ignore_stdin(self, httpbin):
        env = MockEnvironment(
            stdin=StdinBytesIO(FILE_PATH.read_bytes()),
            stdin_isatty=False,
        )
        r = http('--ignore-stdin', '--verbose', httpbin + '/get', env=env)
        assert HTTP_OK in r
        assert 'GET /get HTTP' in r, "Don't default to POST."
        assert FILE_CONTENT not in r, "Don't send stdin data."

    def test_ignore_stdin_cannot_prompt_password(self, httpbin):
        r = http('--ignore-stdin', '--auth=no-password', httpbin + '/get',
                 tolerate_error_exit_status=True)
        assert r.exit_status == ExitStatus.ERROR
        assert 'because --ignore-stdin' in r.stderr

    def test_stdin_closed(self, httpbin):
        r = http(httpbin + '/get', env=MockEnvironment(stdin=None))
        assert HTTP_OK in r


class TestSchemes:

    def test_invalid_custom_scheme(self):
        # InvalidSchema is expected because HTTPie
        # shouldn't touch a formally valid scheme.
        with pytest.raises(InvalidSchema):
            http('foo+bar-BAZ.123://bah')

    def test_invalid_scheme_via_via_default_scheme(self):
        # InvalidSchema is expected because HTTPie
        # shouldn't touch a formally valid scheme.
        with pytest.raises(InvalidSchema):
            http('bah', '--default=scheme=foo+bar-BAZ.123')

    def test_default_scheme_option(self, httpbin_secure):
        url = f'{httpbin_secure.host}:{httpbin_secure.port}'
        assert HTTP_OK in http(url, '--default-scheme=https')

    def test_scheme_when_invoked_as_https(self, httpbin_secure):
        url = f'{httpbin_secure.host}:{httpbin_secure.port}'
        assert HTTP_OK in http(url, program_name='https')


class TestBaseUrl:
    def test_base_url(self):
        assert False, 'Needs tests'
