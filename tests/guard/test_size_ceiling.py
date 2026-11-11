"""A size ceiling per kitlib/guard module, and for the guard as a whole (docs/guard-core.md "Size ceilings").

The rules it enforces: the builder size line (about 1,500 lines of non-test code per file) and the
owner's ruling of 2026-10-17 (minimal guard v2, docs/guard-core.md "Size ceilings"). The guard grew from about 14.4k
to 18.4k lines after that ruling, so growth is a deliberate edit: CEILINGS below is the one list.

Fails when
  - a module is longer than its ceiling (raise it only with a reason the review reads);
  - a module has no entry in CEILINGS (a new file names its ceiling);
  - an entry names no module (a moved or deleted file takes its entry out);
  - a ceiling is more than 5% above the module's count (SLACK_PERCENT: ceiling * 100 <= lines * 105),
    so a cut or a split lowers its ceiling in the same commit;
  - the guard's total passes TOTAL_CEILING.
Lines are len(text.splitlines()); a blank line and a comment count."""
import os
import unittest

GUARD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     'kitlib', 'guard')
SLACK_PERCENT = 5
TOTAL_CEILING = 18864
CEILINGS = {
    '__init__.py': 21,
    'backlogrole.py': 1167,
    'commands.py': 760,
    'commands_env.py': 302,
    'commands_git.py': 159,
    'commands_nested.py': 558,
    'context.py': 591,
    'delegfile.py': 579,
    'fallback.py': 580,
    'gaterole.py': 89,
    'generated.py': 76,
    'hub.py': 407,
    'jail.py': 325,
    'modes.py': 87,
    'paths.py': 154,
    'place.py': 404,
    'prepush.py': 552,
    'refusal_log.py': 169,
    'rolepolicy.py': 626,
    'rules.py': 766,
    'rules_base.py': 491,
    'rules_edit.py': 1183,
    'rules_gitcommit.py': 368,
    'rules_gitcore.py': 641,
    'rules_gitjail.py': 287,
    'rules_gitread.py': 504,
    'rules_gitref.py': 1244,
    'rules_net.py': 429,
    'rules_owner.py': 738,
    'rules_push.py': 410,
    'rules_run.py': 799,
    'rules_tools.py': 651,
    'rules_write.py': 881,
    'shell.py': 1465,
}


def counts():
    out = {}
    for name in sorted(os.listdir(GUARD)):
        if name.endswith('.py'):
            with open(os.path.join(GUARD, name), encoding='utf-8') as f:
                out[name] = len(f.read().splitlines())
    return out


class SizeCeilingTests(unittest.TestCase):
    def test_no_module_passes_its_ceiling(self):
        over = ['%s: %d lines, ceiling %d' % (n, c, CEILINGS[n])
                for n, c in counts().items() if n in CEILINGS and c > CEILINGS[n]]
        self.assertEqual(over, [], 'a guard module grew past its ceiling (tests/guard/test_size_ceiling.py)')

    def test_every_module_has_a_ceiling(self):
        self.assertEqual(sorted(set(counts()) - set(CEILINGS)), [],
                         'a guard module has no entry in CEILINGS')

    def test_every_ceiling_names_a_module(self):
        self.assertEqual(sorted(set(CEILINGS) - set(counts())), [],
                         'CEILINGS names a module that is gone')

    def test_ceilings_stay_within_the_slack(self):
        loose = ['%s: %d lines, ceiling %d' % (n, c, CEILINGS[n])
                 for n, c in counts().items() if n in CEILINGS and CEILINGS[n] * 100 > c * (100 + SLACK_PERCENT)]
        self.assertEqual(loose, [], 'lower the ceiling to within %d%% of the count' % SLACK_PERCENT)

    def test_the_guard_total_stays_under_its_ceiling(self):
        total = sum(counts().values())
        self.assertLessEqual(total, TOTAL_CEILING)
        self.assertLessEqual(TOTAL_CEILING * 100, total * (100 + SLACK_PERCENT),
                             'lower TOTAL_CEILING to within %d%% of the total' % SLACK_PERCENT)


if __name__ == '__main__':
    unittest.main()
