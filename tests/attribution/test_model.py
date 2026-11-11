"""The co-author trailer, the model a commit names and the refusal when none is named (kitlib.attribution)."""
import unittest

from kitlib import attribution


class CoAuthorTrailer(unittest.TestCase):

    def test_a_bare_model_name_gets_claude(self):
        self.assertEqual(attribution.coauthor('Opus 5.5'),
                         'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')

    def test_a_name_that_already_says_claude_is_not_doubled(self):
        self.assertEqual(attribution.coauthor('Claude Opus 5.5'),
                         'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')
        self.assertEqual(attribution.coauthor('Claude'), 'Co-Authored-By: Claude <noreply@anthropic.com>')

    def test_a_lower_case_model_id_is_written_as_its_display_name(self):
        for model, name in (('claude-opus-5-5', 'Opus 5.5'), ('claude-sonnet-5-5', 'Sonnet 5.5'),
                            ('claude-haiku-4-5', 'Haiku 4.5'), ('claude-opus-5', 'Opus 5'),
                            (' claude-opus-5-5 ', 'Opus 5.5')):
            self.assertEqual(attribution.coauthor(model),
                             'Co-Authored-By: Claude %s <noreply@anthropic.com>' % name)

    def test_display_names_and_other_ids_are_left_as_they_are(self):
        for model in ('Claude Opus 5.5', 'Opus 5.5', 'Claude-Opus-5-5', 'claude-opus',
                      'claude-opus-5-5-20260101'):
            self.assertEqual(attribution.display_name(model), model)
        self.assertEqual(attribution.coauthor('claude-opus'),
                         'Co-Authored-By: Claude claude-opus <noreply@anthropic.com>')

    def test_whitespace_is_collapsed(self):
        self.assertEqual(attribution.coauthor('  Opus   5.5 '),
                         'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')

    def test_an_empty_model_is_refused(self):
        for blank in ('', '   ', None):
            with self.assertRaises(ValueError):
                attribution.coauthor(blank)

    def test_a_listed_family_is_kept_as_given(self):
        for name in ('Codex', 'Codex GPT-5', 'Codex-mini', 'Codex.2'):
            self.assertEqual(attribution.coauthor(name, None, ['Codex']),
                             'Co-Authored-By: %s <noreply@anthropic.com>' % name)
        self.assertEqual(attribution.coauthor('GPT-5.1', None, ['GPT-5']),
                         'Co-Authored-By: GPT-5.1 <noreply@anthropic.com>')

    def test_a_family_followed_by_anything_else_still_names_claude(self):
        for name, fams in (('Codexer 1', ['Codex']), ('Codex_2', ['Codex']), ('GPT-50', ['GPT-5'])):
            self.assertEqual(attribution.coauthor(name, None, fams),
                             'Co-Authored-By: Claude %s <noreply@anthropic.com>' % name)


class ModelResolution(unittest.TestCase):

    def test_the_flag(self):
        self.assertEqual(attribution.resolve_model(False, 'M', {}), ('M', '--model', None))

    def test_the_environment_variable_is_the_fallback(self):
        self.assertEqual(attribution.resolve_model(False, None, {attribution.MODEL_ENV: ' E '}), ('E', attribution.MODEL_ENV, None))

    def test_the_flag_wins_over_the_environment(self):
        self.assertEqual(attribution.resolve_model(False, 'F', {attribution.MODEL_ENV: 'E'})[0], 'F')

    def test_blank_everywhere_is_an_error(self):
        _m, _s, err = attribution.resolve_model(False, '  ', {attribution.MODEL_ENV: '  '})
        self.assertIn('--model is required', err)

    def test_owner_authored_takes_no_model(self):
        self.assertEqual(attribution.resolve_model(True, None, {attribution.MODEL_ENV: 'E'}), (None, None, None))
        self.assertIn('contradict', attribution.resolve_model(True, 'F', {})[2])


class MissingModel(unittest.TestCase):

    def test_the_missing_model_refusal_names_only_the_chosen_hosts_variable(self):
        """A host with no model variable (codex) was told to set the default
        host's, another host's environment (skill builder: never infer the model from it)."""
        claude = attribution.missing_model('claude')
        self.assertIn('--model "<model name>"', claude)
        self.assertIn('(or set CLAUDE_AGENT_MODEL for this session only)', claude)
        codex = attribution.missing_model('codex')
        self.assertIn('--model "<model name>"', codex)
        self.assertNotIn('CLAUDE_AGENT_MODEL', codex)
        self.assertEqual(attribution.resolve_model(False, None, {}, 'claude')[2], claude)
