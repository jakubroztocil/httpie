"""Test data"""
import json
from pathlib import Path
from typing import Optional, Dict, Any

import httpie
from httpie.encoding import UTF8
from httpie.output.formatters.xml import pretty_xml, parse_xml


def patharg(path):
    """
    Back slashes need to be escaped in ITEM args,
    even in Windows paths.

    """
    return str(path).replace('\\', '\\\\\\')


FIXTURES_ROOT = Path(__file__).parent
FILE_PATH = FIXTURES_ROOT / 'test.txt'
JSON_FILE_PATH = FIXTURES_ROOT / 'test.json'
JSON_WITH_DUPE_KEYS_FILE_PATH = FIXTURES_ROOT / 'test_with_dupe_keys.json'
BIN_FILE_PATH = FIXTURES_ROOT / 'test.bin'
TINY_FILE_PATH = FIXTURES_ROOT / 'test_tiny.txt'

XML_FILES_PATH = FIXTURES_ROOT / 'xmldata'
XML_FILES_VALID = list((XML_FILES_PATH / 'valid').glob('*_raw.xml'))
XML_FILES_INVALID = list((XML_FILES_PATH / 'invalid').glob('*.xml'))

SESSION_FILES_PATH = FIXTURES_ROOT / 'session_data'
SESSION_FILES_OLD = sorted((SESSION_FILES_PATH / 'old').glob('*.json'))
SESSION_FILES_NEW = sorted((SESSION_FILES_PATH / 'new').glob('*.json'))

SESSION_VARIABLES = {
    '__version__': httpie.__version__,
    '__host__': 'null',
}

FILE_PATH_ARG = patharg(FILE_PATH)
BIN_FILE_PATH_ARG = patharg(BIN_FILE_PATH)
JSON_FILE_PATH_ARG = patharg(JSON_FILE_PATH)
TINY_FILE_PATH_ARG = patharg(TINY_FILE_PATH)

# Strip because we don't want new lines in the data so that we can
# easily count occurrences also when embedded in JSON (where the new
# line would be escaped).
FILE_CONTENT = FILE_PATH.read_text(encoding=UTF8).strip()

ASCII_FILE_CONTENT = "random text" * 10


JSON_FILE_CONTENT = JSON_FILE_PATH.read_text(encoding=UTF8)
BIN_FILE_CONTENT = BIN_FILE_PATH.read_bytes()
UNICODE = FILE_CONTENT
XML_DATA_RAW = '<?xml version="1.0" encoding="utf-8"?><root><e>text</e></root>'
XML_DATA_FORMATTED = pretty_xml(parse_xml(XML_DATA_RAW))


def read_session_file(session_file: Path, *, extra_variables: Optional[Dict[str, str]] = None) -> Any:
    with open(session_file) as stream:
        data = stream.read()

    session_vars = {**SESSION_VARIABLES, **(extra_variables or {})}
    for variable, value in session_vars.items():
        data = data.replace(variable, value)

    return json.loads(data)
