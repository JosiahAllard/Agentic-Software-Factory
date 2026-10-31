"""launch.py topic (kitlib/launcher/start.py topic): a plain session (no --agent) marked
PM_KIT_ROLE=topic, named topic-<slug>, started in a linked worktree with its brief. Each refusal
starts nothing. Dry runs (plan=True) and real runs on a FakeHost, so no case opens a window."""
import os
import unittest

from kitlib.git import repo as gitrepo
from kitlib.launcher import start, window
from kitlib.launcher import settings as S
from tests.launcher.fakes import role_of, session
from tests.launcher.test_start import Base


class Topic(Base):
    def setUp(self):
        Base.setUp(self)
        self.host.where = gitrepo.LINKED
        self.brief = os.path.join(self.repo, 'topic-brief.md')
        with open(self.brief, 'w', encoding='utf-8') as f:
            f.write('brief')

    def topic(self, plan=True, **kw):
        args = dict(worktree=self.repo, slug='brain-ui', brief=self.brief, account='1', plan=plan)
        args.update(kw)
        return '\n'.join(start.topic(self.h.settings, self.host, **args))

    def refused(self, msg, **kw):
        with self.assertRaises(S.LaunchError) as c:
            self.topic(**kw)
        self.assertIn(msg, str(c.exception))
        self.assertEqual(self.host.opened, [])
        return c.exception

    def test_a_dry_run_is_a_plain_session_marked_topic_and_named_topic_slug(self):
        out = self.topic()
        self.assertIn('Would open a new window running:', out)
        self.assertNotIn('--agent', out)
        self.assertEqual(role_of(out), window.TOPIC)
        self.assertIn("--remote-control 'topic-brain-ui' -n 'topic-brain-ui' ", out)
        self.assertIn('Set-Location %s' % window.q(self.repo), out)
        self.assertIn('Read your brief %s first' % self.brief, out)
        self.assertIn("stop --name 'topic-brain-ui'", out)
        self.assertEqual(self.host.opened, [])

    def test_the_first_message_pulls_with_the_session_key_and_files_no_records(self):
        """The session never reads its role file: the prompt carries the pull form and the add rule."""
        out = self.topic()
        self.assertIn('--session <key> pull <ID>', out)
        self.assertIn('Create no records: backlog.py add is refused for this session', out)
        self.assertIn('When the owner says the work is done', out)

    def test_a_real_run_opens_one_window_with_the_mark(self):
        self.topic(plan=False)
        text, = self.host.opened
        self.assertEqual(role_of(text), 'topic')
        self.assertNotIn('--agent', text)

    def test_a_relative_brief_is_read_from_the_worktree(self):
        self.assertIn('Read your brief %s first' % self.brief, self.topic(brief='topic-brief.md'))

    def test_a_main_checkout_is_refused(self):
        self.host.where = gitrepo.MAIN
        self.refused('is not a linked worktree (a main checkout)')

    def test_a_folder_git_cannot_place_is_refused(self):
        self.host.where = gitrepo.UNKNOWN
        self.refused('is not a linked worktree')

    def test_a_missing_folder_is_refused(self):
        self.refused('No folder at', worktree=os.path.join(self.repo, 'no-such'))

    def test_a_missing_brief_is_refused(self):
        self.refused('No brief file at', brief=os.path.join(self.repo, 'missing.md'))
        self.refused('Give --brief', brief=None)

    def test_a_bad_slug_is_refused(self):
        self.refused("--slug 'X'", slug='X')

    def test_a_live_topic_session_of_the_name_is_refused(self):
        self.host.procs = [session(4242, 'topic-brain-ui', agent='x')]
        self.refused("A session named 'topic-brain-ui' is already running (PID 4242)")


if __name__ == '__main__':
    unittest.main()
