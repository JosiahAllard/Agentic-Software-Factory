"""The gate-OK record: the gate's word that it passed exactly one commit (ARCHITECTURE.md
"Gate-OK record").

gate.py writes `<dev_drive>/pm-kit/kit-gate-ok/<repo key>/<commit>.json` (record_dir; the repo
key is records.place.repo_key's, one for every worktree of a repository) for the commit it
passed; landings, the kit guard and the pre-push hook read it. The folder is outside every
checkout and every agent's temp folder, where agents' own file writes do not reach; the git
common dir, where records were before, is writable by any session that can write `.git`. With no
`dev_drive` there is no record folder: a write writes only the .git copy, then raises
NoRecordFolder. One record per commit; a later pass of the same commit replaces it (atomically: a
temp file in the same folder, then os.replace).

The .git copy, `<git common dir>/kit-gate-ok/<commit>.json` (legacy_dir), is used only when
there is no record folder (no dev_drive): it is then the one record written (write) and the one
read (read). With a dev_drive it is neither written nor read. It stays for load nodes set up
without a dev_drive (docs/load-nodes.md "Gating a branch on a node").

Tested records (KINDS: "daily", "promotion") are this same record, each kind in a folder of its
own beside kit-gate-ok (`<dev_drive>/pm-kit/<KINDS[kind]>/<repo key>/`, the .git copy
`<git common dir>/<KINDS[kind]>/`). A daily record is always a --full pass; a promotion's mode is
what it ran ("select" unless its config escalation ran it in full). record_tested_pass writes one,
verify_tested reads it. The folder functions, read, write and verify take `kind`; None is the
gate-OK record.

The record (SCHEMA 1), every key always present:

  schema        1
  verdict       "OK" (only a pass is ever written)
  commit        the full hash passed; also the file name
  tree          that commit's tree hash
  base          the base commit the gate compared against, or null
  gate_config   sha256 (hex) of the canonical JSON of the gate config the run used
                (config_hash: sorted keys, no spaces, UTF-8)
  kit           the kit's identity: {"version": ..., "commit": ...} as the caller states it
  author        the commit's author as git records it, `Name <email>`: the identity owner
                checks were made against
  repo          the repository name (`.kit.json` "repo".name)
  started_utc   when the gate started, ended_utc when it passed (YYYY-MM-DDTHH:MM:SSZ)
  steps         [{"name", "result", "seconds"}] in run order

Added keys (ADDED_KEYS; written on every new record, not required of an older one by verify()
unless the caller asks for what they carry):

  config_rev    the full hash of the commit whose `.kit.json` the gate config came from (the
                branch gate: the commit gated; a landing: --expect-main), or null
  source        where the gate's steps came from: "gate.json" (the "gate" section of
                `.kit.json`, inline or in its file) or "auto" (detected from the tree,
                gate.detect), or null. A reader takes a record without it as "gate.json"
                (source_of)
  full          true when the gate ran `--full` (every non-manual step, each by its full_cmd when
                it has one; gate.steps.select), false for any other run. Only the JSON
                true counts (full_of): a record without the key was written by a gate from before
                records said which (an older kit, or a kit branch's own gate.py), and a reader that needs a full gate (verify_full; a landing's branch tips)
                takes it as not full. Nothing in the older keys tells the two apart: the steps
                carry names only, and a step with a full_cmd has the same name in both modes
  mode          what the gate selected (MODES): "full" (--full; `full` is true), "select" (the
                steps the change selects, plus "gate".smoke_steps; gate.steps.select) or
                "docs" (a docs-only change: only "gate".docs_only steps ran;
                gate.steps.docs_only). A record without it is "full" when full_of says so,
                else unknown (mode_of: None), and a landing takes an unknown mode only as --full
  input_keys    {"schema": 1, "steps": {name: {part: sha256}}}: for each step that asks for reuse
                and passed, the digests a landing compares to serve it from this record when
                main moved (gate.reuse, "The input tree"; INPUT_PARTS), or null (no step
                has one, or the gate wrote none: --only, a partial or docs-only gate, a work tree
                not clean at its start)

One more key, only on a record of a gate that ran on a load node (import_record, a branch's
gate: docs/load-nodes.md "Gating a branch on a node"; build with `ran_on`, a landing's merged gate
main checked with kitlib.loadnode.landgate); a record without it ran on this machine. verify()
neither requires nor checks it:

  ran_on        {"node": its registry name, "host_key": the SHA256 fingerprint of the host key
                main pinned for it, "job": the node job id}, written by main from its own
                registry, never taken from the node. Its record's input_keys is always null

verify() re-derives everything git can answer: the record's file name, `commit`, `tree` and
`author` must match the repository now, and `gate_config` must match the caller's config when it
passes one. With `config_at` (rev -> that commit's gate config) it instead requires `config_rev`,
a commit in `commit`'s history (or `commit` itself), and the hash of config_at(config_rev). A
record is evidence for a reader that trusts the record folder; it is not a signature.
"""
import datetime
import hashlib
import json
import os
import secrets

import kitlib.config as kconfig
from kitlib.config.types import ConfigError
from kitlib.fs import replace as fsreplace
from kitlib.git import cmd, refs, repo
from kitlib.records import place

DIR_NAME = 'kit-gate-ok'
# The tested records (record_tested_pass, verify_tested): the gate-OK record's format, in a folder
# of their own per kind beside kit-gate-ok, so a later branch gate of the same commit never
# replaces one. Fixed: a kind is one of these, never a folder name a caller makes up. "daily": a
# green daily full run of the commit (kitlib.dailyfull); "promotion": a passing gate of a commit
# made to promote the staging branch onto main, its `base` the main it was made on. With a staging
# branch, the pre-push hook lets main move only to a commit with one of them (docs/gate-records.md
# "Tested records").
KINDS = {'daily': 'kit-daily-full', 'promotion': 'kit-promotion'}
LABELS = {None: 'gate-OK', 'daily': 'daily full', 'promotion': 'promotion'}
SCHEMA = 1
VERDICT = 'OK'
KEYS = ('schema', 'verdict', 'commit', 'tree', 'base', 'gate_config', 'kit', 'author', 'repo',
        'started_utc', 'ended_utc', 'steps')
ADDED_KEYS = ('config_rev', 'source', 'full', 'mode', 'input_keys')
MODES = ('full', 'select', 'docs')
RAN_ON_KEYS = ('node', 'host_key', 'job')
RETRIES = 5         # tries of a record's os.replace while it fails with PermissionError
RETRY_SLEEP = 0.2   # seconds between them
# RETRIES as a wait for kitlib.fs.replace: try k starts at least k-1 pauses in, so the last (at
# 0.8 s or later) is past this bound and the one before it (at 0.6 s) is not; the half pause is
# the margin for a slow try.
REPLACE_WAIT = (RETRIES - 1.5) * RETRY_SLEEP


class RecordError(Exception):
    """A record could not be written, or git could not answer what writing it needs."""


class NoRecordFolder(RecordError):
    """There is no record folder: the machine config sets no dev_drive, or cannot be read."""


def utc(when=None):
    return (when or datetime.datetime.now(datetime.timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def config_hash(gate_config):
    """sha256 hex of the canonical JSON of `gate_config` (any JSON value)."""
    return hashlib.sha256(canonical_json(gate_config).encode('utf-8')).hexdigest()


def _common(root):
    common, why = repo.common_dir(root)
    if not common:
        raise RecordError('no git common dir for %s: %s' % (root, why))
    return common


def dir_name(kind=None):
    """The folder name of a record `kind`: None, the gate-OK record's (DIR_NAME), or one of KINDS.
    RecordError for any other kind."""
    if kind is None:
        return DIR_NAME
    if kind not in KINDS:
        raise RecordError('no record kind %r: the kinds are %s' % (kind, ', '.join(sorted(KINDS))))
    return KINDS[kind]


def machine_dir(kind=None):
    """`<dev_drive>/pm-kit/kit-gate-ok` (or the `kind`'s folder, dir_name): the folder every
    repository's record folder is in (place.kit_folder). NoRecordFolder when there is no
    dev_drive."""
    name = dir_name(kind)
    try:
        return place.kit_folder(name, '%s folder' % LABELS[kind])
    except place.NoKitFolder as e:
        raise NoRecordFolder(str(e))


def record_dir(root, kind=None):
    """The record folder of the repository `root` is in: `<dev_drive>/pm-kit/kit-gate-ok/<repo
    key>` (place.repo_key: one for every worktree of a repository; the `kind`'s folder for a
    tested record). RecordError when it cannot be worked out (no git common dir; NoRecordFolder:
    no dev_drive)."""
    common = _common(root)
    return os.path.join(machine_dir(kind), place.repo_key(common))


def legacy_dir(root, kind=None):
    """The old record folder, `<git common dir>/kit-gate-ok` (or the `kind`'s): written and read
    only when there is no record folder (no dev_drive; write, read)."""
    return os.path.join(_common(root), dir_name(kind))


def _name(commit):
    if not refs.is_sha(commit):
        raise RecordError('a gate-OK record is named by a full commit hash, not %r' % (commit,))
    return commit + '.json'


def record_path(root, commit, kind=None):
    return os.path.join(record_dir(root, kind), _name(commit))


def legacy_path(root, commit, kind=None):
    return os.path.join(legacy_dir(root, kind), _name(commit))


def is_record_name(name):
    """Whether a file name in a record folder is a record's (`<full hash>.json`), not a temp file."""
    return name.endswith('.json') and refs.is_sha(name[:-len('.json')])


def author_of(root, commit):
    """`(Name <email>, None)` of `commit`'s author as git records it, or `(None, why)`."""
    why = refs.bad_rev(commit)
    if why:
        return None, why
    out, why = cmd.run_text(['show', '-s', '--format=%an <%ae>', commit, '--'], root)
    if out is None or not out.strip():
        return None, why or 'git show printed no author for %s' % commit
    return out.strip(), None


def _facts(root, rev):
    commit, why = refs.resolve(rev, root)
    if not commit:
        raise RecordError('cannot resolve %s: %s' % (rev, why))
    tree, why = refs.tree_of(commit, root)
    if not tree:
        raise RecordError('cannot read the tree of %s: %s' % (commit, why))
    author, why = author_of(root, commit)
    if not author:
        raise RecordError('cannot read the author of %s: %s' % (commit, why))
    return commit, tree, author


def _step(s, i):
    if not isinstance(s, dict) or not isinstance(s.get('name'), str) or not s['name']:
        raise RecordError('steps[%d] must be an object with a name' % i)
    secs = s.get('seconds')
    if secs is not None and (isinstance(secs, bool) or not isinstance(secs, (int, float))):
        raise RecordError('steps[%d].seconds must be a number' % i)
    return {'name': s['name'], 'result': s.get('result', 'pass'), 'seconds': secs}


def source_of(record):
    """A record's `source`: "auto" or "gate.json"; a record written before the key, or with
    null, is "gate.json" (every gate then ran its configured steps)."""
    return record.get('source') or 'gate.json'


def full_of(record):
    """True only when the record says its gate ran --full (`full` is the JSON true); a record
    written before the key, or with anything else there, is not a full gate."""
    return record.get('full') is True


def mode_of(record):
    """A record's mode: its `mode` when it is one of MODES and agrees with `full` ("full" exactly
    when full_of); a record without the key is "full" when full_of, else None (unknown), as is
    one whose mode is anything else."""
    mode = record.get('mode')
    if mode is None:
        return 'full' if full_of(record) else None
    if mode not in MODES or (mode == 'full') != full_of(record):
        return None
    return mode


def verify_full(root, commit):
    """`(record, None)` when verify() takes `commit`'s record and it is from a --full gate
    (full_of), else `(None, why)`: verify's reason first, then that the gate was not --full."""
    rec, why = verify(root, commit)
    if rec is None:
        return None, why
    if full_of(rec):
        return rec, None
    if 'full' not in rec:
        return None, ('the record for %s does not say whether the gate ran --full (written by a '
                      'gate from before records said so), so it does not count' % rec['commit'])
    return None, 'the record for %s is from a gate run without --full' % rec['commit']


def gate_config(cfg):
    """The value a gate-OK record's `gate_config` hashes: `.kit.json` "gate" over its defaults.
    The kit guard and the pre-push hook verify a record with this same value. An auto-detected gate
    (gate.detect) hashes this too, not its detected steps: they are a function of the
    commit's tree (which the record names), its base's tree (`base`) and the kit (its `kit`), so
    the hash still names what decides the steps. A key of HASH_IF_SET that is unset is left out,
    so a config that sets none of them hashes as it did before the key existed: a record a kit
    without the key wrote still verifies. Each key of HASH_REMOVED is put in with the default it
    hashed as, so a record a kit that still had the key wrote verifies too. A renamed kind
    hashes under its old name (HASH_KIND_AS)."""
    gate = cfg.section('gate')
    hashed = {k: v for k, v in gate.items() if not (k in HASH_IF_SET and v is None)}
    hashed.update((k, v) for k, v in HASH_REMOVED.items() if k not in hashed)
    if isinstance(hashed.get('kind'), str):
        hashed['kind'] = HASH_KIND_AS.get(hashed['kind'], hashed['kind'])
    return hashed


# "gate" keys added after records were first written; unset, they are not in gate_config().
HASH_IF_SET = ('smoke_steps', 'docs_only')
# "gate" keys the schema no longer has (a config that sets one fails to load), with the default
# every config hashed them as while they were keys. Only the hash keeps them, never section() or
# the steps: dropping one would change every repo's hash, so every gate-OK record written on the
# other side of that kit change would fail verify_record (docs/gate-records.md "Gate-OK record").
HASH_REMOVED = {'python_tests': 'discover', 'python_tests_floor': None, 'trailers': None}
# A renamed kind hashes under the name it had when records were first written (kitlib/lock/
# kinds.json "aliases"), so "gate".kind under either name, and the default, hash as before the
# rename: records from both sides of it verify. Fixed here, never read from the table. A kind
# rename must add its entries here (the new name, and any name between), each mapped to the name
# first written, not the previous one (tests/gate/test_branch_gate.py checks it against the
# aliases). A repository-defined kind in "gate".kind goes through this same mapping.
HASH_KIND_AS = {'test': 'engine-test', 'app-run': 'engine-run'}


def gate_config_at(root, rev):
    """gate_config() of the config commit `rev` holds (kitlib.config.load_at); ConfigError when it
    cannot be read."""
    return gate_config(kconfig.load_at(root, rev))


def verify_record(root, commit, kind=None):
    """verify() with the gate config read from the record's own `config_rev` (the commit
    whose `.kit.json` the gate ran with): `(record, None)` or `(None, why)`. A record's .git copy
    counts only when there is no record folder (no dev_drive; read). `kind`: the tested record's
    folder (KINDS) instead of the gate-OK record's."""
    def config_at(rev):
        try:
            return gate_config_at(root, rev)
        except ConfigError as e:
            raise RecordError('the gate config at %s: %s' % (rev[:10], e))
    return verify(root, commit, config_at=config_at, kind=kind)


def verify_tested(root, commit, kind):
    """`(record, None)` when `commit` has a tested record of `kind` (one of KINDS) that
    verify_record takes, else `(None, why)`. A daily record must be a --full pass (full_of, mode
    "full"); a promotion record must have a known mode (mode_of: "full", "select" or "docs", what
    the promotion's selection ran). The caller checks anything more the kind needs (a promotion's
    `base`)."""
    if kind not in KINDS:
        return None, 'no record kind %r: the kinds are %s' % (kind, ', '.join(sorted(KINDS)))
    rec, why = verify_record(root, commit, kind)
    if rec is None:
        return None, why
    if kind == 'daily' and (not full_of(rec) or mode_of(rec) != 'full'):
        return None, 'the %s record for %s is not from a --full run' % (LABELS[kind], rec['commit'])
    if mode_of(rec) is None:
        return None, 'the %s record for %s names no mode it ran in' % (LABELS[kind], rec['commit'])
    return rec, None


def _input_keys(keys):
    """`keys` when it is None or {"schema": int, "steps": {name: {part: 64-hex}}}, else
    RecordError."""
    if keys is None:
        return None
    bad = RecordError('input_keys is {"schema": int, "steps": {name: {part: sha256}}}')
    if not isinstance(keys, dict) or set(keys) != {'schema', 'steps'} \
            or isinstance(keys['schema'], bool) or not isinstance(keys['schema'], int) \
            or not isinstance(keys['steps'], dict):
        raise bad
    for name, parts in keys['steps'].items():
        if not isinstance(name, str) or not name or not isinstance(parts, dict) or not parts:
            raise bad
        for part, digest in parts.items():
            if not isinstance(part, str) or not isinstance(digest, str) or len(digest) != 64 \
                    or digest.strip('0123456789abcdef'):
                raise bad
    return {'schema': keys['schema'],
            'steps': {n: dict(parts) for n, parts in keys['steps'].items()}}


def _ran_on(ran_on):
    """`ran_on` as a new dict when it is {"node", "host_key", "job"}, each a non-empty string,
    else RecordError."""
    if not isinstance(ran_on, dict) or set(ran_on) != set(RAN_ON_KEYS) or not all(
            isinstance(ran_on[k], str) and ran_on[k] for k in RAN_ON_KEYS):
        raise RecordError('ran_on is {%s}, each a string' % ', '.join(RAN_ON_KEYS))
    return dict(ran_on)


def build(root, rev, gate_config, kit, repo_name, started_utc, steps, base=None, ended_utc=None,
          config_rev=None, source=None, full=False, mode=None, input_keys=None, ran_on=None):
    """The record for `rev` (resolved here: commit, tree and author come from git, never the
    caller). `config_rev`: the commit the gate config was read from (resolved to a full hash).
    `source`: "gate.json" or "auto" (gate.detect), or None. `full`: the gate ran --full.
    `mode`: one of MODES; None: "full" with `full`, else "select". "full" goes with `full` and
    only with it (RecordError). `input_keys`: gate.reuse.record_keys, or None. `ran_on`:
    the steps ran on a load node ({"node", "host_key", "job"}, from main's registry), so the
    record gets that key and no input_keys (RecordError when both are given)."""
    if ran_on is not None:
        ran_on = _ran_on(ran_on)
        if input_keys is not None:
            raise RecordError('a record of a gate run on a load node has no input_keys')
    full = full is True
    if mode is None:
        mode = 'full' if full else 'select'
    if mode not in MODES or (mode == 'full') != full:
        raise RecordError('mode %r with full %r: the mode is one of %s, and "full" exactly when '
                          'the gate ran --full' % (mode, full, ', '.join(MODES)))
    commit, tree, author = _facts(root, rev)
    if base is not None and not refs.is_sha(base):
        resolved, why = refs.resolve(base, root)
        if not resolved:
            raise RecordError('cannot resolve the base %s: %s' % (base, why))
        base = resolved
    if config_rev is not None and not refs.is_sha(config_rev):
        resolved, why = refs.resolve(config_rev, root)
        if not resolved:
            raise RecordError('cannot resolve the config rev %s: %s' % (config_rev, why))
        config_rev = resolved
    if not isinstance(kit, dict) or not kit:
        raise RecordError('the kit identity is an object such as {"version": ..., "commit": ...}')
    input_keys = _input_keys(input_keys)
    rec = {
        'schema': SCHEMA, 'verdict': VERDICT, 'commit': commit, 'tree': tree, 'base': base,
        'gate_config': config_hash(gate_config), 'kit': dict(kit), 'author': author,
        'repo': repo_name or '', 'started_utc': started_utc, 'ended_utc': ended_utc or utc(),
        'steps': [_step(s, i) for i, s in enumerate(steps or [])],
        'config_rev': config_rev, 'source': source, 'full': full, 'mode': mode,
        'input_keys': input_keys,
    }
    if ran_on is not None:
        rec['ran_on'] = ran_on
    return rec


def write(root, record, kind=None):
    """Write `record` (from build()) to the record folder, atomically; returns its path.
    RecordError when the write fails. NoRecordFolder, with the .git copy (legacy_path) written
    instead, when there is no record folder (no dev_drive). `kind`: a tested record's folder."""
    commit = record.get('commit')
    old = legacy_path(root, commit, kind)
    try:
        path = record_path(root, commit, kind)
    except NoRecordFolder:
        # Kept for load nodes set up without a dev_drive (docs/load-nodes.md).
        _write_file(old, record)
        raise
    _write_file(path, record)
    return path


def _write_file(path, record):
    """Write `record` to `path`: a temp file, then os.replace, retried while a reader holds the
    old file open (PermissionError; kitlib.fs.replace for REPLACE_WAIT). RecordError on failure."""
    folder = os.path.dirname(path)
    try:
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, '.%s.%s.tmp' % (record['commit'], secrets.token_hex(4)))
        try:
            with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
                f.write(json.dumps(record, sort_keys=True, indent=1) + '\n')
            fsreplace.replace(tmp, path, wait=REPLACE_WAIT, pause=RETRY_SLEEP)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    except OSError as e:
        raise RecordError('cannot write %s: %s' % (path, e))
    return path


def record_pass(root, rev, gate_config, kit, repo_name, started_utc, steps, base=None,
                config_rev=None, source=None, full=False, mode=None, input_keys=None,
                ran_on=None):
    """build() then write(): `(path, record)`. Only a passing gate calls this."""
    rec = build(root, rev, gate_config, kit, repo_name, started_utc, steps, base,
                config_rev=config_rev, source=source, full=full, mode=mode,
                input_keys=input_keys, ran_on=ran_on)
    return write(root, rec), rec


def record_tested_pass(root, rev, kind, gate_config, kit, repo_name, started_utc, steps,
                       base=None, config_rev=None, source=None, full=True, mode=None):
    """A tested record (KINDS) of `rev`: build(), then write() to the kind's folder:
    `(path, record)`. `full` / `mode` as build(): what the run selected. A daily record is a
    --full run (full false is RecordError); a promotion is "select" (full=False) unless its config
    escalation ran it in full. `config_rev`: the commit whose `.kit.json` the run's gate came from
    (`rev` itself for a run of `rev`); verify_tested checks the hash against it. A promotion
    record's `base` is the main commit it was made on (40 hex). RecordError for another kind;
    NoRecordFolder as write()."""
    if kind not in KINDS:
        raise RecordError('no tested record kind %r: the kinds are %s'
                          % (kind, ', '.join(sorted(KINDS))))
    if kind == 'daily' and full is not True:
        raise RecordError('a daily record is a --full run: full must be True')
    rec = build(root, rev, gate_config, kit, repo_name, started_utc, steps, base,
                config_rev=config_rev, source=source, full=full, mode=mode)
    return write(root, rec, kind), rec


def _load(path):
    """`(record, None)`, `(None, 'none')` when there is no file, or `(None, why)`."""
    try:
        with open(path, encoding='utf-8') as f:
            rec = json.load(f)
    except FileNotFoundError:
        return None, 'none'
    except OSError as e:
        return None, 'cannot read %s: %s' % (path, e)
    except ValueError as e:
        return None, '%s is not a gate-OK record: %s' % (path, e)
    if not isinstance(rec, dict):
        return None, '%s is not a gate-OK record' % path
    return rec, None


def read(root, commit, kind=None):
    """`(record, None)`, `(None, 'none')` when there is no record, or `(None, why)` when the file
    is there and cannot be read as one. The record folder's file; only when there is no record
    folder (no dev_drive), the .git copy (legacy_path). `kind`: a tested record's folder."""
    try:
        old = legacy_path(root, commit, kind)
    except RecordError as e:
        return None, str(e)
    try:
        return _load(record_path(root, commit, kind))
    except NoRecordFolder:
        # Kept for load nodes set up without a dev_drive (docs/load-nodes.md).
        return _load(old)
    except RecordError as e:
        return None, str(e)


def verify(root, commit, gate_config=None, config_at=None, kind=None):
    """`(record, None)` when `commit` has a gate-OK record git still agrees with, else
    `(None, why)`. `gate_config` given: the record's hash must be that config's. `config_at` given
    (a callable: full hash -> the gate config that commit holds; it may raise RecordError): the
    record's `config_rev` must be a full hash in `commit`'s history and the record's hash must be
    config_at(config_rev)'s. `kind`: a tested record's folder (KINDS) instead."""
    if not refs.is_sha(commit):
        resolved, why = refs.resolve(commit, root) if isinstance(commit, str) else (None, None)
        if not resolved:
            return None, 'cannot resolve %r: %s' % (commit, why)
        commit = resolved
    rec, why = read(root, commit, kind)
    if rec is None:
        return None, ('no %s record for %s' % (LABELS.get(kind, kind), commit)) if why == 'none' \
            else why
    why = _check(root, commit, rec, gate_config, config_at)
    return (None, why) if why is not None else (rec, None)


def _check(root, commit, rec, gate_config=None, config_at=None):
    """Why the record `rec` (a dict) filed under the full hash `commit` is not one git still
    agrees with, or None: verify()'s checks, shared with import_record()."""
    missing = [k for k in KEYS if k not in rec]
    if missing:
        return 'the record for %s lacks %s' % (commit, ', '.join(missing))
    if rec['schema'] != SCHEMA:
        return 'the record for %s is schema %r, not %d' % (commit, rec['schema'], SCHEMA)
    if rec['verdict'] != VERDICT:
        return 'the record for %s says %r, not OK' % (commit, rec['verdict'])
    if rec['commit'] != commit:
        return 'the record filed under %s names commit %s' % (commit, rec['commit'])
    try:
        _, tree, author = _facts(root, commit)
    except RecordError as e:
        return str(e)
    if rec['tree'] != tree:
        return 'the record for %s names tree %s; git says %s' % (commit, rec['tree'], tree)
    if rec['author'] != author:
        return 'the record for %s names author %r; git says %r' % (commit, rec['author'], author)
    if gate_config is not None and rec['gate_config'] != config_hash(gate_config):
        return 'the record for %s was made with another gate config' % commit
    if config_at is not None:
        rev = rec.get('config_rev')
        if not refs.is_sha(rev):
            return 'the record for %s names no config_rev (%r)' % (commit, rev)
        inside, why = refs.is_ancestor(rev, commit, root)
        if inside is not True:
            return 'the record for %s names config_rev %s, which is not in its history%s' % (
                commit, rev, ': %s' % why if why else '')
        try:
            cfg = config_at(rev)
        except RecordError as e:
            return str(e)
        if rec['gate_config'] != config_hash(cfg):
            return ('the record for %s was made with another gate config than its '
                    'config_rev %s holds' % (commit, rev[:10]))
    return None


def import_record(root, rec, commit, ran_on, config_at, repo_name):
    """Take `rec`, the record a load node's `gate.py --full` wrote for the full hash `commit`
    (the commit main pushed it), into this machine's record folder: `(path, record)` or
    RecordError naming why not. Checked here, never taken from the node: everything verify()
    checks with `config_at` (the hash of the gate config main's own kit reads at `config_rev`,
    which is `commit` itself, as for any branch gate), that it is a --full pass (full_of, mode
    "full") and that `repo` is `repo_name`. `ran_on` ({"node", "host_key", "job"}, from main's
    registry) is added; `input_keys` is set to null, so a landing serves no step from a run on
    another machine. A local record of the same commit is replaced, as a later pass replaces it."""
    if not isinstance(rec, dict):
        raise RecordError('the node sent no gate-OK record')
    if not refs.is_sha(commit):
        raise RecordError('a gate-OK record is imported for a full commit hash, not %r' % (commit,))
    ran_on = _ran_on(ran_on)
    if config_at is None:
        raise RecordError('an imported record is checked against the gate config at its config_rev')
    why = _check(root, commit, rec, config_at=config_at)
    if why is not None:
        raise RecordError(why)
    if rec.get('config_rev') != commit:
        raise RecordError('the record for %s names config_rev %s, not the commit itself'
                          % (commit, rec.get('config_rev')))
    if not full_of(rec) or mode_of(rec) != 'full':
        raise RecordError('the record for %s is not from a --full gate' % commit)
    if rec.get('repo') != repo_name:
        raise RecordError('the record for %s is of repository %r, not %r'
                          % (commit, rec.get('repo'), repo_name))
    out = dict(rec)
    out['input_keys'] = None
    out['ran_on'] = dict(ran_on)
    return write(root, out), out
