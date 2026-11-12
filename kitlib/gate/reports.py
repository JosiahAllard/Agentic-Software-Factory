"""The reports a test runner leaves: JUnit XML files, a Gradle console log and `dotnet test`
console summaries (docs/gate-rerun-checkout.md "Targeted rerun").

Red-first (kitlib.redfirst.verdict) reads the JUnit counts. The targeted rerun (kitlib.gate.rerun)
reads a red gradle or dotnet step's failing test ids (gradle_red, dotnet_red) and, once the step
ran again whole, judges each id from that run's reports (judge):

- gradle: each `* What went wrong:` item of the log is a test task whose tests failed
  (`Execution failed for task ':m:test'.` and `> There were failing tests. See the report at:
  file:///<module>/build/reports/tests/...`), the module folder inside the tree. The ids come
  from the JUnit XML that task wrote in this run (`<module>/build/test-results/<task>` or
  `<task>-*`, at or after `since`), and that XML must hold exactly the tests and failures the
  log's `N tests completed, M failed` line of the task gives. Anything else is uncovered: a
  compile failure, a Gradle error after test lines (java.io.EOFException), stale or missing XML.
- dotnet: each `  Failed <name> [<time>]` line belongs to the next `Failed!  - Failed: n, ...
  - <X.dll> (<framework>)` summary, and the lines of each summary must add up to its n. The
  project folder is the `Test run for <folder>/bin/.../<X.dll>` line's. A build error, an
  aborted or crashed test host, or a summary of no test is uncovered.

An id passes its rerun (judge) when the step's fresh XML shows it run with no failure, error or
skip (gradle), or when the same dll and framework show no failed test and the red run's total
and skipped counts (dotnet). Anything else is not a pass.
"""
import glob
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

GRADLE, DOTNET = 'gradle', 'dotnet'

GRADLE_MARK = re.compile(r'^(?:> Task :|\* What went wrong:|BUILD FAILED)')
GRADLE_TASK = re.compile(r'^> Task (:\S+)')
GRADLE_COUNT = re.compile(r'^(\d+) tests? completed, (\d+) failed(?:, (\d+) skipped)?\s*$')
WENT_WRONG = '* What went wrong:'
GRADLE_EXEC = re.compile(r"^Execution failed for task '(:[^']+)'\.$")
GRADLE_TESTS = re.compile(r'^> There were failing tests\. See the report at: (file:\S+)$')
GRADLE_REPORT = re.compile(r'^(.+)/build/reports/tests/[^/]+/index\.html$')

DOTNET_RUN = re.compile(r'^Test run for (.+\.dll) \(')
DOTNET_FAILED = re.compile(r'^\s+Failed (\S.*?) \[[^\]]*\]\s*$')
DOTNET_SUMMARY = re.compile(r'^\s*(?:Passed|Failed)!\s+- Failed:\s*(\d+), Passed:\s*(\d+), '
                            r'Skipped:\s*(\d+), Total:\s*(\d+), Duration: .* - (\S+\.dll) '
                            r'\(([^)]+)\)\s*$')
DOTNET_BROKEN = re.compile(r'error (?:CS|MSB|NETSDK|NU)\d+|Build FAILED\.|Test Run Aborted|'
                           r'test run was aborted|[Tt]est host process crashed')
DOTNET_FOLDER = re.compile(r'^(.+)[\\/]bin[\\/]')     # greedy: the last bin folder's parent


# ---------------------------------------------------------------- JUnit XML

def junit_failures(root):
    """Each <failure> and <error> under a JUnit XML element: `(tag, type, message, test name)`."""
    out = []
    for case in root.iter('testcase'):
        name = '.'.join(x for x in (case.get('classname'), case.get('name')) if x)
        for el in case:
            if el.tag in ('failure', 'error'):
                out.append((el.tag, el.get('type') or '', el.get('message') or el.text or '', name))
    return out


def junit_file_counts(path, since=0.0):
    """(tests, failures + errors, failures) of the one JUnit XML file `path`: its root's counts,
    else the sum over its testsuite elements, and each failure (junit_failures). None when absent,
    older than `since`, unparseable, or a count that is no integer."""
    try:
        if os.path.getmtime(path) < since:
            return None
        root = ET.parse(path).getroot()
        suites = ([root] if root.get('tests') is not None
                  else [el for el in root.iter() if el.tag == 'testsuite'])
        tests = sum(int(el.get('tests', 0)) for el in suites)
        bad = sum(int(el.get('failures', 0)) + int(el.get('errors', 0)) for el in suites)
    except (ET.ParseError, OSError, ValueError):
        return None
    return tests, bad, junit_failures(root)


def is_class_of(path, name):
    """Whether the Gradle JUnit file `path`, `TEST-<class>.xml`, is for the class `name` in any
    package (`<pkg>.<name>`, or bare `<name>`) or one nested in it (`<pkg>.<name>$<Inner>`)."""
    base = os.path.basename(path)
    if not (base.startswith('TEST-') and base.endswith('.xml')):
        return False
    top = base[len('TEST-'):-len('.xml')].split('$', 1)[0]
    return top == name or top.endswith('.' + name)


def junit_counts(results_dir, since=0.0, cls=None):
    """(tests, failures + errors, failures) over the TEST-*.xml files at any depth under `results_dir`
    (Gradle writes one folder per test task) written at or after `since`; with `cls`, only the
    files of that class and its nested classes (is_class_of) count. None when a fresh file of any
    class would not parse or holds a count that is no integer: skipping it could turn a failing
    suite into a GREEN made of the others, and a broken file of another class fails closed too."""
    tests = bad = 0
    failures = []
    for f in sorted(set(glob.glob(os.path.join(glob.escape(results_dir), '**', 'TEST-*.xml'),
                                  recursive=True))):
        try:
            if os.path.getmtime(f) < since:
                continue
        except OSError:
            continue
        try:
            root = ET.parse(f).getroot()
            n = int(root.get('tests', 0))
            b = int(root.get('failures', 0)) + int(root.get('errors', 0))
        except (ET.ParseError, OSError, ValueError):
            return None
        if cls is None or is_class_of(f, cls):
            tests += n
            bad += b
            failures += junit_failures(root)
    return tests, bad, failures


def junit_cases(path):
    """[(id, class, status)] of each testcase in the JUnit XML file `path`: id `<classname>.<name>`,
    status `failure`, `error`, `skipped` or `ok`. None when it cannot be read or parsed."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    out = []
    for case in root.iter('testcase'):
        cls, name = case.get('classname') or '', case.get('name') or ''
        tags = {el.tag for el in case}
        status = next((t for t in ('error', 'failure', 'skipped') if t in tags), 'ok')
        out.append(('.'.join(x for x in (cls, name) if x), cls, status))
    return out


# ---------------------------------------------------------------- where a path is

def inside(top, path):
    """`path` relative to the tree `top` ('' for the top, '/'-separated), or None when it is
    outside it (another drive included)."""
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(top))
    except ValueError:
        return None
    if rel == os.curdir:
        return ''
    if rel.startswith(os.pardir) or os.path.isabs(rel):
        return None
    return rel.replace(os.sep, '/')


def _under(top, rel):
    return os.path.join(top, *rel.split('/')) if rel else top


# ---------------------------------------------------------------- gradle

def is_gradle(lines):
    return any(GRADLE_MARK.match(line) for line in lines)


def _went_wrong(lines):
    """`([(task path, report url)], uncovered)` of the log's `* What went wrong:` items."""
    items, uncovered = [], []
    for i, line in enumerate(lines):
        if line.strip() != WENT_WRONG:
            continue
        body = []
        for nxt in lines[i + 1:]:
            if not nxt.strip() or nxt.startswith('* '):
                break
            body.append(nxt.strip())
        ex = GRADLE_EXEC.match(body[0]) if body else None
        rep = GRADLE_TESTS.match(body[1]) if len(body) == 2 else None
        if ex and rep:
            items.append((ex.group(1), rep.group(1)))
        else:
            uncovered.append('gradle: a failure that is not failing tests: %s'
                             % (' '.join(body)[:200] or '(nothing said)'))
    return items, uncovered


def _task_counts(lines):
    """{task path: (tests, failed)} from each `N tests completed, M failed` line, of the most recent
    `> Task` header above it."""
    counts, task = {}, None
    for line in lines:
        t = GRADLE_TASK.match(line)
        if t:
            task = t.group(1)
            continue
        c = GRADLE_COUNT.match(line.strip())
        if c and task:
            counts[task] = (int(c.group(1)), int(c.group(2)))
    return counts


def _module_of(url):
    """The module folder a Gradle test report url names, or None."""
    try:
        path = urllib.request.url2pathname(urllib.parse.urlparse(url).path)
    except (ValueError, OSError):
        return None
    m = GRADLE_REPORT.match(path.replace('\\', '/'))
    return m.group(1) if m else None


def gradle_cases(module, task, since):
    """`(cases, None)`: junit_cases of every TEST-*.xml a Gradle test task wrote at or after
    `since` under `<module>/build/test-results/<name>` or `<name>-*` (`name`: the task's last
    part); or `(None, why)` when a fresh file cannot be read."""
    name = task.rsplit(':', 1)[-1]
    base = os.path.join(module, 'build', 'test-results')
    try:
        dirs = [os.path.join(base, d) for d in sorted(os.listdir(base))
                if d == name or d.startswith(name + '-')]
    except OSError:
        return [], None
    cases = []
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(glob.escape(d), 'TEST-*.xml'))):
            try:
                if os.path.getmtime(f) < since:
                    continue
            except OSError:
                continue
            got = junit_cases(f)
            if got is None:
                return None, '%s cannot be read' % f
            cases += got
    return cases, None


def gradle_red(lines, top, since):
    """`(failures, uncovered)` of a red gradle step's log (module docstring). `top` the tree and
    `since` the red gate's start (epoch seconds): with either None, the reports are not read and
    the step is uncovered."""
    items, uncovered = _went_wrong(lines)
    if uncovered:
        return [], uncovered
    if not items:
        return [], ['gradle: no test task failed (no `%s` item)' % WENT_WRONG]
    if top is None or since is None:
        return [], ['gradle: its test reports are not read here']
    counts, failures, seen = _task_counts(lines), [], set()
    for task, url in items:
        module = _module_of(url)
        scope = inside(top, module) if module else None
        if scope is None:
            uncovered.append('gradle %s: its report %s is not a module folder in the tree'
                             % (task, url))
            continue
        cases, why = gradle_cases(_under(top, scope), task, since)
        if cases is None:
            uncovered.append('gradle %s: %s' % (task, why))
            continue
        bad = [c for c in cases if c[2] in ('failure', 'error')]
        want = counts.get(task)
        if want != (len(cases), len(bad)):
            uncovered.append('gradle %s: its JUnit XML of this run holds %d test(s), %d failed; '
                             'the log says %s' % (task, len(cases), len(bad),
                                                  '%d, %d failed' % want if want else 'no count'))
            continue
        for tid, cls, _ in bad:
            if tid not in seen:
                seen.add(tid)
                failures.append({'id': tid, 'cls': cls, 'env': {}, 'runner': GRADLE,
                                 'scope': scope, 'report': {'task': task}})
    return failures, uncovered


def gradle_judge(failures, top, since):
    """{id: status} of the gradle `failures` of a red run, from the XML their tasks wrote at or
    after `since` (the rerun's start) in the tree `top`."""
    out = {}
    for f in failures:
        cases, why = gradle_cases(_under(top, f.get('scope') or ''), f['report']['task'], since)
        if cases is None:
            out[f['id']] = 'report unreadable'
            continue
        got = [st for tid, _, st in cases if tid == f['id']]
        out[f['id']] = ('not run' if not got else 'ok' if all(st == 'ok' for st in got) else
                        next(st for st in got if st != 'ok'))
    return out


# ---------------------------------------------------------------- dotnet

def is_dotnet(lines):
    return any(DOTNET_RUN.match(line) or DOTNET_SUMMARY.match(line) or DOTNET_BROKEN.search(line)
               for line in lines)


def dotnet_class(name):
    """The class of a `dotnet test` test name: its last dotted part dropped, arguments first."""
    bare = name.split('(', 1)[0]
    return bare.rsplit('.', 1)[0] if '.' in bare else bare


def _dotnet_runs(lines):
    """`(summaries, folders, stray)`: [(dll, framework, failed, skipped, total, [failed names])]
    in log order, {dll name lowercased: project folder}, and the Failed lines no summary follows."""
    summaries, folders, pending = [], {}, []
    for line in lines:
        r = DOTNET_RUN.match(line)
        if r:
            dll, folder = r.group(1), DOTNET_FOLDER.match(r.group(1))
            if folder:
                folders[dll.replace('\\', '/').rsplit('/', 1)[-1].lower()] = folder.group(1)
            continue
        f = DOTNET_FAILED.match(line)
        if f:
            pending.append(f.group(1))
            continue
        s = DOTNET_SUMMARY.match(line)
        if s:
            failed, _, skipped, total = (int(s.group(n)) for n in (1, 2, 3, 4))
            summaries.append((s.group(5), s.group(6), failed, skipped, total, pending))
            pending = []
    return summaries, folders, pending


def dotnet_red(lines, top):
    """`(failures, uncovered)` of a red dotnet step's log (module docstring). With `top` None the
    project folders are not checked and each failure's scope is None."""
    uncovered = ['dotnet: %s' % line.strip()[:200] for line in lines if DOTNET_BROKEN.search(line)]
    summaries, folders, stray = _dotnet_runs(lines)
    if stray:
        uncovered.append('dotnet: %d Failed line(s) with no test summary after them' % len(stray))
    if not summaries:
        uncovered.append('dotnet: no `dotnet test` summary')
    failures, seen = [], set()
    for dll, fw, failed, skipped, total, names in summaries:
        where = 'dotnet %s (%s)' % (dll, fw)
        if total < 1:
            uncovered.append('%s: no test ran' % where)
        if len(names) != failed:
            uncovered.append('%s: %d Failed line(s) against %d in its summary'
                             % (where, len(names), failed))
            continue
        scope = None
        if top is not None and names:
            folder = folders.get(dll.lower())
            scope = inside(top, folder) if folder else None
            if scope is None:
                uncovered.append('%s: no project folder in the tree (Test run for ...)' % where)
                continue
        for name in names:
            if name not in seen:
                seen.add(name)
                failures.append({'id': name, 'cls': dotnet_class(name), 'env': {},
                                 'runner': DOTNET, 'scope': scope,
                                 'report': {'dll': dll, 'fw': fw, 'total': total,
                                            'skipped': skipped}})
    return failures, uncovered


def dotnet_judge(failures, text):
    """{id: status} of the dotnet `failures` of a red run from the rerun's log `text`: `ok` when
    the latest summary of the same dll and framework shows no failed test, the red run's total and
    its skipped count; `Failed` when it names the id failed; else `not run`."""
    summaries, _, _ = _dotnet_runs((text or '').splitlines())
    last = {(dll.lower(), fw): (failed, skipped, total, names)
            for dll, fw, failed, skipped, total, names in summaries}
    out = {}
    for f in failures:
        r = f['report']
        got = last.get((r['dll'].lower(), r['fw']))
        if got and f['id'] in got[3]:
            out[f['id']] = 'Failed'
        elif got and got[0] == 0 and (got[1], got[2]) == (r['skipped'], r['total']):
            out[f['id']] = 'ok'
        else:
            out[f['id']] = 'not run'
    return out


# ---------------------------------------------------------------- either

def judge(failures, text, top, since):
    """{id: status} of a red run's report failures (each carries `runner`) after their step ran
    again whole: its log `text`, the tree `top`, the rerun's start `since` (epoch seconds)."""
    out = gradle_judge([f for f in failures if f.get('runner') == GRADLE], top, since)
    out.update(dotnet_judge([f for f in failures if f.get('runner') == DOTNET], text))
    return out
