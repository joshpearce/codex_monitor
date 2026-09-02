# Goal: tiny JSON formatting library and CLI

Build a Python package named `tiny_json_formatter` using only the Python standard library.

Requirements:

- Provide a library function that accepts JSON text and returns consistently formatted JSON.
- Support configurable indentation and optional key sorting; always end successful output with one newline.
- Reject duplicate object keys instead of silently keeping the last value.
- Provide a `python -m tiny_json_formatter` CLI that reads one file or standard input and writes one file
  or standard output.
- When writing a file, use a temporary sibling followed by `os.replace` so failure cannot corrupt an
  existing destination.
- Return exit code 2 with a concise stderr message for invalid JSON or invalid arguments.
- Add focused `unittest` tests for formatting, duplicate keys, stdin/stdout, invalid JSON, and atomic
  replacement. Tests must use only temporary files and the standard library.
- Include minimal packaging metadata and a concise README. Do not declare or install dependencies.
- Run `python3 -m unittest discover -s tests -v` with the system Python.
- Complete the mandatory Claude Opus review described by `AGENTS.md`, fix supported findings, and reach a
  clean review verdict.
- Do not initialize Git or commit. The disposable harness only needs the files and review result.
