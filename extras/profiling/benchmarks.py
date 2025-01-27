"""
This file is the declaration of benchmarks for HTTPie. It
is also used to run them with the current environment.

Each instance of BaseRunner class will be an individual
benchmark. And if run without any arguments, this file
will execute every benchmark instance and report the
timings.

The benchmarks are run through 'pyperf', which allows to
do get very precise results. For micro-benchmarks like startup,
please run `pyperf system tune` to get even more accurate results.

Examples:

    # Run everything as usual, the default is that we do 3 warm-up runs
    # and 5 actual runs.
    $ python extras/profiling/benchmarks.py

    # For retrieving results faster, pass --fast
    $ python extras/profiling/benchmarks.py --fast

    # For verify everything works as expected, pass --debug-single-value.
    # It will only run everything once, so the results are not reliable. But
    # very useful when iterating on a benchmark
    $ python extras/profiling/benchmarks.py --debug-single-value

    # If you want to run with a custom HTTPie command (for example with
    # and HTTPie instance installed in another virtual environment),
    # pass HTTPIE_COMMAND variable.
    $ HTTPIE_COMMAND="/my/python /my/httpie" python extras/profiling/benchmarks.py
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from functools import cached_property, partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from tempfile import TemporaryDirectory
from typing import ClassVar, Final, List

import pyperf

# For download benchmarks, define a set of files.
# file: (block_size, count) => total_size = block_size * count
PREDEFINED_FILES: Final = {'3G': (3 * 1024 ** 2, 1024)}


class QuietSimpleHTTPServer(SimpleHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass


@contextmanager
def start_server():
    """Create a server to serve local files. It will create the
    PREDEFINED_FILES through dd."""
    with TemporaryDirectory() as directory:
        for file_name, (block_size, count) in PREDEFINED_FILES.items():
            subprocess.check_call(
                [
                    'dd',
                    'if=/dev/zero',
                    f'of={file_name}',
                    f'bs={block_size}',
                    f'count={count}',
                ],
                cwd=directory,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        handler = partial(QuietSimpleHTTPServer, directory=directory)
        server = HTTPServer(('localhost', 0), handler)

        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        yield '{}:{}'.format(*server.socket.getsockname())
        server.shutdown()
        thread.join(timeout=0.5)


@dataclass
class Context:
    benchmarks: ClassVar[List[BaseRunner]] = []
    stack: ExitStack = field(default_factory=ExitStack)
    runner: pyperf.Runner = field(default_factory=pyperf.Runner)

    def run(self) -> pyperf.BenchmarkSuite:
        results = [benchmark.run(self) for benchmark in self.benchmarks]
        return pyperf.BenchmarkSuite(results)

    @property
    def cmd(self) -> List[str]:
        if cmd := os.getenv('HTTPIE_COMMAND'):
            return shlex.split(cmd)

        http = os.path.join(os.path.dirname(sys.executable), 'http')
        assert os.path.exists(http)
        return [sys.executable, http]

    @cached_property
    def server(self) -> str:
        return self.stack.enter_context(start_server())

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.stack.close()


@dataclass
class BaseRunner:
    """
    An individual benchmark case. By default it has the category
    (e.g like startup or download) and a name.
    """

    category: str
    title: str

    def __post_init__(self):
        Context.benchmarks.append(self)

    def run(self, context: Context) -> pyperf.Benchmark:
        raise NotImplementedError

    @property
    def name(self) -> str:
        return f'{self.title} ({self.category})'


@dataclass
class CommandRunner(BaseRunner):
    """
    Run a single command, and benchmark it.
    """

    args: List[str]

    def run(self, context: Context) -> pyperf.Benchmark:
        return context.runner.bench_command(self.name, [*context.cmd, *self.args])


@dataclass
class DownloadRunner(BaseRunner):
    """
    Benchmark downloading a single file from the
    remote server.
    """

    file_name: str

    def run(self, context: Context) -> pyperf.Benchmark:
        return context.runner.bench_command(
            self.name,
            [
                *context.cmd,
                '--download',
                'GET',
                f'{context.server}/{self.file_name}',
            ],
        )


CommandRunner('startup', '`http --version`', ['--version'])
CommandRunner('startup', '`http --offline pie.dev/get`', ['--offline', 'pie.dev/get'])
for pretty in ['all', 'none']:
    CommandRunner(
        'startup',
        f'`http --pretty={pretty} pie.dev/stream/1000`',
        [
            '--print=HBhb',
            f'--pretty={pretty}',
            'httpbin.org/stream/1000'
        ]
    )
DownloadRunner('download', '`http --download :/big_file.txt` (3GB)', '3G')


def main() -> None:
    # PyPerf will bring it's own argument parser, so configure the script.
    # The somewhat fast and also precise enough configuration is this. We run
    # benchmarks 3 times to warm up (e.g especially for download benchmark, this
    # is important). And then 5 actual runs where we record.
    sys.argv.extend(
        ['--worker', '--loops=1', '--warmup=3', '--values=5', '--processes=2']
    )

    with Context() as context:
        context.run()


if __name__ == '__main__':
    main()
