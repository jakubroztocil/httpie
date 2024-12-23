import ssl

import pytest
import pytest_httpbin.certs
import requests.exceptions
import urllib3

from unittest import mock

from httpie.ssl_ import AVAILABLE_SSL_VERSION_ARG_MAPPING, DEFAULT_SSL_CIPHERS_STRING
from httpie.status import ExitStatus

from .utils import HTTP_OK, TESTS_ROOT, IS_PYOPENSSL, http


try:
    # Handle OpenSSL errors, if installed.
    # See <https://github.com/httpie/cli/issues/729>
    # noinspection PyUnresolvedReferences
    import OpenSSL.SSL
    ssl_errors = (
        requests.exceptions.SSLError,
        OpenSSL.SSL.Error,
        ValueError,  # TODO: Remove with OSS-65
    )
except ImportError:
    ssl_errors = (
        requests.exceptions.SSLError,
    )

CERTS_ROOT = TESTS_ROOT / 'client_certs'
CLIENT_CERT = str(CERTS_ROOT / 'client.crt')
CLIENT_KEY = str(CERTS_ROOT / 'client.key')
CLIENT_PEM = str(CERTS_ROOT / 'client.pem')

# In case of a regeneration, use the following commands
# in the PWD_TESTS_ROOT:
#   $ openssl genrsa -aes128 -passout pass:password 3072 > client.pem
#   $ openssl req -new -x509 -nodes -days 10000 -key client.pem > client.pem
PWD_TESTS_ROOT = CERTS_ROOT / 'password_protected'
PWD_CLIENT_PEM = str(PWD_TESTS_ROOT / 'client.pem')
PWD_CLIENT_KEY = str(PWD_TESTS_ROOT / 'client.key')
PWD_CLIENT_PASS = 'password'
PWD_CLIENT_INVALID_PASS = PWD_CLIENT_PASS + 'invalid'

# We test against a local httpbin instance which uses a self-signed cert.
# Requests without --verify=<CA_BUNDLE> will fail with a verification error.
# See: https://github.com/kevin1024/pytest-httpbin#https-support
CA_BUNDLE = pytest_httpbin.certs.where()


@pytest.mark.parametrize('ssl_version',
                         AVAILABLE_SSL_VERSION_ARG_MAPPING.keys())
def test_ssl_version(httpbin_secure, ssl_version):
    try:
        r = http(
            '--ssl', ssl_version,
            httpbin_secure + '/get'
        )
        assert HTTP_OK in r
    except ssl_errors as e:
        if ssl_version == 'ssl3':
            # pytest-httpbin doesn't support ssl3
            pass
        elif e.__context__ is not None:  # Check if root cause was an unsupported TLS version
            root = e.__context__
            while root.__context__ is not None:
                root = root.__context__
            if isinstance(root, ssl.SSLError) and root.reason == "TLSV1_ALERT_PROTOCOL_VERSION":
                pytest.skip(f'Unsupported TLS version: {ssl_version}')
        elif 'No such protocol' in str(e):  # TODO: Remove with OSS-65
            pytest.skip(f'Unsupported TLS version: {ssl_version}')
        else:
            raise


class TestClientCert:

    def test_cert_and_key(self, httpbin_secure):
        r = http(httpbin_secure + '/get',
                 '--cert', CLIENT_CERT,
                 '--cert-key', CLIENT_KEY)
        assert HTTP_OK in r

    def test_cert_pem(self, httpbin_secure):
        r = http(httpbin_secure + '/get',
                 '--cert', CLIENT_PEM)
        assert HTTP_OK in r

    def test_cert_file_not_found(self, httpbin_secure):
        r = http(httpbin_secure + '/get',
                 '--cert', '/__not_found__',
                 tolerate_error_exit_status=True)
        assert r.exit_status == ExitStatus.ERROR
        assert '/__not_found__: No such file or directory' in r.stderr

    def test_cert_file_invalid(self, httpbin_secure):
        with pytest.raises(ssl_errors):
            http(httpbin_secure + '/get',
                 '--cert', __file__)

    def test_cert_ok_but_missing_key(self, httpbin_secure):
        with pytest.raises(ssl_errors):
            http(httpbin_secure + '/get',
                 '--cert', CLIENT_CERT)


class TestServerCert:

    def test_verify_no_OK(self, httpbin_secure):
        # Avoid warnings when explicitly testing insecure requests
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = http(httpbin_secure.url + '/get', '--verify=no')
        assert HTTP_OK in r

    def test_verify_no_shortcut_OK(self, httpbin_secure):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = http(httpbin_secure.url + '/get', '-k')
        assert HTTP_OK in r

    @pytest.mark.parametrize('verify_value', ['false', 'fALse'])
    def test_verify_false_OK(self, httpbin_secure, verify_value):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = http(httpbin_secure.url + '/get', '--verify', verify_value)
        assert HTTP_OK in r

    def test_verify_custom_ca_bundle_path(
        self, httpbin_secure_untrusted
    ):
        r = http(httpbin_secure_untrusted + '/get', '--verify', CA_BUNDLE)
        assert HTTP_OK in r

    def test_self_signed_server_cert_by_default_raises_ssl_error(
        self,
        httpbin_secure_untrusted
    ):
        with pytest.raises(ssl_errors):
            http(httpbin_secure_untrusted.url + '/get')

    def test_verify_custom_ca_bundle_invalid_path(self, httpbin_secure):
        # since 2.14.0 requests raises IOError (an OSError subclass)
        with pytest.raises(ssl_errors + (OSError,)):
            http(httpbin_secure.url + '/get', '--verify', '/__not_found__')

    def test_verify_custom_ca_bundle_invalid_bundle(self, httpbin_secure):
        with pytest.raises(ssl_errors):
            http(httpbin_secure.url + '/get', '--verify', __file__)


def test_ciphers(httpbin_secure):
    r = http(
        httpbin_secure.url + '/get',
        '--ciphers',
        DEFAULT_SSL_CIPHERS_STRING,
    )
    assert HTTP_OK in r


@pytest.mark.skipif(IS_PYOPENSSL, reason='pyOpenSSL uses a different message format.')
def test_ciphers_none_can_be_selected(httpbin_secure):
    r = http(
        httpbin_secure.url + '/get',
        '--ciphers',
        '__FOO__',
        tolerate_error_exit_status=True,
    )
    assert r.exit_status == ExitStatus.ERROR
    # Linux/macOS:
    #   http: error: SSLError: ('No cipher can be selected.',)
    # OpenBSD:
    #   <https://marc.info/?l=openbsd-ports&m=159251948515635&w=2>
    #   http: error: Error: [('SSL routines', '(UNKNOWN)SSL_internal', 'no cipher match')]
    assert 'cipher' in r.stderr


def test_pyopenssl_presence():
    if not IS_PYOPENSSL:
        assert not urllib3.util.ssl_.IS_PYOPENSSL
        assert not urllib3.util.IS_PYOPENSSL
    else:
        assert urllib3.util.ssl_.IS_PYOPENSSL
        assert urllib3.util.IS_PYOPENSSL


@mock.patch('httpie.cli.argtypes.SSLCredentials._prompt_password',
            new=lambda self, prompt: PWD_CLIENT_PASS)
def test_password_protected_cert_prompt(httpbin_secure):
    r = http(httpbin_secure + '/get',
             '--cert', PWD_CLIENT_PEM,
             '--cert-key', PWD_CLIENT_KEY)
    assert HTTP_OK in r


@mock.patch('httpie.cli.argtypes.SSLCredentials._prompt_password',
            new=lambda self, prompt: PWD_CLIENT_INVALID_PASS)
def test_password_protected_cert_prompt_invalid(httpbin_secure):
    with pytest.raises(ssl_errors):
        http(httpbin_secure + '/get',
             '--cert', PWD_CLIENT_PEM,
             '--cert-key', PWD_CLIENT_KEY)


def test_password_protected_cert_cli_arg(httpbin_secure):
    r = http(httpbin_secure + '/get',
             '--cert', PWD_CLIENT_PEM,
             '--cert-key', PWD_CLIENT_KEY,
             '--cert-key-pass', PWD_CLIENT_PASS)
    assert HTTP_OK in r


def test_password_protected_cert_cli_arg_invalid(httpbin_secure):
    with pytest.raises(ssl_errors):
        http(httpbin_secure + '/get',
             '--cert', PWD_CLIENT_PEM,
             '--cert-key', PWD_CLIENT_KEY,
             '--cert-key-pass', PWD_CLIENT_INVALID_PASS)
