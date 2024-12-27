from __future__ import annotations

import os
import textwrap
from argparse import FileType

from httpie import __doc__, __version__
from httpie.cli.argtypes import (KeyValueArgType, SessionNameValidator,
                                 SSLCredentials, readable_file_arg,
                                 response_charset_type, response_mime_type)
from httpie.cli.constants import (BASE_OUTPUT_OPTIONS, DEFAULT_FORMAT_OPTIONS,
                                  OUT_REQ_BODY, OUT_REQ_HEAD, OUT_RESP_BODY,
                                  OUT_RESP_HEAD, OUT_RESP_META, OUTPUT_OPTIONS,
                                  OUTPUT_OPTIONS_DEFAULT, PRETTY_MAP,
                                  PRETTY_STDOUT_TTY_ONLY,
                                  FILTER_STDOUT_TTY_ONLY,
                                  SEPARATOR_GROUP_ALL_ITEMS, SEPARATOR_PROXY,
                                  SORTED_FORMAT_OPTIONS_STRING,
                                  UNSORTED_FORMAT_OPTIONS_STRING, RequestType)
from httpie.cli.options import ParserSpec, Qualifiers, to_argparse
from httpie.output.formatters.colors import (AUTO_STYLE, DEFAULT_STYLE, BUNDLED_STYLES,
                                             get_available_styles)
from httpie.plugins.builtin import BuiltinAuthPlugin
from httpie.plugins.registry import plugin_manager
from httpie.ssl_ import AVAILABLE_SSL_VERSION_ARG_MAPPING, DEFAULT_SSL_CIPHERS_STRING


# Man pages are static (built when making a release).
# We use this check to not include generated, system-specific information there (e.g., default --ciphers).
IS_MAN_PAGE = bool(os.environ.get('HTTPIE_BUILDING_MAN_PAGES'))


options = ParserSpec(
    'http',
    description=f'{__doc__.strip()} <https://httpie.io>',
    epilog="""
    For every --OPTION there is also a --no-OPTION that reverts OPTION
    to its default value.

    Suggestions and bug reports are greatly appreciated:
        https://github.com/httpie/cli/issues
    """,
    source_file=__file__
)

#######################################################################
# Positional arguments.
#######################################################################

positional_arguments = options.add_group(
    'Positional arguments',
    description="""
    These arguments come after any flags and in the order they are listed here.
    Only URL is required.
    """,
)

positional_arguments.add_argument(
    dest='method',
    metavar='METHOD',
    nargs=Qualifiers.OPTIONAL,
    default=None,
    short_help='The HTTP method to be used for the request (GET, POST, PUT, DELETE, ...).',
    help="""
    The HTTP method to be used for the request (GET, POST, PUT, DELETE, ...).

    This argument can be omitted in which case HTTPie will use POST if there
    is some data to be sent, otherwise GET:

        $ http example.org               # => GET
        $ http example.org hello=world   # => POST

    """,
)
positional_arguments.add_argument(
    dest='url',
    metavar='URL',
    short_help='The request URL.',
    help="""
    The request URL. Scheme defaults to 'http://' if the URL
    does not include one. (You can override this with: --default-scheme=http/https)

    You can also use a shorthand for localhost

        $ http :3000                    # => http://localhost:3000
        $ http :/foo                    # => http://localhost/foo

    """,
)
positional_arguments.add_argument(
    dest='request_items',
    metavar='REQUEST_ITEM',
    nargs=Qualifiers.ZERO_OR_MORE,
    default=None,
    type=KeyValueArgType(*SEPARATOR_GROUP_ALL_ITEMS),
    short_help=(
        'HTTPie’s request items syntax for specifying HTTP headers, JSON/Form'
        'data, files, and URL parameters.'
    ),
    nested_options=[
        ('HTTP Headers', 'Name:Value', 'Arbitrary HTTP header, e.g X-API-Token:123'),
        ('URL Parameters', 'name==value', 'Querystring parameter to the URL, e.g limit==50'),
        ('Data Fields', 'field=value', 'Data fields to be serialized as JSON (default) or Form Data (with --form)'),
        ('Raw JSON Fields', 'field:=json', 'Data field for real JSON types.'),
        ('File upload Fields', 'field@/dir/file', 'Path field for uploading a file.'),
    ],
    help=r"""
    Optional key-value pairs to be included in the request. The separator used
    determines the type:

    ':' HTTP headers:

        Referer:https://httpie.io  Cookie:foo=bar  User-Agent:bacon/1.0

    '==' URL parameters to be appended to the request URI:

        search==httpie

    '=' Data fields to be serialized into a JSON object (with --json, -j)
        or form data (with --form, -f):

        name=HTTPie  language=Python  description='CLI HTTP client'

    ':=' Non-string JSON data fields (only with --json, -j):

        awesome:=true  amount:=42  colors:='["red", "green", "blue"]'

    '@' Form file fields (only with --form or --multipart):

        cv@~/Documents/CV.pdf
        cv@'~/Documents/CV.pdf;type=application/pdf'

    '=@' A data field like '=', but takes a file path and embeds its content:

        essay=@Documents/essay.txt

    ':=@' A raw JSON field like ':=', but takes a file path and embeds its content:

        package:=@./package.json

    You can use a backslash to escape a colliding separator in the field name:

        field-name-with\:colon=value

    """,
)

#######################################################################
# Content type.
#######################################################################

content_types = options.add_group('Predefined content types')

content_types.add_argument(
    '--json',
    '-j',
    action='store_const',
    const=RequestType.JSON,
    dest='request_type',
    short_help='(default) Serialize data items from the command line as a JSON object.',
    help="""
    (default) Data items from the command line are serialized as a JSON object.
    The Content-Type and Accept headers are set to application/json
    (if not specified).

    """,
)
content_types.add_argument(
    '--form',
    '-f',
    action='store_const',
    const=RequestType.FORM,
    dest='request_type',
    short_help='Serialize data items from the command line as form field data.',
    help="""
    Data items from the command line are serialized as form fields.

    The Content-Type is set to application/x-www-form-urlencoded (if not
    specified). The presence of any file fields results in a
    multipart/form-data request.

    """,
)
content_types.add_argument(
    '--multipart',
    action='store_const',
    const=RequestType.MULTIPART,
    dest='request_type',
    short_help=(
        'Similar to --form, but always sends a multipart/form-data '
        'request (i.e., even without files).'
    )
)
content_types.add_argument(
    '--boundary',
    short_help=(
        'Specify a custom boundary string for multipart/form-data requests. '
        'Only has effect only together with --form.'
    )
)
content_types.add_argument(
    '--raw',
    short_help='Pass raw request data without extra processing.',
    help="""
    This option allows you to pass raw request data without extra processing
    (as opposed to the structured request items syntax):

        $ http --raw='data' pie.dev/post

    You can achieve the same by piping the data via stdin:

        $ echo data | http pie.dev/post

    Or have HTTPie load the raw data from a file:

        $ http pie.dev/post @data.txt


    """,
)

#######################################################################
# Content processing.
#######################################################################

processing_options = options.add_group('Content processing options')

processing_options.add_argument(
    '--compress',
    '-x',
    action='count',
    default=0,
    short_help='Compress the content with Deflate algorithm.',
    help="""
    Content compressed (encoded) with Deflate algorithm.
    The Content-Encoding header is set to deflate.

    Compression is skipped if it appears that compression ratio is
    negative. Compression can be forced by repeating the argument.

    """,
)


#######################################################################
# Output processing
#######################################################################


def format_style_help(available_styles, *, isolation_mode: bool = False):
    text = """
    Output coloring style (default is "{default}"). It can be one of:

        {available_styles}
    """
    if isolation_mode:
        text += '\n\n'
        text += 'For finding out all available styles in your system, try:\n\n'
        text += '    $ http --style\n'
    text += textwrap.dedent("""
        The "{auto_style}" style follows your terminal's ANSI color styles.
        For non-{auto_style} styles to work properly, please make sure that the
        $TERM environment variable is set to "xterm-256color" or similar
        (e.g., via `export TERM=xterm-256color' in your ~/.bashrc).
    """)

    if isolation_mode:
        available_styles = sorted(BUNDLED_STYLES)

    available_styles_text = '\n'.join(
        f'    {line.strip()}'
        for line in textwrap.wrap(', '.join(available_styles), 60)
    ).strip()
    return text.format(
        default=DEFAULT_STYLE,
        available_styles=available_styles_text,
        auto_style=AUTO_STYLE,
    )


_sorted_kwargs = {
    'action': 'append_const',
    'const': SORTED_FORMAT_OPTIONS_STRING,
    'dest': 'format_options',
}
_unsorted_kwargs = {
    'action': 'append_const',
    'const': UNSORTED_FORMAT_OPTIONS_STRING,
    'dest': 'format_options',
}

output_processing = options.add_group('Output processing')

output_processing.add_argument(
    '--pretty',
    dest='prettify',
    default=PRETTY_STDOUT_TTY_ONLY,
    choices=sorted(PRETTY_MAP.keys()),
    short_help='Control the processing of console outputs.',
    help="""
    Controls output processing. The value can be "none" to not prettify
    the output (default for redirected output), "all" to apply both colors
    and formatting (default for terminal output), "colors", or "format".

    """,
)

output_processing.add_argument(
    '--filter-keys',
    dest='filtery',
    default=FILTER_STDOUT_TTY_ONLY,
    # choices=sorted(PRETTY_MAP.keys()),
    short_help='Control the processing of console outputs.',
    help="""
    Controls output processing. Filters the comma separated
    key values in the output json

    """,
)

output_processing.add_argument(
    '--style',
    '-s',
    dest='style',
    metavar='STYLE',
    default=DEFAULT_STYLE,
    action='lazy_choices',
    getter=get_available_styles,
    short_help=f'Output coloring style (default is "{DEFAULT_STYLE}").',
    help_formatter=format_style_help,
)

# The closest approx. of the documented resetting to default via --no-<option>.
# We hide them from the doc because they act only as low-level aliases here.
output_processing.add_argument(
    '--no-unsorted', **_sorted_kwargs, help=Qualifiers.SUPPRESS
)
output_processing.add_argument(
    '--no-sorted', **_unsorted_kwargs, help=Qualifiers.SUPPRESS
)

output_processing.add_argument(
    '--unsorted',
    **_unsorted_kwargs,
    short_help='Disables all sorting while formatting output.',
    help=f"""
    Disables all sorting while formatting output. It is a shortcut for:

        --format-options={UNSORTED_FORMAT_OPTIONS_STRING}

    """,
)
output_processing.add_argument(
    '--sorted',
    **_sorted_kwargs,
    short_help='Re-enables all sorting options while formatting output.',
    help=f"""
    Re-enables all sorting options while formatting output. It is a shortcut for:

        --format-options={SORTED_FORMAT_OPTIONS_STRING}

    """,
)
output_processing.add_argument(
    '--response-charset',
    metavar='ENCODING',
    type=response_charset_type,
    short_help='Override the response encoding for terminal display purposes.',
    help="""
    Override the response encoding for terminal display purposes, e.g.:

        --response-charset=utf8
        --response-charset=big5

    """,
)
output_processing.add_argument(
    '--response-mime',
    metavar='MIME_TYPE',
    type=response_mime_type,
    short_help='Override the response mime type for coloring and formatting for the terminal.',
    help="""
    Override the response mime type for coloring and formatting for the terminal, e.g.:

        --response-mime=application/json
        --response-mime=text/xml

    """,
)
output_processing.add_argument(
    '--format-options',
    action='append',
    short_help='Controls output formatting.',
    help="""
    Controls output formatting. Only relevant when formatting is enabled
    through (explicit or implied) --pretty=all or --pretty=format.
    The following are the default options:

        {option_list}

    You may use this option multiple times, as well as specify multiple
    comma-separated options at the same time. For example, this modifies the
    settings to disable the sorting of JSON keys, and sets the indent size to 2:

        --format-options json.sort_keys:false,json.indent:2

    This is something you will typically put into your config file.

    """.format(
        option_list='\n'.join(
            f'        {option}' for option in DEFAULT_FORMAT_OPTIONS
        ).strip()
    ),
)

#######################################################################
# Output options
#######################################################################

output_options = options.add_group('Output options')

output_options.add_argument(
    '--print',
    '-p',
    dest='output_options',
    metavar='WHAT',
    short_help='Options to specify what the console output should contain.',
    help=f"""
    String specifying what the output should contain:

        '{OUT_REQ_HEAD}' request headers
        '{OUT_REQ_BODY}' request body
        '{OUT_RESP_HEAD}' response headers
        '{OUT_RESP_BODY}' response body
        '{OUT_RESP_META}' response metadata

    The default behaviour is '{OUTPUT_OPTIONS_DEFAULT}' (i.e., the response
    headers and body is printed), if standard output is not redirected.
    If the output is piped to another program or to a file, then only the
    response body is printed by default.

    """,
)
output_options.add_argument(
    '--headers',
    '-h',
    dest='output_options',
    action='store_const',
    const=OUT_RESP_HEAD,
    short_help='Print only the response headers.',
    help=f"""
    Print only the response headers. Shortcut for --print={OUT_RESP_HEAD}.

    """,
)
output_options.add_argument(
    '--meta',
    '-m',
    dest='output_options',
    action='store_const',
    const=OUT_RESP_META,
    short_help='Print only the response metadata.',
    help=f"""
    Print only the response metadata. Shortcut for --print={OUT_RESP_META}.

    """,
)
output_options.add_argument(
    '--body',
    '-b',
    dest='output_options',
    action='store_const',
    const=OUT_RESP_BODY,
    short_help='Print only the response body.',
    help=f"""
    Print only the response body. Shortcut for --print={OUT_RESP_BODY}.

    """,
)

output_options.add_argument(
    '--verbose',
    '-v',
    dest='verbose',
    action='count',
    default=0,
    short_help='Make output more verbose.',
    help=f"""
    Verbose output. For the level one (with single `-v`/`--verbose`), print
    the whole request as well as the response. Also print any intermediary
    requests/responses (such as redirects). For the second level and higher,
    print these as well as the response metadata.

    Level one is a shortcut for: --all --print={''.join(sorted(BASE_OUTPUT_OPTIONS))}
    Level two is a shortcut for: --all --print={''.join(sorted(OUTPUT_OPTIONS))}
    """,
)
output_options.add_argument(
    '--all',
    default=False,
    action='store_true',
    short_help='Show any intermediary requests/responses.',
    help="""
    By default, only the final request/response is shown. Use this flag to show
    any intermediary requests/responses as well. Intermediary requests include
    followed redirects (with --follow), the first unauthorized request when
    Digest auth is used (--auth=digest), etc.

    """,
)
output_options.add_argument(
    '--history-print',
    '-P',
    dest='output_options_history',
    metavar='WHAT',
    help=Qualifiers.SUPPRESS,
)
output_options.add_argument(
    '--stream',
    '-S',
    action='store_true',
    default=False,
    short_help='Always stream the response body by line, i.e., behave like `tail -f`.',
    help="""
    Always stream the response body by line, i.e., behave like `tail -f'.

    Without --stream and with --pretty (either set or implied),
    HTTPie fetches the whole response before it outputs the processed data.

    Set this option when you want to continuously display a prettified
    long-lived response, such as one from the Twitter streaming API.

    It is useful also without --pretty: It ensures that the output is flushed
    more often and in smaller chunks.

    """,
)
output_options.add_argument(
    '--output',
    '-o',
    type=FileType('a+b'),
    dest='output_file',
    metavar='FILE',
    short_help='Save output to FILE instead of stdout.',
    help="""
    Save output to FILE instead of stdout. If --download is also set, then only
    the response body is saved to FILE. Other parts of the HTTP exchange are
    printed to stderr.

    """,
)

output_options.add_argument(
    '--download',
    '-d',
    action='store_true',
    default=False,
    short_help='Download the body to a file instead of printing it to stdout.',
    help="""
    Do not print the response body to stdout. Rather, download it and store it
    in a file. The filename is guessed unless specified with --output
    [filename]. This action is similar to the default behaviour of wget.

    """,
)
output_options.add_argument(
    '--continue',
    '-c',
    dest='download_resume',
    action='store_true',
    default=False,
    short_help='Resume an interrupted download (--output needs to be specified).',
    help="""
    Resume an interrupted download. Note that the --output option needs to be
    specified as well.

    """,
)
output_options.add_argument(
    '--quiet',
    '-q',
    action='count',
    default=0,
    short_help='Do not print to stdout or stderr, except for errors and warnings when provided once.',
    help="""
    Do not print to stdout or stderr, except for errors and warnings when provided once.
    Provide twice to suppress warnings as well.
    stdout is still redirected if --output is specified.
    Flag doesn't affect behaviour of download beyond not printing to terminal.

    """,
)

#######################################################################
# Sessions
#######################################################################

session_name_validator = SessionNameValidator(
    'Session name contains invalid characters.'
)

sessions = options.add_group('Sessions', is_mutually_exclusive=True)

sessions.add_argument(
    '--session',
    metavar='SESSION_NAME_OR_PATH',
    type=session_name_validator,
    short_help='Create, or reuse and update a session.',
    help="""
    Create, or reuse and update a session. Within a session, custom headers,
    auth credential, as well as any cookies sent by the server persist between
    requests.

    Session files are stored in:

        [HTTPIE_CONFIG_DIR]/<HOST>/<SESSION_NAME>.json.

    See the following page to find out your default HTTPIE_CONFIG_DIR:

        https://httpie.io/docs/cli/config-file-directory
    """,
)
sessions.add_argument(
    '--session-read-only',
    metavar='SESSION_NAME_OR_PATH',
    type=session_name_validator,
    short_help='Create or read a session without updating it',
    help="""
    Create or read a session without updating it form the request/response
    exchange.

    """,
)


#######################################################################
# Authentication
#######################################################################


def format_auth_help(auth_plugins_mapping, *, isolation_mode: bool = False):
    text = """
    The authentication mechanism to be used. Defaults to "{default}".

    {auth_types}
    """

    auth_plugins = list(auth_plugins_mapping.values())
    if isolation_mode:
        auth_plugins = [
            auth_plugin
            for auth_plugin in auth_plugins
            if issubclass(auth_plugin, BuiltinAuthPlugin)
        ]
        text += '\n'
        text += 'To see all available auth types on your system, including ones installed via plugins, run:\n\n'
        text += '    $ http --auth-type'

    auth_types = '\n\n    '.join(
        '"{type}": {name}{package}{description}'.format(
            type=plugin.auth_type,
            name=plugin.name,
            package=(
                ''
                if issubclass(plugin, BuiltinAuthPlugin)
                else f' (provided by {plugin.package_name})'
            ),
            description=(
                ''
                if not plugin.description
                else '\n      '
                     + ('\n      '.join(textwrap.wrap(plugin.description)))
            ),
        )
        for plugin in auth_plugins
    )

    return text.format(
        default=auth_plugins[0].auth_type,
        auth_types=auth_types,
    )


authentication = options.add_group('Authentication')

authentication.add_argument(
    '--auth',
    '-a',
    default=None,
    metavar='USER[:PASS] | TOKEN',
    short_help='Credentials for the selected (-A) authentication method.',
    help="""
    For username/password based authentication mechanisms (e.g
    basic auth or digest auth) if only the username is provided
    (-a username), HTTPie will prompt for the password.

    """,
)
authentication.add_argument(
    '--auth-type',
    '-A',
    action='lazy_choices',
    default=None,
    getter=plugin_manager.get_auth_plugin_mapping,
    sort=True,
    cache=False,
    short_help='The authentication mechanism to be used.',
    help_formatter=format_auth_help,
)
authentication.add_argument(
    '--ignore-netrc',
    default=False,
    action='store_true',
    short_help='Ignore credentials from .netrc.'
)

#######################################################################
# Network
#######################################################################

network = options.add_group('Network')

network.add_argument(
    '--offline',
    default=False,
    action='store_true',
    short_help='Build the request and print it but don’t actually send it.'
)
network.add_argument(
    '--proxy',
    default=[],
    action='append',
    metavar='PROTOCOL:PROXY_URL',
    type=KeyValueArgType(SEPARATOR_PROXY),
    short_help='String mapping of protocol to the URL of the proxy.',
    help="""
    String mapping protocol to the URL of the proxy
    (e.g. http:http://foo.bar:3128). You can specify multiple proxies with
    different protocols. The environment variables $ALL_PROXY, $HTTP_PROXY,
    and $HTTPS_proxy are supported as well.

    """,
)
network.add_argument(
    '--follow',
    '-F',
    default=False,
    action='store_true',
    short_help='Follow 30x Location redirects.'
)

network.add_argument(
    '--max-redirects',
    type=int,
    default=30,
    short_help='The maximum number of redirects that should be followed (with --follow).',
    help="""
    By default, requests have a limit of 30 redirects (works with --follow).

    """,
)
network.add_argument(
    '--max-headers',
    type=int,
    default=0,
    short_help=(
        'The maximum number of response headers to be read before '
        'giving up (default 0, i.e., no limit).'
    )
)

network.add_argument(
    '--timeout',
    type=float,
    default=0,
    metavar='SECONDS',
    short_help='The connection timeout of the request in seconds.',
    help="""
    The connection timeout of the request in seconds.
    The default value is 0, i.e., there is no timeout limit.
    This is not a time limit on the entire response download;
    rather, an error is reported if the server has not issued a response for
    timeout seconds (more precisely, if no bytes have been received on
    the underlying socket for timeout seconds).

    """,
)
network.add_argument(
    '--check-status',
    default=False,
    action='store_true',
    short_help='Exit with an error status code if the server replies with an error.',
    help="""
    By default, HTTPie exits with 0 when no network or other fatal errors
    occur. This flag instructs HTTPie to also check the HTTP status code and
    exit with an error if the status indicates one.

    When the server replies with a 4xx (Client Error) or 5xx (Server Error)
    status code, HTTPie exits with 4 or 5 respectively. If the response is a
    3xx (Redirect) and --follow hasn't been set, then the exit status is 3.
    Also an error message is written to stderr if stdout is redirected.

    """,
)
network.add_argument(
    '--path-as-is',
    default=False,
    action='store_true',
    short_help='Bypass dot segment (/../ or /./) URL squashing.'
)
network.add_argument(
    '--chunked',
    default=False,
    action='store_true',
    short_help=(
        'Enable streaming via chunked transfer encoding. '
        'The Transfer-Encoding header is set to chunked.'
    )
)

#######################################################################
# SSL
#######################################################################

ssl = options.add_group('SSL')

ssl.add_argument(
    '--verify',
    default='yes',
    short_help='If "no", skip SSL verification. If a file path, use it as a CA bundle.',
    help="""
    Set to "no" (or "false") to skip checking the host's SSL certificate.
    Defaults to "yes" ("true"). You can also pass the path to a CA_BUNDLE file
    for private certs. (Or you can set the REQUESTS_CA_BUNDLE environment
    variable instead.)
    """,
)
ssl.add_argument(
    '--ssl',
    dest='ssl_version',
    choices=sorted(AVAILABLE_SSL_VERSION_ARG_MAPPING.keys()),
    short_help='The desired protocol version to used.',
    help="""
    The desired protocol version to use. This will default to
    SSL v2.3 which will negotiate the highest protocol that both
    the server and your installation of OpenSSL support. Available protocols
    may vary depending on OpenSSL installation (only the supported ones
    are shown here).

    """,
)

CIPHERS_CURRENT_DEFAULTS = (
    """
    See `http --help` for the default ciphers list on you system.

    """
    if IS_MAN_PAGE else
    f"""
    By default, the following ciphers are used on your system:

    {DEFAULT_SSL_CIPHERS_STRING}

    """
)
ssl.add_argument(
    '--ciphers',
    short_help='A string in the OpenSSL cipher list format.',
    help=f"""

    A string in the OpenSSL cipher list format.

    {CIPHERS_CURRENT_DEFAULTS}

    """
)
ssl.add_argument(
    '--cert',
    default=None,
    type=readable_file_arg,
    short_help='Specifies a local cert to use as the client-side SSL certificate.',
    help="""
    You can specify a local cert to use as client side SSL certificate.
    This file may either contain both private key and certificate or you may
    specify --cert-key separately.

    """,
)
ssl.add_argument(
    '--cert-key',
    default=None,
    type=readable_file_arg,
    short_help='The private key to use with SSL. Only needed if --cert is given.',
    help="""
    The private key to use with SSL. Only needed if --cert is given and the
    certificate file does not contain the private key.

    """,
)

ssl.add_argument(
    '--cert-key-pass',
    default=None,
    type=SSLCredentials,
    short_help='The passphrase to be used to with the given private key.',
    help="""
    The passphrase to be used to with the given private key. Only needed if --cert-key
    is given and the key file requires a passphrase.
    If not provided, you’ll be prompted interactively.
    """
)

#######################################################################
# Troubleshooting
#######################################################################

troubleshooting = options.add_group('Troubleshooting')
troubleshooting.add_argument(
    '--ignore-stdin',
    '-I',
    action='store_true',
    default=False,
    short_help='Do not attempt to read stdin'
)
troubleshooting.add_argument(
    '--help',
    action='help',
    default=Qualifiers.SUPPRESS,
    short_help='Show this help message and exit.',
)
troubleshooting.add_argument(
    '--manual',
    action='manual',
    default=Qualifiers.SUPPRESS,
    short_help='Show the full manual.',
)
troubleshooting.add_argument(
    '--version',
    action='version',
    version=__version__,
    short_help='Show version and exit.',
)
troubleshooting.add_argument(
    '--traceback',
    action='store_true',
    default=False,
    short_help='Prints the exception traceback should one occur.',
)
troubleshooting.add_argument(
    '--default-scheme',
    default='http',
    short_help='The default scheme to use if not specified in the URL.'
)
troubleshooting.add_argument(
    '--debug',
    action='store_true',
    default=False,
    short_help='Print useful diagnostic information for bug reports.',
    help="""
    Prints the exception traceback should one occur, as well as other
    information useful for debugging HTTPie itself and for reporting bugs.

    """,
)

#######################################################################
# Finalization
#######################################################################

options.finalize()
parser = to_argparse(options)
