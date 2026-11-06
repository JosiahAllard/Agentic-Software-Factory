"""What each test module costs, kept per day (docs/test-tools.md "kit_tests"): the data for a test
budget rule. kit-tests-times.json (selfrun.save_times) keeps only the last wall time per unit, for
scheduling; this is the measurement, and nothing here changes what runs.

The history file `kit-tests-history.jsonl` sits in the git common dir. A daily full run
(`kit_tests --full --both-tiers`, its fake-git pass) appends one line: {"v": 1, "date":
"YYYY-MM-DD", "label", "jobs", "wall" (the run's seconds), "modules": {module: {"tests", "wall",
"cpu", "status"}}}. A split module's units are one row: tests and seconds summed, the worst status.
`wall` is the unit seconds summed (units overlap, so it exceeds the run's wall time). `cpu` is
the pool worker job's cpu seconds (pool.Worker.cpu) summed the same way: exact for everything the
unit's tests start, but not the worker's preload, and null when any unit of the module ran in its
own process or its job could not be asked. Nothing here fails a run.
"""
import json
import os
import time

HISTORY_FILE = 'kit-tests-history.jsonl'
VERSION = 1
_WORST = {'OK': 0, 'FAILED': 1, 'TIMEOUT': 2}


def module_rows(results, cpus, module_of):
    """{module: {tests, wall, cpu, status}} from selfrun's results `((unit, 1, 1), status, ran,
    secs, text)` and `cpus` {unit: seconds}; module_of maps a unit to its module."""
    rows = {}
    for (unit, _, _), status, ran, secs, _text in results:
        row = rows.setdefault(module_of(unit), {'tests': 0, 'wall': 0.0, 'cpu': 0.0, 'status': 'OK'})
        row['tests'] += ran
        row['wall'] += secs
        c = cpus.get(unit)
        row['cpu'] = None if c is None or row['cpu'] is None else row['cpu'] + c
        if _WORST.get(status, 1) > _WORST.get(row['status'], 1):
            row['status'] = status
    for row in rows.values():
        row['wall'] = round(row['wall'], 1)
        if row['cpu'] is not None:
            row['cpu'] = round(row['cpu'], 2)
    return rows


def record(path, rows, wall, jobs, label, today=None):
    """Append this run's `rows` as one history line; False when it could not be written."""
    if not path:
        return False
    line = json.dumps({'v': VERSION, 'date': today or time.strftime('%Y-%m-%d'), 'label': label,
                       'jobs': jobs, 'wall': round(wall, 1), 'modules': rows}, sort_keys=True)
    try:
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(line + '\n')
    except OSError:
        return False
    return True


def load(path):
    """The history's records, oldest first; unreadable lines are skipped, a missing file is []."""
    out = []
    try:
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get('modules'), dict) \
                        and isinstance(rec.get('date'), str):
                    out.append(rec)
    except OSError:
        return []
    return out


def _package(module):
    parts = module.split('.')
    return parts[1] if len(parts) > 2 else '(tests)'


def _total(rows, key):
    return sum(r[key] for r in rows.values() if isinstance(r.get(key), (int, float)))


def baseline(records, since):
    """The record growth is measured from: the latest one dated `since` or earlier, else the
    earliest one after it; None with no record."""
    if not records:
        return None
    if since is None:
        return records[0]
    before = [r for r in records if r['date'] <= since]
    return before[-1] if before else records[0]


def _growth(now, then):
    if not then:
        return '-'
    return '%+.0f%%' % (100.0 * (now - then) / then)


def report(records, since=None, top=30, by='cpu'):
    """The lines of the cost report: the latest run's totals, its top `top` modules by `by`
    ('cpu' or 'wall'), and each package, with the change from the baseline record
    (`baseline(records, since)`). Read-only."""
    if not records:
        return ['kit_tests: no cost history yet: a daily full run (--full --both-tiers) writes it']
    last = records[-1]
    base = baseline(records, since)
    cmp_ = base if base is not last else None
    now, then = last['modules'], (cmp_['modules'] if cmp_ else {})
    lines = ['kit_tests cost: %d run(s) on record, %s .. %s' % (
        len(records), records[0]['date'], last['date'])]
    lines.append('latest run %s: %d module(s), %d test(s), cpu %.0fs, wall %.0fs (unit seconds '
                 'summed), run wall %.0fs' % (last['date'], len(now), _total(now, 'tests'),
                                              _total(now, 'cpu'), _total(now, 'wall'),
                                              last.get('wall') or 0))
    if cmp_:
        lines.append('growth since the %s run (%s): modules %+d, tests %+d, cpu %+.0fs (%s)' % (
            cmp_['date'], 'latest on or before %s' % since if since and cmp_['date'] <= since
            else 'earliest on record', len(now) - len(then),
            _total(now, 'tests') - _total(then, 'tests'), _total(now, 'cpu') - _total(then, 'cpu'),
            _growth(_total(now, 'cpu'), _total(then, 'cpu'))))
    unknown = sorted(m for m, r in now.items() if r.get('cpu') is None)
    if unknown:
        lines.append('cpu unknown for %d module(s) (own process or job not queryable), counted '
                     'as 0: %s' % (len(unknown), ', '.join(unknown[:5]) + (' ...' if len(unknown) > 5
                                                                           else '')))
    lines.append('')
    lines.append('top %d module(s) by %s:' % (top, by))
    lines.append('  %8s %7s %6s %9s %8s  %s' % ('cpu-s', 'wall-s', 'tests', 'cpu/test', 'growth',
                                                'module'))
    ranked = sorted(now.items(), key=lambda kv: (-(kv[1].get(by) or 0), kv[0]))
    for m, r in ranked[:top]:
        c, tests = r.get('cpu') or 0.0, r.get('tests') or 0
        old = then.get(m)
        lines.append('  %8.1f %7.1f %6d %9.3f %8s  %s' % (
            c, r.get('wall') or 0.0, tests, c / tests if tests else 0.0,
            ('new' if old is None else _growth(c, old.get('cpu'))) if cmp_ else '-', m))
    if cmp_:
        gone = sorted(m for m in then if m not in now)
        if gone:
            lines.append('  (%d module(s) in the %s run are gone)' % (len(gone), cmp_['date']))
    pk, pk_then = {}, {}
    for rows, into in ((now, pk), (then, pk_then)):
        for m, r in rows.items():
            p = into.setdefault(_package(m), {'cpu': 0.0, 'tests': 0, 'modules': 0})
            p['cpu'] += r.get('cpu') or 0.0
            p['tests'] += r.get('tests') or 0
            p['modules'] += 1
    lines.append('')
    lines.append('per package, by cpu:')
    lines.append('  %8s %6s %7s %9s %8s  %s' % ('cpu-s', 'tests', 'modules', 'cpu/test', 'growth',
                                                'package'))
    for name, p in sorted(pk.items(), key=lambda kv: (-kv[1]['cpu'], kv[0])):
        old = pk_then.get(name)
        lines.append('  %8.1f %6d %7d %9.3f %8s  tests/%s' % (
            p['cpu'], p['tests'], p['modules'], p['cpu'] / p['tests'] if p['tests'] else 0.0,
            _growth(p['cpu'], old['cpu'] if old else None) if cmp_ else '-', name))
    return lines


def history_path(common):
    """The history file in the git common dir `common`; None without one."""
    return os.path.join(common, HISTORY_FILE) if common else None
