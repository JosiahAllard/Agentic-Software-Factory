"""The pure parts of a red-first run: which runner takes a test file (`.kit.json` "redfirst".runners),
the command it runs, and the verdict read from its log and result files (docs/redfirst.md).

A runner is `{glob, kind, ...}`; the first whose glob matches the repo-relative path wins (fnmatch,
case-blind, `*` crosses `/`). Kinds:
  python   `python -m unittest -v <module>[.<filter>]`, cwd the throwaway root, PYTHONPATH its
           `pythonpath` folder; the module is the path relative to that folder, dotted
  gradle   `<gradlew> <project>:test --offline --tests <filter>` (no filter: `*.<FileName>`); the
           verdict from the file's own class's `TEST-<pkg>.<FileName>[$<Nested>].xml` under
           `<dir>/build/test-results/` (any test task's folder) written in this run
  dotnet   `dotnet test <csproj> --filter <filter>` (a bare name is `FullyQualifiedName~<name>`; no
           filter: `FullyQualifiedName~<FileName>`); the verdict from its .trx
  command  the runner's own `command`, a list of argv words (an engine's test runner, say), each
           word's placeholders filled (fill): {selector} the filter, else the file name without its
           extension; {path} the repo-relative path; {stem}; {tree} the throwaway root; {log_dir};
           {n} the selector's number. A first word with a slash that is not absolute is under the
           throwaway root. The verdict from the JUnit XML file its `result` names (filled the same
           way, under the throwaway root unless absolute) written in this run, else from the exit
           code: 0 GREEN, 1 RED, any other ERROR. A log line its `no_test` regex matches is NONE.
A runner's `args` are appended to its command (a gradle or dotnet no-reuse switch, say).

gradlew.bat is a batch file, so Windows runs it in cmd.exe, which reads its command line again
before gradle sees it: `--tests a&whoami` runs whoami. A gradle word after the program that holds
one of cmd.exe's characters (kitlib.guard.shell.CMD_REPARSE: & | < > ^ % ! ( ) " CR LF) is
refused when the selector is parsed (gradle_args), before anything is made or run. A command
runner whose program is a .bat or .cmd file is checked the same way, every word after it
(command_argv). Any other program gets each word as one argument: the argv goes to the process
as a list, never through a shell.
"""
import fnmatch
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

from kitlib.guard.shell import cmd_unsafe

RED, GREEN, NONE, ERROR = 'RED', 'GREEN', 'NONE', 'ERROR'
TIMEOUT = 'TIMEOUT'         # the command ran past its limit and was killed: no verdict (run.py)
EXIT_RED, EXIT_GREEN, EXIT_USAGE, EXIT_UNDECIDED, EXIT_LEFTOVER = 0, 1, 2, 3, 4
KINDS = ('python', 'gradle', 'dotnet', 'command')
NEEDS = {'gradle': ('dir', 'project'), 'dotnet': ('csproj',), 'command': ('command',)}


class Usage(Exception):
    """A usage error or a refusal: exit 2, nothing made."""


class Selector(object):
    def __init__(self, text, path, filt, runner, target):
        self.text, self.path, self.filter, self.runner, self.target = text, path, filt, runner, target

    @property
    def kind(self):
        return self.runner['kind']


def stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def matches(path, pattern):
    return fnmatch.fnmatchcase(path.lower(), pattern.replace('\\', '/').lower())


def module_of(path, pythonpath):
    """The unittest module of the repo-relative `path` under the folder `pythonpath`, or None."""
    root = (pythonpath or '.').replace('\\', '/').strip('/')
    rel = path
    if root not in ('', '.'):
        if not path.lower().startswith(root.lower() + '/'):
            return None
        rel = path[len(root) + 1:]
    if not rel.endswith('.py'):
        return None
    parts = rel[:-3].split('/')
    return '.'.join(parts) if all(re.fullmatch(r'[A-Za-z_]\w*', p) for p in parts) else None


def parse_selector(text, runners):
    """A Selector for `--tests` text, or Usage."""
    text = text.strip()                      # a CRLF list leaves a CR on each value
    path, _, filt = text.partition('::')
    filt = filt.strip()
    path = path.replace('\\', '/').strip()
    if not path or path.startswith('/') or '..' in path.split('/') or re.match(r'^[A-Za-z]:', path):
        raise Usage('usage: --tests %r: a repo-relative file path, then optionally ::<filter>' % text)
    for r in runners:
        if not matches(path, r['glob']):
            continue
        missing = [k for k in NEEDS.get(r['kind'], ()) if not r.get(k)]
        if missing:
            raise Usage('usage: the %s runner for %r has no %s (.kit.json "redfirst".runners)'
                        % (r['kind'], r['glob'], ', '.join(missing)))
        if r['kind'] == 'python':
            mod = module_of(path, r.get('pythonpath'))
            if mod is None:
                raise Usage('usage: --tests %r: not a .py file under %s, so no unittest module'
                            % (text, r.get('pythonpath') or '.'))
            return Selector(text, path, filt, r, mod)
        if r['kind'] == 'command':
            sel = Selector(text, path, filt, r, None)
            command_argv(sel, '.', '.', 1)   # refuses here, before anything is made
            return sel
        sel = Selector(text, path, filt, r, r['project'] if r['kind'] == 'gradle' else r['csproj'])
        if sel.kind == 'gradle':
            gradle_args(sel)                 # refuses here, before anything is made
        return sel
    raise Usage('usage: --tests %r: no runner takes it (.kit.json "redfirst".runners: %s)'
                % (text, ', '.join(r['glob'] for r in runners) or 'none'))


_CMD_CHAR_NAMES = {'\r': 'a carriage return', '\n': 'a line break'}


def gradle_args(sel):
    """The words after gradlew for a gradle selector, or Usage when one holds a character cmd.exe
    reads again (cmd_unsafe): gradlew.bat runs in cmd.exe, so `a&whoami` would run whoami."""
    filt = sel.filter or '*.' + stem(sel.path)
    task = '%s:test' % sel.target
    extra = list(sel.runner.get('args') or [])
    runner = 'of the runner for %r in `.kit.json` "redfirst".runners' % sel.runner.get('glob')
    fix_filter = ('a gradle filter needs none of & | < > ^ %% ! ( ) " or a line break: write the '
                  'wildcard * in its place')
    fix_runner = 'change that runner'
    words = [(task, 'the project %r %s' % (sel.target, runner), fix_runner),
             (filt, ('the filter %r' if sel.filter else 'the default filter %r (from the file name)')
              % filt, fix_filter)]
    words += [(a, 'the "args" word %r %s' % (a, runner), fix_runner) for a in extra]
    for word, what, fix in words:
        c = cmd_unsafe(word)
        if c is not None:
            raise Usage(('refused: --tests %r: %s holds %s, which cmd.exe reads again: gradlew.bat '
                         'runs in cmd.exe, so the word could run another command; nothing was run. '
                         'Instead: ' + fix)
                        % (sel.text, what, _CMD_CHAR_NAMES.get(c, c)))
    return [task, '--offline', '--tests', filt] + extra


PLACEHOLDERS = ('{selector}', '{path}', '{stem}', '{tree}', '{log_dir}', '{n}')


def fill(word, sel, tw, log_dir, n):
    """`word` with each placeholder of a command runner replaced (PLACEHOLDERS); other braces stay."""
    values = {'{selector}': sel.filter or stem(sel.path), '{path}': sel.path,
              '{stem}': stem(sel.path), '{tree}': tw, '{log_dir}': log_dir, '{n}': str(n)}
    return re.sub('|'.join(re.escape(p) for p in PLACEHOLDERS), lambda m: values[m.group(0)], word)


def _under(tw, path):
    """`path` as it is when absolute, else under `tw`."""
    return path if os.path.isabs(path) else os.path.join(tw, *path.replace('\\', '/').split('/'))


def command_argv(sel, tw, log_dir, n):
    """The argv of a command runner's selector, or Usage. A first word with a slash that is not
    absolute is under `tw`. A .bat or .cmd program runs in cmd.exe, which reads its command line
    again, so a later word that holds one of its characters is refused (cmd_unsafe)."""
    runner = 'of the runner for %r in `.kit.json` "redfirst".runners' % sel.runner.get('glob')
    words = [fill(w, sel, tw, log_dir, n) for w in sel.runner['command']]
    words += list(sel.runner.get('args') or [])
    prog = words[0].strip()
    if not prog:
        raise Usage('usage: the first word of the command %s names no program' % runner)
    if '/' in prog or '\\' in prog:
        prog = _under(tw, prog)
    if prog.lower().endswith(('.bat', '.cmd')):
        for word in words[1:]:
            c = cmd_unsafe(word)
            if c is not None:
                raise Usage('refused: --tests %r: the word %r of the command %s holds %s, which '
                            'cmd.exe reads again: %s runs in cmd.exe, so the word could run '
                            'another command; nothing was run. Instead: change the filter or that '
                            'runner, or name a program that is not a batch file'
                            % (sel.text, word, runner, _CMD_CHAR_NAMES.get(c, c),
                               os.path.basename(prog)))
    return [prog] + words[1:]


def result_path(sel, tw, log_dir, n):
    """The file a command runner's `result` names for this selector, or None when it names none."""
    if not sel.runner.get('result'):
        return None
    return os.path.normpath(_under(tw, fill(sel.runner['result'], sel, tw, log_dir, n)))


def trx_path(sel, log_dir, n):
    return os.path.join(log_dir, '%d-%s.trx' % (n, stem(sel.path)))


def command(sel, tw, log_dir, n, python=sys.executable):
    """(argv, cwd, env extra) for one selector run in the throwaway tree `tw`. Pure."""
    if sel.kind == 'command':
        return command_argv(sel, tw, log_dir, n), tw, {}
    if sel.kind == 'gradle':
        gradlew = os.path.join(tw, 'gradlew.bat' if os.name == 'nt' else 'gradlew')
        return [gradlew] + gradle_args(sel), tw, {}
    extra = list(sel.runner.get('args') or [])
    if sel.kind == 'dotnet':
        filt = sel.filter or stem(sel.path)
        if not re.search(r'[=~!&|]', filt):
            filt = 'FullyQualifiedName~' + filt
        return (['dotnet', 'test', os.path.join(tw, *sel.target.split('/')), '--nologo',
                 '--filter', filt, '--results-directory', log_dir,
                 '--logger', 'trx;LogFileName=%s' % os.path.basename(trx_path(sel, log_dir, n))]
                + extra, tw, {})
    pp = (sel.runner.get('pythonpath') or '.').replace('\\', '/')
    name = sel.target + ('.' + sel.filter if sel.filter else '')
    return ([python, '-m', 'unittest', '-v', name] + extra, tw,
            {'PYTHONPATH': os.path.normpath(os.path.join(tw, *pp.split('/'))),
             'PYTHONDONTWRITEBYTECODE': '1'})


_ERROR_AFTER_PATH = r'(?::\d+: error:|\(\d+,\d+\): error CS\d+)'   # javac, csc


def names_copied(line, copied):
    """Whether `line` is a compiler ERROR at one of the `copied` repo-relative paths, named as a
    whole path: javac's `<path>:<n>: error:`, csc's `<path>(<l>,<c>): error CS<n>`, or a Kotlin
    line starting `e: ` with `<path>:<n>`. A warning never counts; separators compare as `/`;
    case-blind on Windows."""
    text = line.replace('\\', '/')
    flags = re.I if os.name == 'nt' else 0
    kotlin = text.lstrip().startswith('e: ')
    for p in copied:
        at = r'(?:^|[/\s])' + re.escape(p.replace('\\', '/'))
        if re.search(at + _ERROR_AFTER_PATH, text, flags):
            return True
        if kotlin and re.search(at + r':\d+', text, flags):
            return True
    return False


def compile_error_lines(log, copied):
    return [l for l in log.splitlines() if names_copied(l, copied)]


def gradle_verdict(rc, log, counts, copied):
    """(verdict, reason). `counts`: (tests, failures + errors) from this run's JUnit XML, or None."""
    if 'No tests found for given includes' in log:
        return NONE, 'no test matched the filter'
    if re.search(r'compile\w*Test\w*\s+FAILED|Execution failed for task \'[^\']*compile\w*Test', log):
        errs = compile_error_lines(log, copied)
        if errs:
            return RED, 'does not compile against the base: %s' % errs[0].strip()
        return ERROR, 'a test compile failed, but no error line names a checked-out file'
    if counts is None:
        return ERROR, 'a JUnit result file of this run could not be read (gradle exited %d)' % rc
    tests, bad = counts
    if tests and bad:
        return RED, '%d of %d test(s) failed' % (bad, tests)
    if tests and rc == 0:
        return GREEN, '%d test(s) passed' % tests
    if rc == 0:
        return NONE, 'no test ran'
    return ERROR, 'gradle exited %d with no test result' % rc


def dotnet_verdict(rc, log, counts, copied):
    """(verdict, reason). `counts`: (total, failed) from this run's .trx, or None."""
    if 'No test matches the given testcase filter' in log:
        return NONE, 'no test matched the filter'
    build_errors = [l for l in log.splitlines() if re.search(r'error CS\d+', l)]
    if build_errors:
        named = [l for l in build_errors if names_copied(l, copied)]
        if named:
            return RED, 'does not compile against the base: %s' % named[0].strip()
        return ERROR, 'the build failed, but no error names a checked-out file: %s' % build_errors[0].strip()
    if counts is None:
        return ERROR, 'dotnet exited %d and wrote no readable .trx in this run' % rc
    total, failed = counts
    if total == 0:
        return NONE, 'no test ran'
    if failed:
        return RED, '%d of %d test(s) failed' % (failed, total)
    if rc == 0:
        return GREEN, '%d test(s) passed' % total
    return ERROR, 'dotnet exited %d with every test passing' % rc


def python_verdict(rc, log):
    m = re.findall(r'^Ran (\d+) tests? in', log, re.M)
    if not m:
        return ERROR, 'unittest printed no summary (exit %d)' % rc
    ran = int(m[-1])
    if ran == 0:
        return NONE, 'no test ran'
    if rc == 0:
        return GREEN, '%d test(s) passed' % ran
    # unittest reports both a module that will not import and a filter naming nothing as one
    # errored `unittest.loader._FailedTest`; only the first is red. Matched at a line start: a
    # failing test's traceback may quote either string.
    # The name may hold stray whitespace (a CR from a CRLF selector), so it is not matched as \S+.
    if re.search(r'^ERROR: .*? \(unittest\.loader\._FailedTest\.', log, re.M):
        if re.search(r'^ImportError: Failed to import test module:', log, re.M):
            why = [l.strip() for l in re.findall(
                r'^(?:ImportError|ModuleNotFoundError): .*$', log, re.M)
                if not l.startswith('ImportError: Failed to import test module:')]
            if why:
                return RED, 'does not import against the base: %s' % why[-1]
            return RED, 'does not import against the base'
        return NONE, 'the filter names nothing in the test file'
    return RED, 'failed (%d test(s) ran)' % ran


def command_verdict(rc, log, runner, counts):
    """(verdict, reason) of a command runner. `counts`: (tests, failures + errors) from this run's
    result file, or None (absent, stale or unreadable); read only when the runner names a `result`.
    A test that is not there, or a result that never came, never reads as RED: a `no_test` match is
    NONE, a missing result ERROR, and with no result file only exit 1 is RED."""
    if runner.get('no_test') and re.search(runner['no_test'], log, re.M):
        return NONE, "the log says no test matched (the runner's no_test)"
    if runner.get('result'):
        if counts is None:
            return ERROR, 'the command exited %d and wrote no readable result file in this run' % rc
        tests, bad = counts
        if tests == 0:
            return NONE, 'no test ran'
        if bad:
            return RED, '%d of %d test(s) failed' % (bad, tests)
        if rc == 0:
            return GREEN, '%d test(s) passed' % tests
        return ERROR, 'the command exited %d with every test passing' % rc
    if rc == 0:
        return GREEN, 'the command exited 0'
    if rc == 1:
        return RED, 'the command exited 1'
    return ERROR, 'the command exited %d; only 0 (passed) and 1 (failed) give a verdict' % rc


def junit_file_counts(path, since=0.0):
    """(tests, failures + errors) of the one JUnit XML file `path`: its root's counts, else the sum
    over its testsuite elements. None when absent, older than `since`, unparseable, or a count that
    is no integer."""
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
    return tests, bad


def is_class_of(path, name):
    """Whether the Gradle JUnit file `path`, `TEST-<class>.xml`, is for the class `name` in any
    package (`<pkg>.<name>`, or bare `<name>`) or one nested in it (`<pkg>.<name>$<Inner>`)."""
    base = os.path.basename(path)
    if not (base.startswith('TEST-') and base.endswith('.xml')):
        return False
    top = base[len('TEST-'):-len('.xml')].split('$', 1)[0]
    return top == name or top.endswith('.' + name)


def junit_counts(results_dir, since=0.0, cls=None):
    """(tests, failures + errors) over the TEST-*.xml files at any depth under `results_dir`
    (Gradle writes one folder per test task) written at or after `since`; with `cls`, only the
    files of that class and its nested classes (is_class_of) count. None when a fresh file of any
    class would not parse or holds a count that is no integer: skipping it could turn a failing
    suite into a GREEN made of the others, and a broken file of another class fails closed too."""
    tests = bad = 0
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
    return tests, bad


def trx_counts(path, since=0.0):
    """(total, failed + error) from the .trx at `path`, or None: absent, older than `since`,
    unparseable, or a count that is no integer."""
    try:
        if os.path.getmtime(path) < since:
            return None
        root = ET.parse(path).getroot()
        for el in root.iter():
            if el.tag.endswith('Counters'):
                return int(el.get('total', 0)), int(el.get('failed', 0)) + int(el.get('error', 0))
    except (ET.ParseError, OSError, ValueError):
        return None
    return None


def judge(sel, rc, log_text, tw, log_dir, n, copied, since=0.0):
    if sel.kind == 'gradle':
        # Every task's folder (a module's test task may not be named `test`), but only the
        # selector's own class: `--tests` filters the named task alone, so a sibling test task
        # in the same build runs its whole suite. `since` keeps out an earlier run's XML.
        results = os.path.join(tw, *sel.runner['dir'].replace('\\', '/').split('/'),
                               'build', 'test-results')
        return gradle_verdict(rc, log_text, junit_counts(results, since, stem(sel.path)), copied)
    if sel.kind == 'command':
        result = result_path(sel, tw, log_dir, n)
        return command_verdict(rc, log_text, sel.runner,
                               junit_file_counts(result, since) if result else None)
    if sel.kind == 'dotnet':
        return dotnet_verdict(rc, log_text, trx_counts(trx_path(sel, log_dir, n), since), copied)
    return python_verdict(rc, log_text)


def exit_code(verdicts, cleaned):
    if not cleaned:
        return EXIT_LEFTOVER
    if GREEN in verdicts:
        return EXIT_GREEN
    if not verdicts or any(v != RED for v in verdicts):
        return EXIT_UNDECIDED
    return EXIT_RED
