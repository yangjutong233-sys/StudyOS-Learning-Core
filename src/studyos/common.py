from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import math
import os
import time
import uuid

SHANGHAI = timezone(timedelta(hours=8))
SUBJECTS = ('math2', 'cs408', 'english1', 'politics101')


class Error(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.details = code, details


def require(condition, message, code='VALIDATION_ERROR', details=None):
    if not condition:
        raise Error(code, message, details)


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def uid():
    return str(uuid.uuid4())


def now(value=None):
    try:
        t = datetime.fromisoformat(value) if value else datetime.now(SHANGHAI)
        return (t.replace(tzinfo=SHANGHAI) if t.tzinfo is None else t.astimezone(SHANGHAI)).isoformat()
    except (ValueError, TypeError):
        raise Error('VALIDATION_ERROR', '时间必须为 ISO 日期或日期时间')


def day(value):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise Error('VALIDATION_ERROR', '日期必须为 YYYY-MM-DD')


def text(value, field):
    require(isinstance(value, str) and bool(value.strip()), f'{field} 必须为非空字符串')
    return value


def number(value, field, minimum=0, maximum=None):
    require(type(value) in (int, float) and math.isfinite(value) and value >= minimum and
            (maximum is None or value <= maximum), f'{field} 数值无效')
    return value


def safe_path(root, relative):
    p = (Path(root) / relative).resolve()
    require(p.is_relative_to(Path(root).resolve()), '路径越界', 'INVALID_PATH')
    return p


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uid() + '.tmp')
    try:
        with temp.open('xb') as f:
            f.write(data if isinstance(data, bytes) else data.encode('utf-8'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'),
                          parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (ValueError, OSError) as e:
        raise Error('INVALID_JSON', f'无法读取 JSON: {path}', str(e))


@contextmanager
def operation_lock(root, timeout=5):
    """OS lock; stale lock files are harmless. Never locks the legacy project."""
    p = Path(root) / 'database/.operations.lock'
    p.parent.mkdir(parents=True, exist_ok=True)
    f = p.open('a+b')
    f.seek(0, 2)
    if not f.tell():
        f.write(b'0'); f.flush()
    start = time.monotonic()
    acquired = False
    try:
        while True:
            try:
                f.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() - start >= timeout:
                    raise Error('DATABASE_BUSY', '操作锁等待超时')
                time.sleep(.05)
        yield
    finally:
        if acquired:
            f.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()
