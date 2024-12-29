import argparse
import http.client
import json
import sys
from contextlib import contextmanager
from time import monotonic
from typing import Any, Dict, Callable, Iterable
from urllib.parse import urlparse, urlunparse

import requests
# noinspection PyPackageRequirements
import urllib3
from urllib3.util import SKIP_HEADER, SKIPPABLE_HEADERS

from . import __version__
from .adapters import HTTPieHTTPAdapter
from .cli.constants import HTTP_OPTIONS
from .cli.dicts import HTTPHeadersDict
from .cli.nested_json import unwrap_top_level_list_if_needed
from .context import Environment
from .encoding import UTF8
from .models import RequestsMessage
from .plugins.registry import plugin_manager
from .sessions import get_httpie_session
from .ssl_ import AVAILABLE_SSL_VERSION_ARG_MAPPING, HTTPieCertificate, HTTPieHTTPSAdapter
from .uploads import (
    compress_request, prepare_request_body,
    get_multipart_data_and_content_type,
)
from .utils import get_expired_cookies, repr_dict


urllib3.disable_warnings()

FORM_CONTENT_TYPE = f'application/x-www-form-urlencoded; charset={UTF8}'
JSON_CONTENT_TYPE = 'application/json'
JSON_ACCEPT = f'{JSON_CONTENT_TYPE}, */*;q=0.5'
DEFAULT_UA = f'HTTPie/{__version__}'

IGNORE_CONTENT_LENGTH_METHODS = frozenset([HTTP_OPTIONS])


def collect_messages(
    env: Environment,
    args: argparse.Namespace,
    request_body_read_callback: Callable[[bytes], None] = None,
) -> Iterable[RequestsMessage]:
    httpie_session = None
    httpie_session_headers = None
    if args.session or args.session_read_only:
        httpie_session = get_httpie_session(
            env=env,
            config_dir=env.config.directory,
            session_name=args.session or args.session_read_only,
            host=args.headers.get('Host'),
            url=args.url,
        )
        httpie_session_headers = httpie_session.headers

    request_kwargs = make_request_kwargs(
        env,
        args=args,
        base_headers=httpie_session_headers,
        request_body_read_callback=request_body_read_callback
    )
    send_kwargs = make_send_kwargs(args)
    send_kwargs_mergeable_from_env = make_send_kwargs_mergeable_from_env(args)
    requests_session = build_requests_session(
        ssl_version=args.ssl_version,
        ciphers=args.ciphers,
        verify=bool(send_kwargs_mergeable_from_env['verify'])
    )

    if httpie_session:
        httpie_session.update_headers(request_kwargs['headers'])
        requests_session.cookies = httpie_session.cookies
        if args.auth_plugin:
            # Save auth from CLI to HTTPie session.
            httpie_session.auth = {
                'type': args.auth_plugin.auth_type,
                'raw_auth': args.auth_plugin.raw_auth,
            }
        elif httpie_session.auth:
            # Apply auth from HTTPie session
            request_kwargs['auth'] = httpie_session.auth

    if args.debug:
        # TODO: reflect the split between request and send kwargs.
        dump_request(request_kwargs)

    request = requests.Request(**request_kwargs)
    prepared_request = requests_session.prepare_request(request)
    transform_headers(request, prepared_request)
    if args.path_as_is:
        prepared_request.url = ensure_path_as_is(
            orig_url=args.url,
            prepped_url=prepared_request.url,
        )
    if args.compress and prepared_request.body:
        compress_request(
            request=prepared_request,
            always=args.compress > 1,
        )
    response_count = 0
    expired_cookies = []
    while prepared_request:
        yield prepared_request
        if not args.offline:
            send_kwargs_merged = requests_session.merge_environment_settings(
                url=prepared_request.url,
                **send_kwargs_mergeable_from_env,
            )
            with max_headers(args.max_headers):
                response = requests_session.send(
                    request=prepared_request,
                    **send_kwargs_merged,
                    **send_kwargs,
                )
            response._httpie_headers_parsed_at = monotonic()
            expired_cookies += get_expired_cookies(
                response.headers.get('Set-Cookie', '')
            )

            response_count += 1
            if response.next:
                if args.max_redirects and response_count == args.max_redirects:
                    raise requests.TooManyRedirects
                if args.follow:
                    prepared_request = response.next
                    if args.all:
                        yield response
                    continue
            yield response
        break

    if httpie_session:
        if httpie_session.is_new() or not args.session_read_only:
            httpie_session.cookies = requests_session.cookies
            httpie_session.remove_cookies(expired_cookies)
            httpie_session.save()


# noinspection PyProtectedMember
@contextmanager
def max_headers(limit):
    # <https://github.com/httpie/cli/issues/802>
    # noinspection PyUnresolvedReferences
    orig = http.client._MAXHEADERS
    http.client._MAXHEADERS = limit or float('Inf')
    try:
        yield
    finally:
        http.client._MAXHEADERS = orig


def build_requests_session(
    verify: bool,
    ssl_version: str = None,
    ciphers: str = None,
) -> requests.Session:
    requests_session = requests.Session()

    # Install our adapter.
    http_adapter = HTTPieHTTPAdapter()
    https_adapter = HTTPieHTTPSAdapter(
        ciphers=ciphers,
        verify=verify,
        ssl_version=(
            AVAILABLE_SSL_VERSION_ARG_MAPPING[ssl_version]
            if ssl_version else None
        ),
    )
    requests_session.mount('http://', http_adapter)
    requests_session.mount('https://', https_adapter)

    # Install adapters from plugins.
    for plugin_cls in plugin_manager.get_transport_plugins():
        transport_plugin = plugin_cls()
        requests_session.mount(
            prefix=transport_plugin.prefix,
            adapter=transport_plugin.get_adapter(),
        )

    return requests_session


def dump_request(kwargs: dict):
    sys.stderr.write(
        f'\n>>> requests.request(**{repr_dict(kwargs)})\n\n')


def finalize_headers(headers: HTTPHeadersDict) -> HTTPHeadersDict:
    final_headers = HTTPHeadersDict()
    for name, value in headers.items():
        if value is not None:
            # “leading or trailing LWS MAY be removed without
            # changing the semantics of the field value”
            # <https://www.w3.org/Protocols/rfc2616/rfc2616-sec4.html>
            # Also, requests raises `InvalidHeader` for leading spaces.
            value = value.strip()
            if isinstance(value, str):
                # See <https://github.com/httpie/cli/issues/212>
                value = value.encode()
        elif name.lower() in SKIPPABLE_HEADERS:
            # Some headers get overwritten by urllib3 when set to `None`
            # and should be replaced with the `SKIP_HEADER` constant.
            value = SKIP_HEADER
        final_headers.add(name, value)
    return final_headers


def transform_headers(
    request: requests.Request,
    prepared_request: requests.PreparedRequest
) -> None:
    """Apply various transformations on top of the `prepared_requests`'s
    headers to change the request prepreation behavior."""

    # Remove 'Content-Length' when it is misplaced by requests.
    if (
        prepared_request.method in IGNORE_CONTENT_LENGTH_METHODS
        and prepared_request.headers.get('Content-Length') == '0'
        and request.headers.get('Content-Length') != '0'
    ):
        prepared_request.headers.pop('Content-Length')

    apply_missing_repeated_headers(
        request.headers,
        prepared_request
    )


def apply_missing_repeated_headers(
    original_headers: HTTPHeadersDict,
    prepared_request: requests.PreparedRequest
) -> None:
    """Update the given `prepared_request`'s headers with the original
    ones. This allows the requests to be prepared as usual, and then later
    merged with headers that are specified multiple times."""

    new_headers = HTTPHeadersDict(prepared_request.headers)
    for prepared_name, prepared_value in prepared_request.headers.items():
        if prepared_name not in original_headers:
            continue

        original_keys, original_values = zip(*filter(
            lambda item: item[0].casefold() == prepared_name.casefold(),
            original_headers.items()
        ))

        if prepared_value not in original_values:
            # If the current value is not among the initial values
            # set for this field, then it means that this field got
            # overridden on the way, and we should preserve it.
            continue

        new_headers.popone(prepared_name)
        new_headers.update(zip(original_keys, original_values))

    prepared_request.headers = new_headers


def make_default_headers(args: argparse.Namespace, config_default_headers: dict) -> HTTPHeadersDict:
    default_headers = HTTPHeadersDict({
        'User-Agent': DEFAULT_UA,
        **config_default_headers
    })

    auto_json = args.data and not args.form
    if args.json or auto_json:
        default_headers['Accept'] = JSON_ACCEPT
        if args.json or (auto_json and args.data):
            default_headers['Content-Type'] = JSON_CONTENT_TYPE

    elif args.form and not args.files:
        # If sending files, `requests` will set
        # the `Content-Type` for us.
        default_headers['Content-Type'] = FORM_CONTENT_TYPE

    return default_headers


def make_send_kwargs(args: argparse.Namespace) -> dict:
    return {
        'timeout': args.timeout or None,
        'allow_redirects': False,
    }


def make_send_kwargs_mergeable_from_env(args: argparse.Namespace) -> dict:
    cert = None
    if args.cert:
        cert = args.cert
        if args.cert_key:
            # Having a client certificate key passphrase is not supported
            # by requests. So we are using our own transportation structure
            # which is compatible with their format (a tuple of minimum two
            # items).
            #
            # See: https://github.com/psf/requests/issues/2519
            cert = HTTPieCertificate(cert, args.cert_key, args.cert_key_pass.value)

    return {
        'proxies': {p.key: p.value for p in args.proxy},
        'stream': True,
        'verify': {
            'yes': True,
            'true': True,
            'no': False,
            'false': False,
        }.get(args.verify.lower(), args.verify),
        'cert': cert,
    }


def json_dict_to_request_body(data: Dict[str, Any]) -> str:
    data = unwrap_top_level_list_if_needed(data)
    if data:
        data = json.dumps(data)
    else:
        # We need to set data to an empty string to prevent requests
        # from assigning an empty list to `response.request.data`.
        data = ''
    return data


def make_request_kwargs(
    env: Environment,
    args: argparse.Namespace,
    base_headers: HTTPHeadersDict = None,
    request_body_read_callback=lambda chunk: chunk
) -> dict:
    """
    Translate our `args` into `requests.Request` keyword arguments.

    """
    files = args.files
    # Serialize JSON data, if needed.
    data = args.data
    auto_json = data and not args.form
    if (args.json or auto_json) and isinstance(data, dict):
        data = json_dict_to_request_body(data)

    # Apply config's default headers
    config_default_headers = {}
    if type(env.config.default_headers) is dict:
        config_default_headers = env.config.default_headers

    # Finalize headers.
    headers = make_default_headers(args, config_default_headers)
    if base_headers:
        headers.update(base_headers)
    headers.update(args.headers)
    if args.offline and args.chunked and 'Transfer-Encoding' not in headers:
        # When online, we let requests set the header instead to be able more
        # easily verify chunking is taking place.
        headers['Transfer-Encoding'] = 'chunked'
    headers = finalize_headers(headers)

    if (args.form and files) or args.multipart:
        data, headers['Content-Type'] = get_multipart_data_and_content_type(
            data=args.multipart_data,
            boundary=args.boundary,
            content_type=args.headers.get('Content-Type'),
        )

    return {
        'method': args.method.lower(),
        'url': args.url,
        'headers': headers,
        'data': prepare_request_body(
            env,
            data,
            body_read_callback=request_body_read_callback,
            chunked=args.chunked,
            offline=args.offline,
            content_length_header_value=headers.get('Content-Length'),
        ),
        'auth': args.auth,
        'params': args.params.items(),
    }


def ensure_path_as_is(orig_url: str, prepped_url: str) -> str:
    """
    Handle `--path-as-is` by replacing the path component of the prepared
    URL with the path component from the original URL. Other parts stay
    untouched because other (welcome) processing on the URL might have
    taken place.

    <https://github.com/httpie/cli/issues/895>


    <https://ec.haxx.se/http/http-basics#path-as-is>
    <https://curl.haxx.se/libcurl/c/CURLOPT_PATH_AS_IS.html>

    >>> ensure_path_as_is('http://foo/../', 'http://foo/?foo=bar')
    'http://foo/../?foo=bar'

    """
    parsed_orig, parsed_prepped = urlparse(orig_url), urlparse(prepped_url)
    final_dict = {
        # noinspection PyProtectedMember
        **parsed_prepped._asdict(),
        'path': parsed_orig.path,
    }
    return urlunparse(tuple(final_dict.values()))
