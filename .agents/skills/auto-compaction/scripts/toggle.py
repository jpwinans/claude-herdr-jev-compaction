#!/usr/bin/env python3
"""Toggle the installed Jev hooks' existing dry mode without editing registration."""
import argparse
import ast
import os
from pathlib import Path
import tempfile

MARKER = '    os.environ["TASK_COMPACT_DRY"] = "1"  # jev-auto-compaction: off\n'


def updated(source, action):
    tree = ast.parse(source)
    mains = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main']
    if len(mains) != 1 or 'TASK_COMPACT_DRY' not in source:
        raise ValueError('Not a supported task-compact.py hook')
    lines = source.splitlines(keepends=True)
    main = mains[0]
    # Keep a possible function docstring in its original position.
    first = main.body[0]
    index = first.lineno - 1
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        index = first.end_lineno
    if MARKER in lines:
        if lines.count(MARKER) != 1 or lines[index] != MARKER:
            raise ValueError('Unexpected toggle marker location; refusing to edit')
        if action == 'on':
            del lines[index]
    elif action == 'off':
        lines.insert(index, MARKER)
    result = ''.join(lines)
    compile(result, '<hook>', 'exec')
    return result


def replace(path, content):
    mode = path.stat().st_mode & 0o777
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['on', 'off', 'status'])
    parser.add_argument('--target', choices=['codex'], required=True)
    args = parser.parse_args()
    roots = {
        'claude': Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude'))),
        'codex': Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))),
    }
    pending = []
    for target, root in roots.items():
        if args.target != target:
            continue
        path = (root / 'hooks' / 'task-compact.py').resolve()
        if not path.is_file():
            if args.action == 'status':
                print(f'{target}: hook not installed ({path})')
                continue
            parser.error(f'{target}: hook not installed: {path}')
        source = path.read_text()
        try:
            content = updated(source, args.action)
        except (ValueError, SyntaxError) as error:
            parser.error(f'{target}: {error}')
        pending.append((target, path, source, content))
    for target, path, source, content in pending:
        if content != source:
            if path.read_text() != source:
                raise RuntimeError(f'{path} changed during toggle; rerun')
            replace(path, content)
        state = 'off (dry mode)' if MARKER in content.splitlines(keepends=True) else 'on (no skill override)'
        print(f'{target}: {state}')


if __name__ == '__main__':
    main()
