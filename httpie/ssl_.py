import ssl
from typing import NamedTuple, Optional, Tuple, MutableMapping
import json
import os.path
from os import makedirs

from httpie.config import DEFAULT_CONFIG_DIR
from httpie.adapters import HTTPAdapter
# noinspection PyPackageRequirements
from urllib3.util.ssl_ import (
    create_urllib3_context,
    resolve_ssl_version,
)


SSL_VERSION_ARG_MAPPING = {
    'tls1.2': 'PROTOCOL_TLSv1_2',
    'tls1.3': 'PROTOCOL_TLSv1_3',
}
AVAILABLE_SSL_VERSION_ARG_MAPPING = {
    arg: getattr(ssl, constant_name)
    for arg, constant_name in SSL_VERSION_ARG_MAPPING.items()
    if hasattr(ssl, constant_name)
}


class QuicCapabilityCache(
    MutableMapping[Tuple[str, int], Optional[Tuple[str, int]]]
):

    def __init__(self):
        self._cache = {}
        if not os.path.exists(DEFAULT_CONFIG_DIR):
            makedirs(DEFAULT_CONFIG_DIR, exist_ok=True)
        if os.path.exists(os.path.join(DEFAULT_CONFIG_DIR, "quic.json")):
            with open(os.path.join(DEFAULT_CONFIG_DIR, "quic.json"), "r") as fp:
                self._cache = json.load(fp)

    def save(self):
        with open(os.path.join(DEFAULT_CONFIG_DIR, "quic.json"), "w+") as fp:
            json.dump(self._cache, fp)

    def __contains__(self, item: Tuple[str, int]):
        return f"QUIC_{item[0]}_{item[1]}" in self._cache

    def __setitem__(self, key: Tuple[str, int], value: Optional[Tuple[str, int]]):
        self._cache[f"QUIC_{key[0]}_{key[1]}"] = f"{value[0]}:{value[1]}"
        self.save()

    def __getitem__(self, item: Tuple[str, int]):
        key: str = f"QUIC_{item[0]}_{item[1]}"
        if key in self._cache:
            host, port = self._cache[key].split(":")
            return host, int(port)

        return None

    def __delitem__(self, key: Tuple[str, int]):
        key: str = f"QUIC_{key[0]}_{key[1]}"
        if key in self._cache:
            del self._cache[key]
            self.save()

    def __len__(self):
        return len(self._cache)

    def __iter__(self):
        yield from self._cache.items()


class HTTPieCertificate(NamedTuple):
    cert_file: Optional[str] = None
    key_file: Optional[str] = None
    key_password: Optional[str] = None

    def to_raw_cert(self):
        """Synthesize a requests-compatible (2-item tuple of cert and key file)
        object from HTTPie's internal representation of a certificate."""
        if self.key_password:
            return self.cert_file, self.key_file, self.key_password
        return self.cert_file, self.key_file


class HTTPieHTTPSAdapter(HTTPAdapter):
    def __init__(
        self,
        verify: bool,
        ssl_version: str = None,
        ciphers: str = None,
        **kwargs
    ):
        self._ssl_context = None
        self._verify = None

        if ssl_version or ciphers:
            # Only set the custom context if user supplied one.
            # Because urllib3-future set his own secure ctx with a set of
            # ciphers (moz recommended list). thus avoiding excluding QUIC
            # in case some ciphers are accidentally excluded.
            self._ssl_context = self._create_ssl_context(
                verify=verify,
                ssl_version=ssl_version,
                ciphers=ciphers,
            )
        else:
            self._verify = verify

        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs['ssl_context'] = self._ssl_context
        if self._verify is not None:
            kwargs['cert_reqs'] = ssl.CERT_REQUIRED if self._verify else ssl.CERT_NONE
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs['ssl_context'] = self._ssl_context
        if self._verify is not None:
            kwargs['cert_reqs'] = ssl.CERT_REQUIRED if self._verify else ssl.CERT_NONE
        return super().proxy_manager_for(*args, **kwargs)

    def cert_verify(self, conn, url, verify, cert):
        if isinstance(cert, HTTPieCertificate):
            cert = cert.to_raw_cert()

        return super().cert_verify(conn, url, verify, cert)

    @staticmethod
    def _create_ssl_context(
        verify: bool,
        ssl_version: str = None,
        ciphers: str = None,
    ) -> 'ssl.SSLContext':
        return create_urllib3_context(
            ciphers=ciphers,
            ssl_version=resolve_ssl_version(ssl_version),
            # Since we are using a custom SSL context, we need to pass this
            # here manually, even though it’s also passed to the connection
            # in `super().cert_verify()`.
            cert_reqs=ssl.CERT_REQUIRED if verify else ssl.CERT_NONE
        )

    @classmethod
    def get_default_ciphers_names(cls):
        return [cipher['name'] for cipher in cls._create_ssl_context(verify=False).get_ciphers()]


def _is_key_file_encrypted(key_file):
    """Detects if a key file is encrypted or not.

    Copy of the internal urllib function (urllib3.util.ssl_)"""

    with open(key_file, "r") as f:
        for line in f:
            # Look for Proc-Type: 4,ENCRYPTED
            if "ENCRYPTED" in line:
                return True

    return False


# We used to import the default set of TLS ciphers from urllib3, but they removed it.
# Instead, now urllib3 uses the list of ciphers configured by the system.
# <https://github.com/httpie/cli/pull/1501>
DEFAULT_SSL_CIPHERS_STRING = ':'.join(HTTPieHTTPSAdapter.get_default_ciphers_names())
