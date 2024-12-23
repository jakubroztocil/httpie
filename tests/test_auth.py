"""HTTP authentication-related tests."""
from unittest import mock
import pytest

from httpie.plugins.builtin import HTTPBasicAuth
from httpie.status import ExitStatus
from httpie.utils import ExplicitNullAuth
from .utils import http, add_auth, HTTP_OK, MockEnvironment
import httpie.cli.constants
import httpie.cli.definition


def test_basic_auth(httpbin_both):
    r = http('--auth=user:password',
             'GET', httpbin_both + '/basic-auth/user/password')
    assert HTTP_OK in r
    assert r.json == {'authenticated': True, 'user': 'user'}


@pytest.mark.parametrize('argument_name', ['--auth-type', '-A'])
def test_digest_auth(httpbin_both, argument_name):
    r = http(argument_name + '=digest', '--auth=user:password',
             'GET', httpbin_both.url + '/digest-auth/auth/user/password')
    assert HTTP_OK in r
    assert r.json == {'authenticated': True, 'user': 'user'}


@pytest.mark.parametrize('token', [
    'token_1',
    'long_token' * 5,
    'user:style',
])
def test_bearer_auth(httpbin_both, token):
    r = http('--auth-type', 'bearer', '--auth', token,
             httpbin_both + '/bearer')

    assert HTTP_OK in r
    assert r.json == {'authenticated': True, 'token': token}


@mock.patch('httpie.cli.argtypes.AuthCredentials._getpass',
            new=lambda self, prompt: 'password')
def test_password_prompt(httpbin):
    r = http('--auth', 'user',
             'GET', httpbin + '/basic-auth/user/password')
    assert HTTP_OK in r
    assert r.json == {'authenticated': True, 'user': 'user'}


def test_credentials_in_url(httpbin_both):
    url = add_auth(httpbin_both.url + '/basic-auth/user/password',
                   auth='user:password')
    r = http('GET', url)
    assert HTTP_OK in r
    assert r.json == {'authenticated': True, 'user': 'user'}


def test_credentials_in_url_auth_flag_has_priority(httpbin_both):
    """When credentials are passed in URL and via -a at the same time,
     then the ones from -a are used."""
    url = add_auth(httpbin_both.url + '/basic-auth/user/password',
                   auth='user:wrong')
    r = http('--auth=user:password', 'GET', url)
    assert HTTP_OK in r
    assert r.json == {'authenticated': True, 'user': 'user'}


@pytest.mark.parametrize('url', [
    'username@example.org',
    'username:@example.org',
])
def test_only_username_in_url(url):
    """
    https://github.com/httpie/cli/issues/242

    """
    args = httpie.cli.definition.parser.parse_args(args=[url], env=MockEnvironment())
    assert args.auth
    assert args.auth.username == 'username'
    assert args.auth.password == ''


def test_missing_auth(httpbin):
    r = http(
        '--auth-type=basic',
        'GET',
        httpbin + '/basic-auth/user/password',
        tolerate_error_exit_status=True
    )
    assert HTTP_OK not in r
    assert '--auth required' in r.stderr


def test_netrc(httpbin_both):
    # This one gets handled by requests (no --auth, --auth-type present),
    # that’s why we patch inside `requests.sessions`.
    with mock.patch('requests.sessions.get_netrc_auth') as get_netrc_auth:
        get_netrc_auth.return_value = ('httpie', 'password')
        r = http(httpbin_both + '/basic-auth/httpie/password')
        assert get_netrc_auth.call_count == 1
        assert HTTP_OK in r


def test_ignore_netrc(httpbin_both):
    with mock.patch('httpie.cli.argparser.get_netrc_auth') as get_netrc_auth:
        get_netrc_auth.return_value = ('httpie', 'password')
        r = http('--ignore-netrc', httpbin_both + '/basic-auth/httpie/password')
        assert get_netrc_auth.call_count == 0
        assert 'HTTP/1.1 401 UNAUTHORIZED' in r


def test_ignore_netrc_together_with_auth():
    args = httpie.cli.definition.parser.parse_args(
        args=['--ignore-netrc', '--auth=username:password', 'example.org'],
        env=MockEnvironment(),
    )
    assert isinstance(args.auth, HTTPBasicAuth)


def test_ignore_netrc_with_auth_type_resulting_in_missing_auth(httpbin):
    with mock.patch('httpie.cli.argparser.get_netrc_auth') as get_netrc_auth:
        get_netrc_auth.return_value = ('httpie', 'password')
        r = http(
            '--ignore-netrc',
            '--auth-type=basic',
            httpbin + '/basic-auth/httpie/password',
            tolerate_error_exit_status=True,
        )
    assert get_netrc_auth.call_count == 0
    assert r.exit_status == ExitStatus.ERROR
    assert '--auth required' in r.stderr


@pytest.mark.parametrize(
    'auth_type, endpoint',
    [
        ('basic', '/basic-auth/httpie/password'),
        ('digest', '/digest-auth/auth/httpie/password'),
    ]
)
def test_auth_plugin_netrc_parse(auth_type, endpoint, httpbin):
    # Test
    with mock.patch('httpie.cli.argparser.get_netrc_auth') as get_netrc_auth:
        get_netrc_auth.return_value = ('httpie', 'password')
        r = http('--auth-type', auth_type, httpbin + endpoint)
    assert get_netrc_auth.call_count == 1
    assert HTTP_OK in r


def test_ignore_netrc_null_auth():
    args = httpie.cli.definition.parser.parse_args(
        args=['--ignore-netrc', 'example.org'],
        env=MockEnvironment(),
    )
    assert isinstance(args.auth, ExplicitNullAuth)


@pytest.mark.parametrize('argument_name', ['--auth-type', '-A'])
def test_digest_auth_auth_header(httpbin_both, argument_name):
    r = http(argument_name + '=digest', '--auth=user:password',
             'GET', httpbin_both.url + '/digest-auth/auth/user/password', '-v')
    assert 'Authorization: Digest username="user", realm="me@kennethreitz.com"' in r
