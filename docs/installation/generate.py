import re
import sys
from pathlib import Path
from typing import Dict

import yaml

from jinja2 import Template


Database = Dict[str, dict]

# Files
HERE = Path(__file__).parent
DB_FILE = HERE / 'methods.yml'
DOC_FILE = HERE.parent / 'README.md'
TPL_FILE = HERE / 'installation.jinja2'

# Database keys
KEY_DOC_STRUCTURE = 'docs-structure'
KEY_TOOLS = 'tools'

# Markers in-between content will be put.
MARKER_START = '<div data-installation-instructions>'
MARKER_END = '</div>'


def generate_documentation() -> str:
    database = load_database()
    structure = build_docs_structure(database)
    template = Template(source=TPL_FILE.read_text())
    output = template.render(structure=structure)
    output = clean_template_output(output)
    return output


def save_doc_file(content: str) -> bool:
    current_doc = load_doc_file()
    marker_start = current_doc.find(MARKER_START) + len(MARKER_START) + 2
    marker_end = current_doc.find(MARKER_END, marker_start)
    updated_doc = (
        current_doc[:marker_start]
        + content
        + current_doc[marker_end:]
    )
    if current_doc != updated_doc:
        DOC_FILE.write_text(updated_doc, encoding='utf-8')
        return True
    return False


def build_docs_structure(database: Database):
    tools = database[KEY_TOOLS]
    tree = database[KEY_DOC_STRUCTURE]
    structure = []
    for platform, tools_ids in tree.items():
        assert platform.isalnum()  # for generated links to work
        platform_tools = [tools[tool_id] for tool_id in tools_ids]
        structure.append((platform, platform_tools))
    return structure


def clean_template_output(output):
    output = '\n'.join(line.lstrip() for line in output.strip().splitlines())
    output = re.sub('\n{3,}', '\n\n', output)
    return output


def load_database() -> Database:
    return yaml.safe_load(DB_FILE.read_text(encoding='utf-8'))


def load_doc_file() -> str:
    return DOC_FILE.read_text(encoding='utf-8')


def main() -> int:
    content = generate_documentation()
    return int(save_doc_file(content))


if __name__ == '__main__':
    sys.exit(main())
