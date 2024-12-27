import argparse
import errno
import os
import re
import sys
from argparse import RawDescriptionHelpFormatter
from textwrap import dedent
from urllib.parse import urlsplit

from requests.utils import get_netrc_auth

from .argtypes import (
    AuthCredentials, SSLCredentials, KeyValueArgType,
    PARSED_DEFAULT_FORMAT_OPTIONS,
    parse_auth,
    parse_format_options,
)
from .constants import (
    HTTP_GET, HTTP_POST, BASE_OUTPUT_OPTIONS, OUTPUT_OPTIONS, OUTPUT_OPTIONS_DEFAULT,
    OUTPUT_OPTIONS_DEFAULT_OFFLINE, OUTPUT_OPTIONS_DEFAULT_STDOUT_REDIRECTED,
    OUT_RESP_BODY, PRETTY_MAP, PRETTY_STDOUT_TTY_ONLY, FILTER_STDOUT_TTY_ONLY, RequestType,
    SEPARATOR_CREDENTIALS,
    SEPARATOR_GROUP_ALL_ITEMS, SEPARATOR_GROUP_DATA_ITEMS, URL_SCHEME_RE,
)
from .exceptions import ParseError
from .requestitems import RequestItems
from ..context import Environment
from ..plugins.registry import plugin_manager
from ..utils import ExplicitNullAuth, get_content_type


class HTTPieHelpFormatter(RawDescriptionHelpFormatter):
    """A nicer help formatter.

    Help for arguments can be indented and contain new lines.
    It will be de-dented and arguments in the help
    will be separated by a blank line for better readability.


    """

    def __init__(self, max_help_position=6, *args, **kwargs):
        # A smaller indent for args help.
        kwargs['max_help_position'] = max_help_position
        super().__init__(*args, **kwargs)

    def _split_lines(self, text, width):
        text = dedent(text).strip() + '\n\n'
        return text.splitlines()

    def add_usage(self, usage, actions, groups, prefix=None):
        # Only display the positional arguments
        displayed_actions = [
            action
            for action in actions
            if not action.option_strings
        ]

        _, exception, _ = sys.exc_info()
        if (
            isinstance(exception, argparse.ArgumentError)
            and len(exception.args) >= 1
            and isinstance(exception.args[0], argparse.Action)
        ):
            # add_usage path is also taken when you pass an invalid option,
            # e.g --style=invalid. If something like that happens, we want
            # to include to action that caused to the invalid usage into
            # the list of actions we are displaying.
            displayed_actions.insert(0, exception.args[0])

        super().add_usage(
            usage,
            displayed_actions,
            groups,
            prefix="usage:\n    "
        )


# TODO: refactor and design type-annotated data structures
#       for raw args + parsed args and keep things immutable.
class BaseHTTPieArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.env = None
        self.args = None
        self.has_stdin_data = False
        self.has_input_data = False

    # noinspection PyMethodOverriding
    def parse_args(
        self,
        env: Environment,
        args=None,
        namespace=None
    ) -> argparse.Namespace:
        self.env = env
        self.args, no_options = self.parse_known_args(args, namespace)
        if self.args.debug:
            self.args.traceback = True
        self.has_stdin_data = (
            self.env.stdin
            and not getattr(self.args, 'ignore_stdin', False)
            and not self.env.stdin_isatty
        )
        self.has_input_data = self.has_stdin_data or getattr(self.args, 'raw', None) is not None
        return self.args

    # noinspection PyShadowingBuiltins
    def _print_message(self, message, file=None):
        # Sneak in our stderr/stdout.
        if hasattr(self, 'root'):
            env = self.root.env
        else:
            env = self.env

        if env is not None:
            file = {
                sys.stdout: env.stdout,
                sys.stderr: env.stderr,
                None: env.stderr
            }.get(file, file)

        if not hasattr(file, 'buffer') and isinstance(message, str):
            message = message.encode(env.stdout_encoding)
        super()._print_message(message, file)


class HTTPieManagerArgumentParser(BaseHTTPieArgumentParser):
    def parse_known_args(self, args=None, namespace=None):
        try:
            return super().parse_known_args(args, namespace)
        except SystemExit as exc:
            if not hasattr(self, 'root') and exc.code == 2:  # Argument Parser Error
                raise argparse.ArgumentError(None, None)
            raise


class HTTPieArgumentParser(BaseHTTPieArgumentParser):
    """Adds additional logic to `argparse.ArgumentParser`.

    Handles all input (CLI args, file args, stdin), applies defaults,
    and performs extra validation.

    """

    def __init__(self, *args, formatter_class=HTTPieHelpFormatter, **kwargs):
        kwargs.setdefault('add_help', False)
        super().__init__(*args, formatter_class=formatter_class, **kwargs)

    # noinspection PyMethodOverriding
    def parse_args(
        self,
        env: Environment,
        args=None,
        namespace=None
    ) -> argparse.Namespace:
        self.env = env
        self.env.args = namespace = namespace or argparse.Namespace()
        self.args, no_options = super().parse_known_args(args, namespace)
        if self.args.debug:
            self.args.traceback = True
        self.has_stdin_data = (
            self.env.stdin
            and not self.args.ignore_stdin
            and not self.env.stdin_isatty
        )
        self.has_input_data = self.has_stdin_data or self.args.raw is not None
        # Arguments processing and environment setup.
        self._apply_no_options(no_options)
        self._process_request_type()
        self._process_download_options()
        self._setup_standard_streams()
        self._process_output_options()
        self._process_pretty_options()
        self._process_filter_options()
        self._process_format_options()
        self._guess_method()
        self._parse_items()
        self._process_url()
        self._process_auth()
        self._process_ssl_cert()

        if self.args.raw is not None:
            self._body_from_input(self.args.raw)
        elif self.has_stdin_data:
            self._body_from_file(self.env.stdin)

        if self.args.compress:
            # TODO: allow --compress with --chunked / --multipart
            if self.args.chunked:
                self.error('cannot combine --compress and --chunked')
            if self.args.multipart:
                self.error('cannot combine --compress and --multipart')

        return self.args

    def _process_request_type(self):
        request_type = self.args.request_type
        self.args.json = request_type is RequestType.JSON
        self.args.multipart = request_type is RequestType.MULTIPART
        self.args.form = request_type in {
            RequestType.FORM,
            RequestType.MULTIPART,
        }

    def _process_url(self):
        if self.args.url.startswith('://'):
            # Paste URL & add space shortcut: `http ://pie.dev` → `http://pie.dev`
            self.args.url = self.args.url[3:]
        if not URL_SCHEME_RE.match(self.args.url):
            if os.path.basename(self.env.program_name) == 'https':
                scheme = 'https://'
            else:
                scheme = self.args.default_scheme + '://'

            # See if we're using curl style shorthand for localhost (:3000/foo)
            shorthand = re.match(r'^:(?!:)(\d*)(/?.*)$', self.args.url)
            if shorthand:
                port = shorthand.group(1)
                rest = shorthand.group(2)
                self.args.url = scheme + 'localhost'
                if port:
                    self.args.url += ':' + port
                self.args.url += rest
            else:
                self.args.url = scheme + self.args.url

    def _setup_standard_streams(self):
        """
        Modify `env.stdout` and `env.stdout_isatty` based on args, if needed.

        """

        self.args.output_file_specified = bool(self.args.output_file)
        if self.args.download:
            # FIXME: Come up with a cleaner solution.
            if not self.args.output_file and not self.env.stdout_isatty:
                # Use stdout as the download output file.
                self.args.output_file = self.env.stdout
            # With `--download`, we write everything that would normally go to
            # `stdout` to `stderr` instead. Let's replace the stream so that
            # we don't have to use many `if`s throughout the codebase.
            # The response body will be treated separately.
            self.env.stdout = self.env.stderr
            self.env.stdout_isatty = self.env.stderr_isatty

        elif self.args.output_file:
            # When not `--download`ing, then `--output` simply replaces
            # `stdout`. The file is opened for appending, which isn't what
            # we want in this case.
            self.args.output_file.seek(0)
            try:
                self.args.output_file.truncate()
            except OSError as e:
                if e.errno == errno.EINVAL:
                    # E.g. /dev/null on Linux.
                    pass
                else:
                    raise
            self.env.stdout = self.args.output_file
            self.env.stdout_isatty = False

        if self.args.quiet:
            self.env.quiet = self.args.quiet
            self.env.stderr = self.env.devnull
            if not (self.args.output_file_specified and not self.args.download):
                self.env.stdout = self.env.devnull
            self.env.apply_warnings_filter()

    def _process_ssl_cert(self):
        from httpie.ssl_ import _is_key_file_encrypted

        if self.args.cert_key_pass is None:
            self.args.cert_key_pass = SSLCredentials(None)

        if (
            self.args.cert_key is not None
            and self.args.cert_key_pass.value is None
            and _is_key_file_encrypted(self.args.cert_key)
        ):
            self.args.cert_key_pass.prompt_password(self.args.cert_key)

    def _process_auth(self):
        # TODO: refactor & simplify this method.
        self.args.auth_plugin = None
        default_auth_plugin = plugin_manager.get_auth_plugins()[0]
        auth_type_set = self.args.auth_type is not None
        url = urlsplit(self.args.url)

        if self.args.auth is None and not auth_type_set:
            if url.username is not None:
                # Handle http://username:password@hostname/
                username = url.username
                password = url.password or ''
                self.args.auth = AuthCredentials(
                    key=username,
                    value=password,
                    sep=SEPARATOR_CREDENTIALS,
                    orig=SEPARATOR_CREDENTIALS.join([username, password])
                )

        if self.args.auth is not None or auth_type_set:
            if not self.args.auth_type:
                self.args.auth_type = default_auth_plugin.auth_type
            plugin = plugin_manager.get_auth_plugin(self.args.auth_type)()

            if (not self.args.ignore_netrc
                    and self.args.auth is None
                    and plugin.netrc_parse):
                # Only host needed, so it’s OK URL not finalized.
                netrc_credentials = get_netrc_auth(self.args.url)
                if netrc_credentials:
                    self.args.auth = AuthCredentials(
                        key=netrc_credentials[0],
                        value=netrc_credentials[1],
                        sep=SEPARATOR_CREDENTIALS,
                        orig=SEPARATOR_CREDENTIALS.join(netrc_credentials)
                    )

            if plugin.auth_require and self.args.auth is None:
                self.error('--auth required')

            plugin.raw_auth = self.args.auth
            self.args.auth_plugin = plugin
            already_parsed = isinstance(self.args.auth, AuthCredentials)

            if self.args.auth is None or not plugin.auth_parse:
                self.args.auth = plugin.get_auth()
            else:
                if already_parsed:
                    # from the URL
                    credentials = self.args.auth
                else:
                    credentials = parse_auth(self.args.auth)

                if (not credentials.has_password()
                        and plugin.prompt_password):
                    if self.args.ignore_stdin:
                        # Non-tty stdin read by now
                        self.error(
                            'Unable to prompt for passwords because'
                            ' --ignore-stdin is set.'
                        )
                    credentials.prompt_password(url.netloc)

                if (credentials.key and credentials.value):
                    plugin.raw_auth = credentials.key + ":" + credentials.value

                self.args.auth = plugin.get_auth(
                    username=credentials.key,
                    password=credentials.value,
                )
        if not self.args.auth and self.args.ignore_netrc:
            # Set a no-op auth to force requests to ignore .netrc
            # <https://github.com/psf/requests/issues/2773#issuecomment-174312831>
            self.args.auth = ExplicitNullAuth()

    def _apply_no_options(self, no_options):
        """For every `--no-OPTION` in `no_options`, set `args.OPTION` to
        its default value. This allows for un-setting of options, e.g.,
        specified in config.

        """
        invalid = []

        for option in no_options:
            if not option.startswith('--no-'):
                invalid.append(option)
                continue

            # --no-option => --option
            inverted = '--' + option[5:]
            for action in self._actions:
                if inverted in action.option_strings:
                    setattr(self.args, action.dest, action.default)
                    break
            else:
                invalid.append(option)

        if invalid:
            self.error(f'unrecognized arguments: {" ".join(invalid)}')

    def _body_from_file(self, fd):
        """Read the data from a file-like object.

        Bytes are always read.

        """
        self._ensure_one_data_source(self.args.data, self.args.files)
        self.args.data = getattr(fd, 'buffer', fd)

    def _body_from_input(self, data):
        """Read the data from the CLI.

        """
        self._ensure_one_data_source(self.has_stdin_data, self.args.data,
                                     self.args.files)
        self.args.data = data.encode()

    def _ensure_one_data_source(self, *other_sources):
        """There can only be one source of input request data.

        """
        if any(other_sources):
            self.error('Request body (from stdin, --raw or a file) and request '
                       'data (key=value) cannot be mixed. Pass '
                       '--ignore-stdin to let key/value take priority. '
                       'See https://httpie.io/docs#scripting for details.')

    def _guess_method(self):
        """Set `args.method` if not specified to either POST or GET
        based on whether the request has data or not.

        """
        if self.args.method is None:
            # Invoked as `http URL'.
            assert not self.args.request_items
            if self.has_input_data:
                self.args.method = HTTP_POST
            else:
                self.args.method = HTTP_GET

        # FIXME: False positive, e.g., "localhost" matches but is a valid URL.
        elif not re.match('^[a-zA-Z]+$', self.args.method):
            # Invoked as `http URL item+'. The URL is now in `args.method`
            # and the first ITEM is now incorrectly in `args.url`.
            try:
                # Parse the URL as an ITEM and store it as the first ITEM arg.
                self.args.request_items.insert(0, KeyValueArgType(
                    *SEPARATOR_GROUP_ALL_ITEMS).__call__(self.args.url))

            except argparse.ArgumentTypeError as e:
                if self.args.traceback:
                    raise
                self.error(e.args[0])

            else:
                # Set the URL correctly
                self.args.url = self.args.method
                # Infer the method
                has_data = (
                    self.has_input_data
                    or any(
                        item.sep in SEPARATOR_GROUP_DATA_ITEMS
                        for item in self.args.request_items)
                )
                self.args.method = HTTP_POST if has_data else HTTP_GET

    def _parse_items(self):
        """
        Parse `args.request_items` into `args.headers`, `args.data`,
        `args.params`, and `args.files`.

        """
        try:
            request_items = RequestItems.from_args(
                request_item_args=self.args.request_items,
                request_type=self.args.request_type,
            )
        except ParseError as e:
            if self.args.traceback:
                raise
            self.error(e.args[0])
        else:
            self.args.headers = request_items.headers
            self.args.data = request_items.data
            self.args.files = request_items.files
            self.args.params = request_items.params
            self.args.multipart_data = request_items.multipart_data

        if self.args.files and not self.args.form:
            # `http url @/path/to/file`
            request_file = None
            for key, file in self.args.files.items():
                if key != '':
                    self.error(
                        'Invalid file fields (perhaps you meant --form?):'
                        f' {",".join(self.args.files.keys())}')
                if request_file is not None:
                    self.error("Can't read request from multiple files")
                request_file = file

            fn, fd, ct = request_file
            self.args.files = {}

            self._body_from_file(fd)

            if 'Content-Type' not in self.args.headers:
                content_type = get_content_type(fn)
                if content_type:
                    self.args.headers['Content-Type'] = content_type

    def _process_output_options(self):
        """Apply defaults to output options, or validate the provided ones.

        The default output options are stdout-type-sensitive.

        """

        def check_options(value, option):
            unknown = set(value) - OUTPUT_OPTIONS
            if unknown:
                self.error(f'Unknown output options: {option}={",".join(unknown)}')

        if self.args.verbose:
            self.args.all = True

        if self.args.output_options is None:
            if self.args.verbose >= 2:
                self.args.output_options = ''.join(OUTPUT_OPTIONS)
            elif self.args.verbose == 1:
                self.args.output_options = ''.join(BASE_OUTPUT_OPTIONS)
            elif self.args.offline:
                self.args.output_options = OUTPUT_OPTIONS_DEFAULT_OFFLINE
            elif not self.env.stdout_isatty:
                self.args.output_options = OUTPUT_OPTIONS_DEFAULT_STDOUT_REDIRECTED
            else:
                self.args.output_options = OUTPUT_OPTIONS_DEFAULT

        if self.args.output_options_history is None:
            self.args.output_options_history = self.args.output_options

        check_options(self.args.output_options, '--print')
        check_options(self.args.output_options_history, '--history-print')

        if self.args.download and OUT_RESP_BODY in self.args.output_options:
            # Response body is always downloaded with --download and it goes
            # through a different routine, so we remove it.
            self.args.output_options = str(
                set(self.args.output_options) - set(OUT_RESP_BODY))

    def _process_pretty_options(self):
        if self.args.prettify == PRETTY_STDOUT_TTY_ONLY:
            self.args.prettify = PRETTY_MAP[
                'all' if self.env.stdout_isatty else 'none']
        elif (self.args.prettify and self.env.is_windows
              and self.args.output_file):
            self.error('Only terminal output can be colorized on Windows.')
        else:
            # noinspection PyTypeChecker
            self.args.prettify = PRETTY_MAP[self.args.prettify]

    def _process_filter_options(self):
        if self.args.filtery == FILTER_STDOUT_TTY_ONLY:
            pass
        elif (self.args.filtery and self.env.is_windows
              and self.args.output_file):
            self.error('Only terminal output can be colorized on Windows.')
        else:
            # noinspection PyTypeChecker
            pass

    def _process_download_options(self):
        if self.args.offline:
            self.args.download = False
            self.args.download_resume = False
            return
        if not self.args.download:
            if self.args.download_resume:
                self.error('--continue only works with --download')
        if self.args.download_resume and not (
                self.args.download and self.args.output_file):
            self.error('--continue requires --output to be specified')

    def _process_format_options(self):
        format_options = self.args.format_options or []
        parsed_options = PARSED_DEFAULT_FORMAT_OPTIONS
        for options_group in format_options:
            parsed_options = parse_format_options(options_group, defaults=parsed_options)
        self.args.format_options = parsed_options

    def print_manual(self):
        from httpie.output.ui import man_pages

        if man_pages.is_available(self.env.program_name):
            man_pages.display_for(self.env, self.env.program_name)
            return None

        text = self.format_help()
        with self.env.rich_console.pager():
            self.env.rich_console.print(
                text,
                highlight=False
            )

    def print_usage(self, file):
        from rich.text import Text
        from httpie.output.ui import rich_help

        whitelist = set()
        _, exception, _ = sys.exc_info()
        if (
            isinstance(exception, argparse.ArgumentError)
            and len(exception.args) >= 1
            and isinstance(exception.args[0], argparse.Action)
            and exception.args[0].option_strings
        ):
            # add_usage path is also taken when you pass an invalid option,
            # e.g --style=invalid. If something like that happens, we want
            # to include to action that caused to the invalid usage into
            # the list of actions we are displaying.
            whitelist.add(exception.args[0].option_strings[0])

        usage_text = Text('usage', style='bold')
        usage_text.append(':\n    ')
        usage_text.append(rich_help.to_usage(self.spec, whitelist=whitelist))
        self.env.rich_error_console.print(usage_text)

    def error(self, message):
        """Prints a usage message incorporating the message to stderr and
        exits."""
        self.print_usage(sys.stderr)
        self.env.rich_error_console.print(
            dedent(
                f'''
                [bold]error[/bold]:
                    {message}

                [bold]for more information[/bold]:
                    run '{self.prog} --help' or visit https://httpie.io/docs/cli
                '''.rstrip()
            )
        )
        self.exit(2)
