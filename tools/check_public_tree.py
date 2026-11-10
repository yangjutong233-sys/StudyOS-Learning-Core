"""Audit staged Git blobs, optionally every reachable commit, before publishing."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


PRIVATE_DIRS = {'.local', 'local-data', 'database', 'records', 'reports', 'backups',
    'snapshots', 'config', 'materials', 'courseware', 'subjects', '课件', 'tmp', '.venv'}
ALLOWED_DIRS = {'src', 'tests', 'tools', 'docs', 'schemas', 'examples', 'skills', 'migrations', '.github'}
ALLOWED_ROOT = {'README.md', 'AGENTS.md', 'LICENSE', 'CONTRIBUTING.md', 'pyproject.toml',
                '.gitignore', '.gitattributes', 'dashboard.bat'}
TEXT_SUFFIXES = {'.py', '.md', '.json', '.sql', '.toml', '.yml', '.yaml', '.bat'}
PATTERNS = [
    ('personal home path', re.compile(r'[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/][A-Za-z0-9_.-]+', re.I)),
    ('personal home path', re.compile(r'/(?:home|Users)/[A-Za-z0-9_.-]+/')),
    ('GitHub credential', re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})')),
    ('API credential', re.compile(r'sk-(?:proj-)?[A-Za-z0-9_-]{24,}')),
    ('private key', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
    ('AWS access key', re.compile(r'(?:AKIA|ASIA)[A-Z0-9]{16}')),
]


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True).stdout


def audit(root, all_history=False):
    root = Path(root).resolve()
    issues = []
    entries = []
    for entry in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not entry:
            continue
        metadata, name = entry.split(b'\t', 1)
        mode, oid, stage = metadata.decode('ascii').split()
        path = name.decode('utf-8')
        if stage != '0':
            issues.append({'path': path, 'reason': 'unresolved index conflict'})
        entries.append(('index', mode, oid, path))
    commits = []
    if all_history:
        commits = git(root, 'rev-list', '--all').decode('ascii').splitlines()
        for commit in commits:
            for entry in git(root, 'ls-tree', '-r', '-z', commit).split(b'\0'):
                if not entry:
                    continue
                metadata, name = entry.split(b'\t', 1)
                mode, kind, oid = metadata.decode('ascii').split()
                entries.append((commit, mode, oid, name.decode('utf-8')))
    seen = set()
    for origin, mode, oid, path in entries:
        if (mode, oid, path) in seen:
            continue
        seen.add((mode, oid, path))
        p = PurePosixPath(path)
        reasons = []
        if any(part.lower() in PRIVATE_DIRS for part in p.parts[:-1]):
            reasons.append('local learner-data directory')
        if len(p.parts) == 1 and path not in ALLOWED_ROOT:
            reasons.append('unexpected root file')
        if len(p.parts) > 1 and p.parts[0] not in ALLOWED_DIRS:
            reasons.append('directory outside public source allowlist')
        if p.name in ('CODEX_HANDOFF.md', 'GPT_TEACHING_HANDOFF.md'):
            reasons.append('generated personal handoff')
        if p.suffix.lower() not in TEXT_SUFFIXES and path not in ALLOWED_ROOT:
            reasons.append('non-source or binary file type')
        if mode not in ('100644', '100755'):
            reasons.append('symlink or non-regular file')
        else:
            blob = git(root, 'cat-file', 'blob', oid)
            if len(blob) > 2_000_000 or b'\0' in blob:
                reasons.append('binary or oversized content')
            else:
                try:
                    text = blob.decode('utf-8-sig')
                except UnicodeDecodeError:
                    reasons.append('non-UTF-8 content')
                else:
                    for label, pattern in PATTERNS:
                        if pattern.search(text):
                            reasons.append(label)
        for reason in reasons:
            issues.append({'origin': origin, 'path': path, 'reason': reason})
    return {'ok': not issues, 'index_files': len({p for source, _, _, p in entries if source == 'index'}),
            'history_commits': len(commits), 'checked_blobs': len(seen), 'issues': issues}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--all-history', action='store_true')
    args = parser.parse_args()
    try:
        result = audit(args.root, args.all_history)
    except (OSError, subprocess.CalledProcessError) as e:
        result = {'ok': False, 'error': type(e).__name__ + ': unable to inspect Git publication candidates'}
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
