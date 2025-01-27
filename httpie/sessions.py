"""
Persistent, JSON-serialized sessions.

"""
import os
import re

from http.cookies import SimpleCookie
from http.cookiejar import Cookie
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from requests.auth import AuthBase
from requests.cookies import RequestsCookieJar, remove_cookie_by_name

from .context import Environment, LogLevel
from .cookies import HTTPieCookiePolicy
from .cli.dicts import HTTPHeadersDict
from .config import BaseConfigDict, DEFAULT_CONFIG_DIR
from .utils import url_as_host
from .plugins.registry import plugin_manager

from .legacy import (
    v3_1_0_session_cookie_format as legacy_cookies,
    v3_2_0_session_header_format as legacy_headers
)


SESSIONS_DIR_NAME = 'sessions'
DEFAULT_SESSIONS_DIR = DEFAULT_CONFIG_DIR / SESSIONS_DIR_NAME
VALID_SESSION_NAME_PATTERN = re.compile('^[a-zA-Z0-9_.-]+$')
# Request headers starting with these prefixes won't be stored in sessions.
# They are specific to each request.
# <https://en.wikipedia.org/wiki/List_of_HTTP_header_fields#Requests>
SESSION_IGNORED_HEADER_PREFIXES = ['Content-', 'If-']

# Cookie related options
KEPT_COOKIE_OPTIONS = ['name', 'expires', 'path', 'value', 'domain', 'secure']
DEFAULT_COOKIE_PATH = '/'


def is_anonymous_session(session_name: str) -> bool:
    return os.path.sep in session_name


def session_hostname_to_dirname(hostname: str, session_name: str) -> str:
    # host:port => host_port
    hostname = hostname.replace(':', '_')
    return os.path.join(
        SESSIONS_DIR_NAME,
        hostname,
        f'{session_name}.json'
    )


def strip_port(hostname: str) -> str:
    return hostname.split(':')[0]


def materialize_cookie(cookie: Cookie) -> Dict[str, Any]:
    materialized_cookie = {
        option: getattr(cookie, option)
        for option in KEPT_COOKIE_OPTIONS
    }

    if (
        cookie._rest.get('is_explicit_none')
        and materialized_cookie['domain'] == ''
    ):
        materialized_cookie['domain'] = None

    return materialized_cookie


def materialize_cookies(jar: RequestsCookieJar) -> List[Dict[str, Any]]:
    return [
        materialize_cookie(cookie)
        for cookie in jar
    ]


def materialize_headers(headers: Dict[str, str]) -> List[Dict[str, Any]]:
    return [
        {
            'name': name,
            'value': value
        }
        for name, value in headers.copy().items()
    ]


def get_httpie_session(
    env: Environment,
    config_dir: Path,
    session_name: str,
    host: Optional[str],
    url: str,
    *,
    suppress_legacy_warnings: bool = False
) -> 'Session':
    bound_hostname = host or url_as_host(url)
    if not bound_hostname:
        # HACK/FIXME: httpie-unixsocket's URLs have no hostname.
        bound_hostname = 'localhost'

    if is_anonymous_session(session_name):
        path = os.path.expanduser(session_name)
        session_id = path
    else:
        path = config_dir / session_hostname_to_dirname(bound_hostname, session_name)
        session_id = session_name

    session = Session(
        path,
        env=env,
        session_id=session_id,
        bound_host=strip_port(bound_hostname),
        suppress_legacy_warnings=suppress_legacy_warnings
    )
    session.load()
    return session


class Session(BaseConfigDict):
    helpurl = 'https://httpie.io/docs#sessions'
    about = 'HTTPie session file'

    def __init__(
        self,
        path: Union[str, Path],
        env: Environment,
        bound_host: str,
        session_id: str,
        suppress_legacy_warnings: bool = False,
    ):
        super().__init__(path=Path(path))

        # Default values for the session files
        self['headers'] = []
        self['cookies'] = []
        self['auth'] = {
            'type': None,
            'username': None,
            'password': None
        }

        # Runtime state of the Session objects.
        self.env = env
        self._headers = HTTPHeadersDict()
        self.cookie_jar = RequestsCookieJar(
            # See also a temporary workaround for a Requests bug in `compat.py`.
            policy=HTTPieCookiePolicy(),
        )
        self.session_id = session_id
        self.bound_host = bound_host
        self.suppress_legacy_warnings = suppress_legacy_warnings

    def _add_cookies(self, cookies: List[Dict[str, Any]]) -> None:
        for cookie in cookies:
            domain = cookie.get('domain', '')
            if domain is None:
                # domain = None means explicitly lack of cookie, though
                # requests requires domain to be a string so we'll cast it
                # manually.
                cookie['domain'] = ''
                cookie['rest'] = {'is_explicit_none': True}

            self.cookie_jar.set(**cookie)

    def pre_process_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        for key, deserializer, importer in [
            ('cookies', legacy_cookies.pre_process, self._add_cookies),
            ('headers', legacy_headers.pre_process, self._headers.update),
        ]:
            values = data.get(key)
            if values:
                normalized_values = deserializer(self, values)
            else:
                normalized_values = []

            importer(normalized_values)

        return data

    def post_process_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        for key, store, serializer, exporter in [
            ('cookies', self.cookie_jar, materialize_cookies, legacy_cookies.post_process),
            ('headers', self._headers, materialize_headers, legacy_headers.post_process),
        ]:
            original_type = type(data.get(key))
            values = serializer(store)

            data[key] = exporter(
                values,
                original_type=original_type
            )

        return data

    def _compute_new_headers(self, request_headers: HTTPHeadersDict) -> HTTPHeadersDict:
        new_headers = HTTPHeadersDict()
        for name, value in request_headers.copy().items():
            if value is None:
                continue  # Ignore explicitly unset headers

            original_value = value
            if not isinstance(value, str):
                value = value.decode()

            if name.lower() == 'user-agent' and value.startswith('HTTPie/'):
                continue

            if name.lower() == 'cookie':
                for cookie_name, morsel in SimpleCookie(value).items():
                    if not morsel['path']:
                        morsel['path'] = DEFAULT_COOKIE_PATH
                    self.cookie_jar.set(cookie_name, morsel)

                request_headers.remove_item(name, original_value)
                continue

            for prefix in SESSION_IGNORED_HEADER_PREFIXES:
                if name.lower().startswith(prefix.lower()):
                    break
            else:
                new_headers.add(name, value)

        return new_headers

    def update_headers(self, request_headers: HTTPHeadersDict):
        """
        Update the session headers with the request ones while ignoring
        certain name prefixes.

        """

        new_headers = self._compute_new_headers(request_headers)
        new_keys = new_headers.copy().keys()

        # New headers will take priority over the existing ones, and override
        # them directly instead of extending them.
        for key, value in self._headers.copy().items():
            if key in new_keys:
                continue

            new_headers.add(key, value)

        self._headers = new_headers

    @property
    def headers(self) -> HTTPHeadersDict:
        return self._headers.copy()

    @property
    def cookies(self) -> RequestsCookieJar:
        self.cookie_jar.clear_expired_cookies()
        return self.cookie_jar

    @cookies.setter
    def cookies(self, jar: RequestsCookieJar):
        self.cookie_jar = jar

    def remove_cookies(self, cookies: List[Dict[str, str]]):
        for cookie in cookies:
            remove_cookie_by_name(
                self.cookie_jar,
                cookie['name'],
                domain=cookie.get('domain', None),
                path=cookie.get('path', None)
            )

    @property
    def auth(self) -> Optional[AuthBase]:
        auth = self.get('auth', None)
        if not auth or not auth['type']:
            return

        plugin = plugin_manager.get_auth_plugin(auth['type'])()

        credentials = {'username': None, 'password': None}
        try:
            # New style
            plugin.raw_auth = auth['raw_auth']
        except KeyError:
            # Old style
            credentials = {
                'username': auth['username'],
                'password': auth['password'],
            }
        else:
            if plugin.auth_parse:
                from .cli.argtypes import parse_auth
                parsed = parse_auth(plugin.raw_auth)
                credentials = {
                    'username': parsed.key,
                    'password': parsed.value,
                }

        return plugin.get_auth(**credentials)

    @auth.setter
    def auth(self, auth: dict):
        assert {'type', 'raw_auth'} == auth.keys()
        self['auth'] = auth

    @property
    def is_anonymous(self):
        return is_anonymous_session(self.session_id)

    def warn_legacy_usage(self, warning: str) -> None:
        if self.suppress_legacy_warnings:
            return None

        self.env.log_error(
            warning,
            level=LogLevel.WARNING
        )

        # We don't want to spam multiple warnings on each usage,
        # so if there is already a warning for the legacy usage
        # we'll skip the next ones.
        self.suppress_legacy_warnings = True
