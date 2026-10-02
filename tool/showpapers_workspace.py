"""Durable local readable-collection workspace. POSIX, owner-selected paths, no service.

Only immutable snapshots contain authored content. Metadata receipts store hashes/identities.
A workspace is a cooperative editing boundary, not authentication or an encrypted backup.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

import showpapers_api as api
import showpapers_format as fmt

MAX_REVISIONS = 32
MAX_PROPOSALS = 64
MAX_WORKSPACE_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 128 * 1024
_HEX = re.compile(r'[a-f0-9]{64}\Z')


def _boundary(_name):
    """Named fault-injection seam used only by synthetic subprocess tests."""


def _need(value, code='workspace'):
    if not value:
        raise fmt.FormatError(code, 'The workspace operation could not be completed safely.')


def _safe(function):
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except fmt.FormatError:
            raise
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            raise fmt.FormatError('workspace', 'The workspace operation could not be completed safely.') from None
    return call


def _identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        _need(stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= limit, 'limit')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            data = stream.read(limit + 1)
        _need(len(data) == before.st_size and _identity(before) == _identity(os.fstat(fd)), 'source')
        _need(_identity(os.stat(path, follow_symlinks=False)) == _identity(before), 'source')
        return data, {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'identity': _identity(before)}
    finally:
        os.close(fd)


def _fingerprint(value):
    fmt.keys(value, {'sha256', 'size', 'identity'})
    _need(type(value['sha256']) is str and _HEX.fullmatch(value['sha256']))
    _need(type(value['size']) is int and 0 <= value['size'] <= fmt.MAX_ARCHIVE_BYTES)
    _need(type(value['identity']) is list and len(value['identity']) == 5 and
          all(type(n) is int and n >= 0 for n in value['identity']))


def _digest(value):
    return hashlib.sha256(fmt.json_bytes(value)).hexdigest()


class _Workspace:
    def __init__(self, path, check=lambda: None):
        self.path = Path(path).absolute()
        self.check = check
        self.rootdir = self.directory = self.lock = None

    def __enter__(self):
        _need(str(self.path) == os.path.realpath(self.path), 'source')
        self.rootdir = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self.original = os.fstat(self.rootdir)
            self.directory = os.open("data", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.rootdir)
            self.data_identity = os.fstat(self.directory)
            self.lock = os.open('.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.directory)
            _need(stat.S_ISREG(os.fstat(self.lock).st_mode))
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise fmt.FormatError('busy', 'Another workspace operation is in progress.') from None
            self.require()
            self.budget()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None
        if self.directory is not None:
            os.close(self.directory)
            self.directory = None
        if self.rootdir is not None:
            os.close(self.rootdir)
            self.rootdir = None

    def file(self, name):
        _need('/' not in name and name not in ('', '.', '..'))
        return Path(f'/proc/self/fd/{self.rootdir}/data') / name

    def require(self):
        self.check()
        live = os.stat(self.path, follow_symlinks=False)
        _need(stat.S_ISDIR(live.st_mode) and (live.st_dev, live.st_ino) ==
              (self.original.st_dev, self.original.st_ino), 'source')
        data = os.stat('data', dir_fd=self.rootdir, follow_symlinks=False)
        _need(stat.S_ISDIR(data.st_mode) and (data.st_dev, data.st_ino) ==
              (self.data_identity.st_dev, self.data_identity.st_ino), 'source')
        lock = os.stat('.lock', dir_fd=self.directory, follow_symlinks=False)
        _need((lock.st_dev, lock.st_ino) == (os.fstat(self.lock).st_dev, os.fstat(self.lock).st_ino), 'source')

    def budget(self):
        # Includes abandoned private writer stages, so repeated crashes cannot bypass bounds.
        total = count = 0
        def visit(path, depth):
            nonlocal total, count
            _need(depth <= 2, 'limit')
            with os.scandir(path) as entries:
                for entry in entries:
                    info = entry.stat(follow_symlinks=False)
                    count += 1
                    _need(count <= 512, 'limit')
                    if stat.S_ISDIR(info.st_mode):
                        _need(entry.name.startswith('.showpapers-'), 'workspace')
                        visit(entry.path, depth + 1)
                    else:
                        _need(stat.S_ISREG(info.st_mode), 'source')
                        total += info.st_size
                        _need(total <= MAX_WORKSPACE_BYTES, 'limit')
        visit(self.file('.lock').parent, 0)
        return total

    def exists(self, name):
        try:
            os.stat(name, dir_fd=self.directory, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def json(self, name):
        raw, _ = _read(self.file(name), MAX_METADATA_BYTES)
        value = fmt.parse_json(raw)
        _need(type(value) is dict)
        return value

    def publish(self, name, value, final_check=lambda: None):
        raw = fmt.json_bytes(value)
        _need(len(raw) <= MAX_METADATA_BYTES, 'limit')
        self.require()
        _need(not self.exists(name), 'exists')
        temporary = '.metadata-' + uuid.uuid4().hex + '.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.directory)
        identity = os.fstat(fd)
        try:
            with os.fdopen(os.dup(fd), 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            self.require()
            _boundary('before-' + name)
            self.require(); final_check()
            _need(_identity(os.stat(temporary, dir_fd=self.directory, follow_symlinks=False)) == _identity(os.fstat(fd)), 'source')
            _need(_read(self.file(temporary), MAX_METADATA_BYTES)[0] == raw, 'source')
            os.link(temporary, name, src_dir_fd=self.directory, dst_dir_fd=self.directory, follow_symlinks=False)
            os.fsync(self.directory)
            _boundary('after-' + name)
        finally:
            os.close(fd)
            try:
                current = os.stat(temporary, dir_fd=self.directory, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    os.unlink(temporary, dir_fd=self.directory)
            except FileNotFoundError:
                pass

    def publish_snapshot(self, source_name, target_name, fingerprint, final_check=lambda: None):
        self.require()
        _, actual = _read(self.file(source_name), fmt.MAX_ARCHIVE_BYTES)
        _need(actual['sha256'] == fingerprint['sha256'] and actual['size'] == fingerprint['size'], 'source')
        if self.exists(target_name):
            _, target = _read(self.file(target_name), fmt.MAX_ARCHIVE_BYTES)
            _need(target['sha256'] == fingerprint['sha256'] and target['size'] == fingerprint['size'], 'source')
            return
        fd = os.open(source_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.directory)
        try:
            os.fsync(fd)
            self.require()
            _boundary('before-' + target_name)
            self.require(); final_check()
            _need(_read(self.file(source_name), fmt.MAX_ARCHIVE_BYTES)[1] == actual, 'source')
            _need(_identity(os.fstat(fd)) == actual['identity'], 'source')
            os.link(source_name, target_name, src_dir_fd=self.directory, dst_dir_fd=self.directory, follow_symlinks=False)
            os.fsync(self.directory)
            _boundary('after-' + target_name)
        finally:
            os.close(fd)


def _snapshot(w, name, expected=None):
    raw, fingerprint = _read(w.file(name), fmt.MAX_ARCHIVE_BYTES)
    summary = api.validate(w.file(name))
    # Validation and digest must refer to the same unchanged snapshot.
    _, after = _read(w.file(name), fmt.MAX_ARCHIVE_BYTES)
    _need(fingerprint == after, 'source')
    value = {k: summary[k] for k in ('collectionId', 'revision', 'formatVersion')}
    value.update(snapshot=name, sha256=fingerprint['sha256'], size=len(raw))
    if expected is not None:
        _need(value == expected, 'source')
    return value


def _snapshot_metadata(value):
    fmt.keys(value, {'collectionId', 'revision', 'formatVersion', 'snapshot', 'sha256', 'size'})
    fmt.identifier(value['collectionId']); fmt.integer(value['revision'], 1, fmt.MAX_REVISION)
    _need(type(value['formatVersion']) is int and value['formatVersion'] in (1, 2, 3))
    _need(type(value['snapshot']) is str and re.fullmatch(r'v[0-9]{4}\.showpapers', value['snapshot']))
    _need(type(value['sha256']) is str and _HEX.fullmatch(value['sha256']))
    fmt.integer(value['size'], 22, fmt.MAX_ARCHIVE_BYTES)


def _load(w):
    init = w.json('init.json')
    _initial(init)
    if not w.exists('initial.json'):
        return init, None, [], None
    initial = w.json('initial.json')
    fmt.keys(initial, {'workspaceId', 'current'})
    _need(initial['workspaceId'] == init['workspaceId'])
    _snapshot_metadata(initial['current'])
    _need(initial['current']['snapshot'] == 'v0000.showpapers' and initial['current']['sha256'] == init['source']['sha256'])
    current = _snapshot(w, 'v0000.showpapers', initial['current'])
    receipts = []
    operations = set()
    pending = None
    # A contiguous receipt chain is authoritative; no mutable or inferred "latest file" pointer.
    for index in range(1, MAX_REVISIONS + 1):
        intent_name = f'i{index:04}.json'
        if not w.exists(intent_name):
            break
        intent = w.json(intent_name)
        _intent(intent)
        _need(intent['proposal']['operationId'] not in operations, 'replay')
        operations.add(intent['proposal']['operationId'])
        if 'resolution' in intent['proposal']:
            # A request merged through an explicit resolution is acknowledged work too.
            _need(intent['proposal']['resolution']['requestOperationId'] not in operations, 'replay')
            operations.add(intent['proposal']['resolution']['requestOperationId'])
        _need(intent['workspaceId'] == init['workspaceId'] and intent['index'] == index and intent['before'] == current)
        proposal = w.json('p-' + intent['proposal']['operationId'] + '.json')
        _need(proposal == intent['proposal'])
        if not w.exists(f'c{index:04}.json'):
            if w.exists(f'r{index:04}.json'):
                _ready(w.json(f'r{index:04}.json'), intent)
            pending = intent
            break
        complete = w.json(f'c{index:04}.json')
        ready = w.json(f'r{index:04}.json')
        _ready(ready, intent)
        _need(complete == ready)
        current = _snapshot(w, ready['after']['snapshot'], ready['after'])
        receipts.append(ready)
    consumed = len(receipts) + (1 if pending else 0)
    for name in os.listdir(w.file('.lock').parent):
        match = re.fullmatch(r'[irc]([0-9]{4})\.json', name)
        if match:
            _need(1 <= int(match[1]) <= consumed, 'workspace')
    return init, current, receipts, pending


def _proposal(value):
    _need(type(value) is dict)
    fmt.keys(value, {'workspaceId', 'operationId', 'before', 'inputs', 'planSha256'} | ({'resolution'} & set(value)))
    if 'resolution' in value:
        # The head ("before") is the other side; the request is replanned against this older base.
        resolution = value['resolution']
        fmt.keys(resolution, {'file', 'base', 'requestOperationId'})
        _fingerprint(resolution['file']); _snapshot_metadata(resolution['base']); fmt.identifier(resolution['requestOperationId'])
        _need(resolution['requestOperationId'] != value['operationId'] and
              resolution['base']['revision'] < value['before']['revision'] and
              resolution['base']['collectionId'] == value['before']['collectionId'] and
              resolution['base']['formatVersion'] == value['before']['formatVersion'])
    fmt.identifier(value['workspaceId']); fmt.identifier(value['operationId']); _snapshot_metadata(value['before'])
    _need(type(value['planSha256']) is str and _HEX.fullmatch(value['planSha256']))
    inputs = value['inputs']
    fmt.keys(inputs, {'request', 'grant', 'sources', 'fieldPolicy', 'addedSources', 'sourceRoot'})
    _fingerprint(inputs['request']); _fingerprint(inputs['grant'])
    for key in ('sources', 'fieldPolicy'):
        if inputs[key] is not None:
            _fingerprint(inputs[key])
    _need(inputs['sourceRoot'] is None or (type(inputs['sourceRoot']) is list and len(inputs['sourceRoot']) == 2 and all(type(n) is int and n >= 0 for n in inputs['sourceRoot'])))
    _need((inputs['sourceRoot'] is None) == (inputs['sources'] is None))
    _need(type(inputs['addedSources']) is dict and len(inputs['addedSources']) <= fmt.MAX_OPERATIONS)
    for key, item in inputs['addedSources'].items():
        fmt.identifier(key); _fingerprint(item)


def _intent(value):
    fmt.keys(value, {'workspaceId', 'index', 'before', 'proposal'})
    fmt.identifier(value['workspaceId']); fmt.integer(value['index'], 1, MAX_REVISIONS)
    _snapshot_metadata(value['before']); _proposal(value['proposal'])
    _need(value['proposal']['workspaceId'] == value['workspaceId'] and value['proposal']['before'] == value['before'])


def _ready(value, intent):
    fmt.keys(value, {'intentSha256', 'after'})
    _need(value['intentSha256'] == _digest(intent))
    _snapshot_metadata(value['after'])
    _need(value['after']['snapshot'] == f"v{intent['index']:04}.showpapers" and
          value['after']['revision'] == intent['before']['revision'] + 1 and
          value['after']['collectionId'] == intent['before']['collectionId'] and
          value['after']['formatVersion'] == intent['before']['formatVersion'])


def _inputs(request_path, grant_path, sources_path, field_policy_path):
    raw, request = _read(request_path, fmt.MAX_MANIFEST_BYTES)
    body = fmt.parse_json(raw)
    _need(type(body) is dict, 'operation')
    _need(type(body.get('operations')) is list and 1 <= len(body['operations']) <= fmt.MAX_OPERATIONS, 'operation')
    _need(all(type(operation) is dict for operation in body['operations']), 'operation')
    fmt.identifier(body.get('operationId'))
    grant_raw, grant = _read(grant_path, fmt.MAX_MANIFEST_BYTES)
    policy = _read(field_policy_path, fmt.MAX_MANIFEST_BYTES)[1] if field_policy_path is not None else None
    sources = _read(sources_path, fmt.MAX_MANIFEST_BYTES)[1] if sources_path is not None else None
    added = {}
    source_root = None
    if sources_path is not None:
        scope = fmt.parse_json(grant_raw)
        _need(type(scope) is dict, 'scope')
        reader = fmt._OperationSources(sources_path, body['collectionId'], scope.get('sourceIds', []))
        source_root = list(reader.identity[:2])
        _need(type(body.get('operations')) is list, 'operation')
        for operation in body.get('operations', []):
            _need(type(operation) is dict, 'operation')
            if operation.get('type') == 'paper.add':
                key = operation['sourceId']
                data = reader.read(key)
                identity = reader.used[key]
                added[key] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'identity': list(identity[-5:])}
        reader.check()
    return body['operationId'], {'request': request, 'grant': grant, 'sources': sources,
                                'fieldPolicy': policy, 'addedSources': added, 'sourceRoot': source_root}


def _options(sources, policy):
    options = {}
    if sources is not None:
        options['sources_path'] = sources
    if policy is not None:
        options['field_policy_path'] = policy
    return options


def _resolution_input(path):
    raw, fingerprint = _read(path, fmt.MAX_MANIFEST_BYTES)
    body = fmt.parse_json(raw)
    _need(type(body) is dict, 'resolution')
    fmt.identifier(body.get('resolutionId'))
    return body['resolutionId'], fingerprint


def _chain(w, receipts):
    """Every committed snapshot of this workspace, oldest first; already validated by _load."""
    return [w.json('initial.json')['current']] + [receipt['after'] for receipt in receipts]


def _bindings(w, proposal, request, grant, sources, policy, resolution=None):
    w.require()
    initial, current, receipts, pending = _load(w)
    _need(initial['workspaceId'] == proposal['workspaceId'] and current == proposal['before'], 'revision')
    _need(pending is None or pending['proposal'] == proposal, 'pending')
    _launch_matches(initial, grant, policy)
    operation, actual = _inputs(request, grant, sources, policy)
    _need(('resolution' in proposal) == (resolution is not None), 'source')
    if resolution is not None:
        identifier, fingerprint = _resolution_input(resolution)
        _need(identifier == proposal['operationId'] and fingerprint == proposal['resolution']['file'] and
              operation == proposal['resolution']['requestOperationId'] and
              proposal['resolution']['base'] in _chain(w, receipts), 'source')
        _snapshot(w, proposal['resolution']['base']['snapshot'], proposal['resolution']['base'])
        operation = identifier
    _need(operation == proposal['operationId'] and actual == proposal['inputs'], 'source')
    _snapshot(w, proposal['before']['snapshot'], proposal['before'])


def _status(init, current, receipts, pending):
    return {'ok': True, 'workspaceVersion': 1, 'workspaceId': init['workspaceId'], 'current': current,
            'committedCount': len(receipts), 'pendingOperationId': pending['proposal']['operationId'] if pending else None,
            'needsResume': current is None or pending is not None}


def _initial(value):
    fmt.keys(value, {'workspaceVersion', 'workspaceId', 'source', 'grant', 'fieldPolicy'})
    _need(type(value['workspaceVersion']) is int and value['workspaceVersion'] == 1, 'version')
    fmt.identifier(value['workspaceId'])
    for key in ('source', 'grant'):
        _fingerprint(value[key])
    if value['fieldPolicy'] is not None:
        _fingerprint(value['fieldPolicy'])


def _launch(grant, policy, collection_id=None):
    raw, binding = _read(grant, fmt.MAX_MANIFEST_BYTES)
    scope = fmt.parse_json(raw)
    _need(type(scope) is dict, 'scope')
    version = scope.get('scopeVersion')
    _need(type(version) is int and version in (1, 2), 'scope')
    checked = fmt._operation_constraints(scope, version)
    _need(collection_id is None or checked['collectionId'] == collection_id, 'scope')
    policy_binding = _read(policy, fmt.MAX_MANIFEST_BYTES)[1] if policy is not None else None
    if policy is not None:
        import showpapers_grants
        showpapers_grants.load(policy, grant).check()
    _need(_read(grant, fmt.MAX_MANIFEST_BYTES)[1] == binding, 'source')
    return binding, policy_binding


def _launch_matches(initial, grant, policy):
    _initial(initial)
    binding, policy_binding = _launch(grant, policy)
    _need(binding == initial['grant'] and policy_binding == initial['fieldPolicy'], 'source')


@_safe
def init(source, workspace, grant, *, field_policy_path=None, check=lambda: None):
    source = Path(source).absolute()
    _, binding = _read(source, fmt.MAX_ARCHIVE_BYTES)
    summary = api.validate(source)
    grant_binding, policy_binding = _launch(grant, field_policy_path, summary['collectionId'])
    _need(_read(source, fmt.MAX_ARCHIVE_BYTES)[1] == binding, 'source')
    target = Path(workspace).absolute()
    _need(str(target.parent) == os.path.realpath(target.parent), 'source')
    check()
    os.mkdir(target, 0o700)  # Never adopts, empties or overwrites an existing directory.
    os.mkdir(target / 'data', 0o700)
    fd = os.open(target / 'data' / '.lock', os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.fsync(fd); os.close(fd)
    parent = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(parent)
    finally: os.close(parent)
    with _Workspace(target, check) as w:
        initial = {'workspaceVersion': 1, 'workspaceId': str(uuid.uuid4()), 'source': binding,
                   'grant': grant_binding, 'fieldPolicy': policy_binding}
        def guard():
            _need(_read(source, fmt.MAX_ARCHIVE_BYTES)[1] == binding, 'source')
            _launch_matches(initial, grant, field_policy_path)
        w.publish('init.json', initial, guard)
        return _resume_init(w, source, grant, field_policy_path)


def _recover_reservation(w, source, grant, policy):
    # Before the first metadata link there is no admitted snapshot. Resume only the
    # exact fully-written, input-bound reservation; never infer a current revision.
    names = set(os.listdir(w.file('.lock').parent)) - {'.lock'}
    _need(len(names) == 1 and all(re.fullmatch(r'\.metadata-[a-f0-9]{32}\.tmp', n) for n in names))
    initial = w.json(next(iter(names))); _initial(initial)
    _need(_read(source, fmt.MAX_ARCHIVE_BYTES)[1] == initial['source'], 'source')
    _launch_matches(initial, grant, policy)
    def guard():
        _need(_read(source, fmt.MAX_ARCHIVE_BYTES)[1] == initial['source'], 'source')
        _launch_matches(initial, grant, policy)
    w.publish('init.json', initial, guard)


def _resume_init(w, source, grant, policy):
    initial = w.json('init.json'); _initial(initial)
    binding = initial['source']
    def guard():
        w.require()
        _need(_read(source, fmt.MAX_ARCHIVE_BYTES)[1] == binding, 'source')
        _launch_matches(initial, grant, policy)
    guard()
    summary = api.validate(source)
    _launch(grant, policy, summary['collectionId'])
    if not w.exists('v0000.showpapers'):
        raw, current = _read(source, fmt.MAX_ARCHIVE_BYTES)
        _need(current == binding, 'source')
        # A killed write is never overwritten or trusted on the next attempt.
        name = '.initial-stage-' + uuid.uuid4().hex + '.showpapers'
        _need(w.budget() + len(raw) <= MAX_WORKSPACE_BYTES, 'limit')
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=w.directory)
        with os.fdopen(fd, 'wb') as stream:
            for offset in range(0, len(raw), 1024 * 1024):
                w.require(); stream.write(raw[offset:offset + 1024 * 1024])
            stream.flush(); os.fsync(stream.fileno())
        guard()
        w.publish_snapshot(name, 'v0000.showpapers', binding, guard)
    snapshot = _snapshot(w, 'v0000.showpapers')
    _need(snapshot['sha256'] == binding['sha256'], 'source')
    if not w.exists('initial.json'):
        def final():
            guard(); _snapshot(w, 'v0000.showpapers', snapshot)
        w.publish('initial.json', {'workspaceId': initial['workspaceId'], 'current': snapshot}, final)
    return _status(*_load(w))


@_safe
def status(workspace, *, check=lambda: None):
    with _Workspace(workspace, check) as w:
        return _status(*_load(w))


@_safe
def open_workspace(workspace, grant, *, field_policy_path=None, include_content=False, check=lambda: None):
    _need(type(include_content) is bool, 'arguments')
    with _Workspace(workspace, check) as w:
        initial, current, receipts, pending = _load(w)
        _need(current is not None, 'pending')
        _launch_matches(initial, grant, field_policy_path)
        if field_policy_path is None:
            # The general inspection API deliberately does not infer a read policy
            # from a mutation scope. Refuse a partial-scope open rather than expose
            # excluded IDs/titles; field policies support projected reads explicitly.
            manifest, _ = fmt.read_archive(w.file(current['snapshot']))
            raw, _ = _read(grant, fmt.MAX_MANIFEST_BYTES)
            scope = fmt.parse_json(raw)
            for kind, group in fmt.RECORD_GROUPS.items():
                _need({row['id'] for row in manifest[group]} <= set(scope[kind + 'Ids']), 'scope')
        options = ({'constraints_path': grant, 'field_policy_path': field_policy_path}
                   if field_policy_path is not None else {})
        result = api.inspect(w.file(current['snapshot']), include_content=include_content, **options)
        _launch_matches(initial, grant, field_policy_path)
        _snapshot(w, current['snapshot'], current)
        w.require()
        return dict(_status(initial, current, receipts, pending), inspection=result)


@_safe
def preview(workspace, request, grant, *, sources_path=None, field_policy_path=None, check=lambda: None):
    with _Workspace(workspace, check) as w:
        initial, current, receipts, pending = _load(w)
        _need(current is not None and pending is None, 'pending')
        _launch_matches(initial, grant, field_policy_path)
        operation, inputs = _inputs(request, grant, sources_path, field_policy_path)
        result = api.preview(w.file(current['snapshot']), request, grant, **_options(sources_path, field_policy_path))
        _need(result['toRevision'] == current['revision'] + 1, 'operation')
        proposal = {'workspaceId': initial['workspaceId'], 'operationId': operation, 'before': current,
                    'inputs': inputs, 'planSha256': _digest(result)}
        _proposal(proposal)
        _bindings(w, proposal, request, grant, sources_path, field_policy_path)
        name = 'p-' + operation + '.json'
        if w.exists(name):
            _need(w.json(name) == proposal, 'replay')
        else:
            _need(sum(name.startswith('p-') and name.endswith('.json') for name in os.listdir(w.file('.lock').parent)) < MAX_PROPOSALS, 'limit')
            w.publish(name, proposal, lambda: _bindings(w, proposal, request, grant, sources_path, field_policy_path))
        return {'ok': True, 'operationId': operation, 'before': current, 'plan': result}


def _engine(w, proposal, request, grant, sources, policy, resolution):
    """(replan, save to a new stage) for an ordinary commit or an explicit conflict resolution."""
    head = w.file(proposal['before']['snapshot'])
    if resolution is None:
        return (lambda: api.preview(head, request, grant, **_options(sources, policy)),
                lambda target: api.save_copy(head, request, grant, target, **_options(sources, policy)))
    base = w.file(proposal['resolution']['base']['snapshot'])
    options = dict(_options(sources, policy), mine_operations_path=request)
    return (lambda: api.preview_resolution(base, head, grant, resolution, **options),
            lambda target: api.save_resolved_copy(base, head, grant, resolution, target, **options))


def _complete(w, intent, request, grant, sources, policy, resolution=None):
    index = intent['index']; proposal = intent['proposal']
    def bind():
        _bindings(w, proposal, request, grant, sources, policy, resolution)
    plan, save = _engine(w, proposal, request, grant, sources, policy, resolution)
    bind()
    result = plan()
    _need(_digest(result) == proposal['planSha256'], 'source')
    # Replaying through the same writer proves any already-staged file is the exact requested result.
    stage = f'.stage-{index:04}.showpapers'
    if w.exists(stage):
        replay = '.replay-' + uuid.uuid4().hex + '.showpapers'
        target = replay
    else:
        replay = None; target = stage
    _need(w.budget() + 2 * fmt.MAX_ARCHIVE_BYTES <= MAX_WORKSPACE_BYTES, 'limit')
    save(w.file(target))
    _boundary('after-stage')
    bind()
    made = _snapshot(w, target)
    made['snapshot'] = f'v{index:04}.showpapers'
    if replay is not None:
        staged = _snapshot(w, stage); staged['snapshot'] = made['snapshot']
        _need(staged == made, 'source')
        os.unlink(replay, dir_fd=w.directory)
    ready = {'intentSha256': _digest(intent), 'after': made}
    _ready(ready, intent)
    ready_name = f'r{index:04}.json'
    if w.exists(ready_name):
        _need(w.json(ready_name) == ready, 'source')
    else:
        bind()
        w.publish(ready_name, ready, bind)
    bind()
    w.publish_snapshot(stage, made['snapshot'], made, bind)
    bind()
    def final():
        bind()
        _snapshot(w, made['snapshot'], made)
    w.publish(f'c{index:04}.json', ready, final)
    return {'ok': True, 'operationId': proposal['operationId'], 'current': made, 'committedResult': made, 'alreadyCommitted': False}


@_safe
def commit(workspace, request, grant, *, sources_path=None, field_policy_path=None, check=lambda: None):
    with _Workspace(workspace, check) as w:
        initial, current, receipts, pending = _load(w)
        _need(current is not None, 'pending')
        _launch_matches(initial, grant, field_policy_path)
        operation, inputs = _inputs(request, grant, sources_path, field_policy_path)
        proposal = w.json('p-' + operation + '.json'); _proposal(proposal)
        _need(proposal['workspaceId'] == initial['workspaceId'] and proposal['inputs'] == inputs, 'source')
        _need('resolution' not in proposal, 'replay')
        for index, receipt in enumerate(receipts, 1):
            old = w.json(f'i{index:04}.json')
            if old['proposal']['operationId'] == operation:
                _need(old['proposal'] == proposal, 'replay')
                return {'ok': True, 'operationId': operation, 'current': current,
                        'committedResult': receipt['after'], 'alreadyCommitted': True}
            # The same request already entered the head through an explicit resolution.
            _need(old['proposal'].get('resolution', {}).get('requestOperationId') != operation, 'replay')
        _need(pending is None, 'pending')
        _need(current == proposal['before'], 'revision')
        _need(len(receipts) < MAX_REVISIONS, 'limit')
        intent = {'workspaceId': initial['workspaceId'], 'index': len(receipts) + 1, 'before': current, 'proposal': proposal}
        _bindings(w, proposal, request, grant, sources_path, field_policy_path)
        w.publish(f"i{intent['index']:04}.json", intent, lambda: _bindings(w, proposal, request, grant, sources_path, field_policy_path))
        return _complete(w, intent, request, grant, sources_path, field_policy_path)


def _stale(w, receipts, current, request, grant, sources_path, field_policy_path):
    """The exact committed base a stale request names, never an inferred or external snapshot."""
    operation, inputs = _inputs(request, grant, sources_path, field_policy_path)
    revision = fmt.parse_json(_read(request, fmt.MAX_MANIFEST_BYTES)[0]).get('expectedRevision')
    _need(type(revision) is int, 'revision')
    chain = _chain(w, receipts)
    base = next((snapshot for snapshot in chain if snapshot['revision'] == revision), None)
    _need(base is not None and base != current, 'revision')
    for index in range(1, len(receipts) + 1):
        old = w.json(f'i{index:04}.json')['proposal']
        _need(operation not in (old['operationId'], old.get('resolution', {}).get('requestOperationId')), 'replay')
    return operation, inputs, base


@_safe
def conflicts(workspace, request, grant, *, sources_path=None, field_policy_path=None, include_content=False,
              check=lambda: None):
    """Read-only: compare a request that names an older committed revision with the current head."""
    _need(type(include_content) is bool, 'arguments')
    with _Workspace(workspace, check) as w:
        initial, current, receipts, pending = _load(w)
        _need(current is not None and pending is None, 'pending')
        _launch_matches(initial, grant, field_policy_path)
        operation, _, base = _stale(w, receipts, current, request, grant, sources_path, field_policy_path)
        report = api.conflicts(w.file(base['snapshot']), w.file(current['snapshot']), grant, mine_operations_path=request,
                               include_content=include_content, **_options(sources_path, field_policy_path))
        _launch_matches(initial, grant, field_policy_path)
        _snapshot(w, base['snapshot'], base); _snapshot(w, current['snapshot'], current)
        w.require()
        return {'ok': True, 'operationId': operation, 'base': base, 'current': current, 'report': report}


@_safe
def resolve(workspace, request, resolution, grant, *, sources_path=None, field_policy_path=None, check=lambda: None):
    """Commit one explicit choice per conflict as the next revision, with the ordinary receipts."""
    with _Workspace(workspace, check) as w:
        initial, current, receipts, pending = _load(w)
        _need(current is not None, 'pending')
        _launch_matches(initial, grant, field_policy_path)
        identifier, fingerprint = _resolution_input(resolution)
        for index, receipt in enumerate(receipts, 1):
            old = w.json(f'i{index:04}.json')['proposal']
            if old['operationId'] == identifier:
                operation, inputs = _inputs(request, grant, sources_path, field_policy_path)
                _need(old.get('resolution', {}).get('file') == fingerprint and old['inputs'] == inputs and
                      old['resolution']['requestOperationId'] == operation, 'replay')
                return {'ok': True, 'operationId': identifier, 'current': current,
                        'committedResult': receipt['after'], 'alreadyCommitted': True}
        _need(pending is None, 'pending')
        _need(len(receipts) < MAX_REVISIONS, 'limit')
        operation, inputs, base = _stale(w, receipts, current, request, grant, sources_path, field_policy_path)
        _need(operation != identifier, 'replay')
        result = api.preview_resolution(w.file(base['snapshot']), w.file(current['snapshot']), grant, resolution,
                                        mine_operations_path=request, **_options(sources_path, field_policy_path))
        _need(not result['noChange'], 'no_change')
        _need(result['toRevision'] == current['revision'] + 1, 'operation')
        proposal = {'workspaceId': initial['workspaceId'], 'operationId': identifier, 'before': current, 'inputs': inputs,
                    'planSha256': _digest(result),
                    'resolution': {'file': fingerprint, 'base': base, 'requestOperationId': operation}}
        _proposal(proposal)
        guard = lambda: _bindings(w, proposal, request, grant, sources_path, field_policy_path, resolution)
        guard()
        name = 'p-' + identifier + '.json'
        if w.exists(name):
            _need(w.json(name) == proposal, 'replay')
        else:
            _need(sum(n.startswith('p-') and n.endswith('.json') for n in os.listdir(w.file('.lock').parent)) < MAX_PROPOSALS, 'limit')
            w.publish(name, proposal, guard)
        intent = {'workspaceId': initial['workspaceId'], 'index': len(receipts) + 1, 'before': current, 'proposal': proposal}
        w.publish(f"i{intent['index']:04}.json", intent, guard)
        return _complete(w, intent, request, grant, sources_path, field_policy_path, resolution)


@_safe
def resume(workspace, request=None, grant=None, *, init_source=None, sources_path=None, field_policy_path=None,
           resolution_path=None, check=lambda: None):
    with _Workspace(workspace, check) as w:
        _need(grant is not None, 'arguments')
        if not w.exists('init.json'):
            _need(init_source is not None and request is None, 'arguments')
            _recover_reservation(w, init_source, grant, field_policy_path)
        initial, current, receipts, pending = _load(w)
        _launch_matches(initial, grant, field_policy_path)
        if current is None:
            _need(init_source is not None and request is None, 'arguments')
            return _resume_init(w, init_source, grant, field_policy_path)
        if init_source is not None:
            _need(request is None and not receipts and pending is None, 'arguments')
            return _resume_init(w, init_source, grant, field_policy_path)
        _need(request is not None, 'pending')
        if pending is None:
            operation, inputs = _inputs(request, grant, sources_path, field_policy_path)
            resolved = _resolution_input(resolution_path) if resolution_path is not None else None
            for index, receipt in enumerate(receipts, 1):
                proposal = w.json(f'i{index:04}.json')['proposal']
                if proposal['operationId'] == (resolved[0] if resolved else operation):
                    _need(proposal['inputs'] == inputs, 'source')
                    _need(resolved is None or (proposal.get('resolution', {}).get('file') == resolved[1] and
                                               proposal['resolution']['requestOperationId'] == operation), 'source')
                    return {'ok': True, 'operationId': proposal['operationId'], 'current': current,
                            'committedResult': receipt['after'], 'alreadyCommitted': True}
            _need(False, 'pending')
        return _complete(w, pending, request, grant, sources_path, field_policy_path, resolution_path)


def main(argv=None):
    try:
        parser = fmt.Parser(description=__doc__)
        commands = parser.add_subparsers(dest='command', required=True)
        create = commands.add_parser('init'); create.add_argument('--source', required=True); create.add_argument('--workspace', required=True); create.add_argument('--grant', required=True); create.add_argument('--field-policy')
        for name in ('status', 'open', 'preview', 'commit', 'resume', 'conflicts', 'resolve'):
            sub = commands.add_parser(name); sub.add_argument('--workspace', required=True)
            if name == 'open':
                sub.add_argument('--include-content', action='store_true'); sub.add_argument('--grant', required=True); sub.add_argument('--field-policy')
            if name in ('preview', 'commit', 'resume', 'conflicts', 'resolve'):
                sub.add_argument('--request', required=name != 'resume'); sub.add_argument('--grant', required=True)
                sub.add_argument('--sources'); sub.add_argument('--field-policy')
            if name == 'resume':
                sub.add_argument('--init-source')
            if name in ('resume', 'resolve'):
                sub.add_argument('--resolution', required=name == 'resolve')
            if name == 'conflicts':
                sub.add_argument('--include-content', action='store_true')
        args = parser.parse_args(argv)
        if args.command == 'init': result = init(args.source, args.workspace, args.grant, field_policy_path=args.field_policy)
        elif args.command == 'status': result = status(args.workspace)
        elif args.command == 'open': result = open_workspace(args.workspace, args.grant, include_content=args.include_content, field_policy_path=args.field_policy)
        elif args.command == 'resolve':
            result = resolve(args.workspace, args.request, args.resolution, args.grant,
                             sources_path=args.sources, field_policy_path=args.field_policy)
        else:
            options = dict(sources_path=args.sources, field_policy_path=args.field_policy)
            if args.command == 'resume': options.update(init_source=args.init_source, resolution_path=args.resolution)
            if args.command == 'conflicts': options['include_content'] = args.include_content
            result = globals()[args.command](args.workspace, args.request, args.grant, **options)
        print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
        return 0
    except fmt.FormatError as failure:
        print(json.dumps({'ok': False, 'error': failure.code, 'message': 'Workspace operation refused.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
