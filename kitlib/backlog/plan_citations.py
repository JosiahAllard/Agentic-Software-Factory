"""The `uncite` plan: drop the owner ruling and question ids a record's body and header cite
(plan_uncite). kitlib/backlog/plan.py has the shared Context and checks.
"""
import re

from . import record as rmod
from .plan import _text
from .store import Change


# --- uncite ---------------------------------------------------------------------------------------

# An owner ruling or question id as a record body cites it: `R-` and four or more digits, `Q-` and
# three or more, not glued to a word after it. `T-` ids, `item:` members and an `R-` id of fewer
# than four digits are not ones.
CITE_ID = r'(?:R-[0-9]{4,}|Q-[0-9]{3,})(?![A-Za-z0-9_])'
CITED = re.compile(r'(?<![A-Za-z0-9-])' + CITE_ID)
# One glued to a word before it by a hyphen (`pre-R-nnnn`): no rule reads it, so its line is left
# for a hand edit.
GLUED = re.compile(r'(?<=[A-Za-z0-9]-)' + CITE_ID)
# The header keys `uncite` drops: each holds only a cited id. `resume`, `q` and `priority_per`
# are kept (offer ranking and the park read them).
UNCITED_KEYS = ('priority_ruling', 'hold_ruling')
# One cite as a sentence names it: a `PM ` or `owner ` before it and a `(c)` part after it are its.
_CITE = r'(?:(?:PM|[Oo]wner) )?' + CITE_ID + r'(?:\([a-z0-9]{1,3}\))?'
_RUN = r'(?:, | and |/|; | & )'
# One cite or a run of them (`R-nnnn, R-nnnn and Q-nnn`), never glued to a word before it.
_G = r'(?<![A-Za-z0-9-])' + _CITE + r'(?:' + _RUN + _CITE + r')*'
# Inside a parenthesis a cite may name the part of it meant (`O2`, `Q2`, `b`).
_PART = r'(?: (?:[A-Z][0-9]{1,2}[a-z]?|[a-z]))?'
_GP = r'(?<![A-Za-z0-9-])' + _CITE + _PART + r'(?:' + _RUN + _CITE + _PART + r')*'
# A key or lead word before a cite that goes with it (`value_ruling R-nnnn`, `ref: R-nnnn`).
_KEY = r'(?:(?:[a-z][a-z0-9]*_[a-z0-9_]+|[Rr]ef|[Pp]er|[Ss]ee|PM|[Oo]wner):? )?'
_BULLET = r'\s*(?:[-*] |[0-9]+\. )?'
# A cite (with a part it names) that ends the text before a tag rule's mark: the mark joins a run
# of cites (`by R-nnnn, R-nnnn`), which is no tag, so the run stays whole for the object rule.
_CITE_END = re.compile(CITE_ID + r'(?:\([a-z0-9]{1,3}\))?' + _PART + '$')
# The mark after a leading cite list that goes with it: not when the words after it join on to
# the cites (`(R-nnnn, and the rule)`, `R-nnnn, which says`), which without them reads wrong.
# After a colon `which` and `that` start the text the label names (`Q-nnn: which one?`).
_JOINS = r'(?!(?:and|or|but|nor|plus|then|with)\b)'
_LIST_MARK = r'(?:[:;]|,(?!\s+(?:which|that)\b))\s+' + _JOINS
# The words a cite can follow as their object (`per R-nnnn`, `answered by R-nnnn`).
_PREP = (r'(?:\b(?:[Pp]er|[Uu]nder|[Ii]n|[Bb]y|[Ff]rom|[Ss]ince|[Aa]fter|[Bb]efore|[Uu]ntil|'
         r'[Ff]ollowing|[Ss]ee|[Cc]iting|[Cc]ites|[Oo]n|[Oo]f|[Ww]ith|[Aa]gainst|[Bb]reaks?|'
         r'[Oo]verrides?|[Rr]everses|[Cc]ontradicts|[Rr]espects|[Ff]ollows) )')
# What may follow a cite as an object: punctuation, the line's end, a parenthesis, a joining word.
_AFTER = (r'(?=[.,;:)!?]|$| \(| (?:and|or|but|so|then|which|that|to|for|when|while|because|as|'
          r'until|unless|if|before|after)\b)')
# The words a question id can follow as an object and still read as the question (`waiting on
# Q-nnn`, `asked in Q-nnn`). After any other (`done per Q-nnn`) the id stands for its answer, which
# `an owner question` would not say: that line is left for a hand edit.
_Q_PREPS = ('on', 'in', 'of', 'see', 'citing', 'cites')
_NOUN = r'(?:ruling|question|answer|decision)s?\b'
# What may stand before `ruling`, `question` and the like for the noun rule to drop the cite after
# it: an article, `this`/`that`/`its`, or a possessive, with an `owner` or `PM` between. A bare
# `Owner ruling R-nnnn` has none: dropping the cite would leave `Owner ruling wants`.
_DET = r"(?:The|the|[Aa]n?|[Tt]hat|This|this|[Ii]ts|[Oo]wner's|PM's)"
# What a rewrite can leave that reads wrong: an empty or space-led parenthesis, one opened at the
# line's end, a space before punctuation, a doubled comma or space.
_ODD = re.compile(r'\(\s*\)|\( |\($|\s[,;.]|,,|\S  \S')


def _opens_at(s, i, opens):
    """Whether position `i` of line `s` starts a sentence: the line's start (after a bullet) when
    the line `opens` one, or just after `.`, `!` or `?` and a space."""
    before = s[:i]
    if re.fullmatch(_BULLET, before):
        return opens
    return re.search(r'[.!?]\s+$', before) is not None


def _words(cites, alone):
    """The words for the cite run `cites`: `an owner ruling` / `an owner question` for one (`an
    owner` when `alone` is False: a noun follows), `owner rulings` / `owner questions` for more
    of one kind (`owner`), None for a run that mixes rulings and questions."""
    ids = CITED.findall(cites)
    kinds = {i[0] for i in ids}
    if len(kinds) != 1:
        return None
    noun = 'question' if kinds == {'Q'} else 'ruling'
    if len(ids) == 1:
        return 'an owner %s' % noun if alone else 'an owner'
    return 'owner %ss' % noun if alone else 'owner'


def _uncite_rules(opens, state):
    """The rewrites in the order they run, each `(pattern, replacement)`. The first ones drop a
    cite that is a tag on the sentence (in a parenthesis, after a comma, a label before a colon,
    one cite after a noun with an article that already says it); the rest put `an owner ruling` /
    `an owner question` where the cite is a word of the sentence: an object (`per R-nnnn`), before
    a noun after a preposition (`under the R-nnnn ruling`), or a dated label of rulings opening a
    line. No rule
    words a cite that is a subject (`R-nnnn says`) or a possessive (`R-nnnn's`): `an owner
    ruling` there would say some ruling where the line meant one. A tag rule whose mark follows a
    cite leaves the run (`by R-nnnn, R-nnnn`) for the object rule, which keeps it plural; one whose
    list would leave a joining word first (`(R-nnnn, and the rule)`) does not run. A cite no rule
    places, a run a rule cannot word, or a word rule at the start of a line that continues a
    sentence, sets state['hand']: uncite_line then leaves the line as it was. state['words']
    counts the word rules that ran."""
    def hand(m):
        state['hand'] = True
        return m.group(0)

    def tag(m):
        # a mark after a cite joins a run of them: no tag, left whole for the object rule
        return m.group(0) if _CITE_END.search(m.string[:m.start()]) else ''

    def continued(m):
        # the line's first words continue the line before's sentence: no rule knows the word
        # before the cite
        return not opens and m.start('pre') == 0 and re.fullmatch(_BULLET, m.group('pre'))

    def prefix(m):
        if not m.group(2) and not opens:
            return hand(m)
        nxt = m.group(3)
        if not m.group(2):
            nxt = nxt.upper()
        return m.group(1) + m.group(2) + nxt

    def noun(m):
        # one cite after the noun: more (`the ruling R-nnnn and Q-nnn say`) would leave the
        # noun saying one where the line named several
        return hand(m) if len(CITED.findall(m.group(2))) > 1 else m.group(1)

    def word(alone=True, suffix='', obj=False, rulings=False):
        def rep(m):
            text = _words(m.group('c'), alone)
            q_word = 'question' in (text or '')
            if text is None or continued(m) or (rulings and q_word) or (
                    obj and q_word and m.group('pre').strip().lower() not in _Q_PREPS):
                return hand(m)
            state['words'] += 1
            start = m.end('pre')
            if _opens_at(m.string, start, opens):
                text = text[:1].upper() + text[1:]
            return m.group('pre') + text + suffix
        return rep

    return [
        # a parenthesis holding only cites (a lead word and parts with them): gone, with the
        # space before it (never the line's indent)
        (r'(?:(?<=\S)\s+)?\(' + _KEY + _GP + r'\)', ''),
        # a sentence that only points at cites: `See R-nnnn.`
        (r'^(' + _BULLET + r')[Ss]ee ' + _G + r'\.\s*', r'\1'),
        (r'(?<=[.!?]) [Ss]ee ' + _G + r'\.', ''),
        # `(CITES: rest`, `rest; CITES)`, `rest, CITES, more`
        (r'\(' + _KEY + _GP + _LIST_MARK, '('),
        (r'[,;]\s+' + _KEY + _GP + r'(?=\))', tag),
        (r'[,;]\s+' + _KEY + _G + r'(?=[,;]\s)', tag),
        # a trailing `, CITES` (or `, key CITES`) before the line's end, a full stop or a
        # parenthesis
        (r'\s*[,;|]\s*' + _KEY + _G + r'(?=\s*(?:\.|\(|$))', tag),
        # a leading `CITES: ` or `CITES, ` (after a bullet and a short `Note: ` style label)
        (r'^(' + _BULLET + r')((?:[A-Za-z][A-Za-z _-]{0,20}: )?)' + _G + r'(?!;)' + _LIST_MARK +
         r'(\S?)', prefix),
        # `the ruling CITES`, `an owner question CITES`: the noun already says it
        (r'\b(' + _DET + r" (?:(?:[Oo]wner|PM)(?:'s)? )?" + _NOUN + r') (' + _G + r')', noun),
        # `R-nnnn (2026-10-07): ...` opening a line; a `Q-nnn (date):` label names the answer,
        # which `an owner question` would not say
        (r'^(?P<pre>' + _BULLET + r')(?P<c>' + _G + r')(?= \([0-9]{4}-[0-9]{2}-[0-9]{2}\):)',
         word(rulings=True)),
        # `under the R-nnnn ruling`
        (r'(?P<pre>' + _PREP + r')(?:(?:The|the) )?(?P<c>' + _G + r') (?=' + _NOUN + r')',
         word(alone=False, suffix=' ')),
        # `per R-nnnn`, `answered by R-nnnn`
        (r'(?P<pre>' + _PREP + r')(?P<c>' + _G + r')' + _AFTER, word(obj=True)),
    ]


def uncite_line(line, opens=True):
    """`(after, hand)`: one body line with each owner ruling or question id (CITE_ID) rewritten
    into plain words (_uncite_rules), or the line as it was and `hand` True where no rule words it
    cleanly: a cite no rule places, one glued to a word by a hyphen (GLUED), more than one cite
    put into words in the line (distinct rulings would read as one), or odd punctuation (_ODD) or
    indentation the rewrite left. Such a line is listed for a hand edit and never rewritten: no
    rewrite says `the owner` or `the owner's`. `opens`: the line starts a sentence (the line before
    it is blank, absent, or ends a sentence). A line left opening on a mark (`, then x`, after a
    parenthesis went) is the body's to join (_uncite_body). A `Blocked on M (date): T` line
    (record.BLOCK_NOTE, written by plan_block) has only its reason T worded, as a line that opens
    a sentence; one whose reason would be left empty or opening on a mark, or that has no such
    prefix, is left for a hand edit. Idempotent: a line with no cite comes back as it was."""
    if not (CITED.search(line) or GLUED.search(line)):
        return line, False
    if line.startswith(rmod.BLOCK_NOTE):
        head = _BLOCK_HEAD.match(line)
        if not head:
            return line, True
        reason, hand = _uncite_text(line[head.end():], True)
        if hand or not reason.strip() or re.match(r'[,;.:]', reason):
            return line, True
        return head.group(0) + reason, False
    return _uncite_text(line, opens)


# The prefix of the body line plan_block writes for `--reason`: `Blocked on M (yyyy-mm-dd): `.
_BLOCK_HEAD = re.compile(re.escape(rmod.BLOCK_NOTE) + r'[^()]*\([0-9]{4}-[0-9]{2}-[0-9]{2}\): ')


def _uncite_text(line, opens):
    """uncite_line for one line of text that is not a `Blocked on` line: a body line, or a
    `summary` or `ref` value (plan_uncite)."""
    if not (CITED.search(line) or GLUED.search(line)):
        return line, False
    if GLUED.search(line):
        return line, True
    opens = opens or re.match(r'\s*(?:[-*]|[0-9]+\.) ', line) is not None
    state = {'hand': False, 'words': 0}
    after = line
    for pattern, rep in _uncite_rules(opens, state):
        after = re.sub(pattern, rep, after)
    if re.fullmatch(r'\s*(?:[-*]|[0-9]+\.)?\s*', after):
        return '', False                     # only cites (and a bullet): the line goes
    lead = re.match(r'\s*', line).group(0)
    joined = re.sub(r'^(\s*)[,;.:]\s*', r'\1', after)    # a leading mark is the body's to join
    if (state['hand'] or state['words'] > 1 or CITED.search(after) or
            len(_ODD.findall(joined)) > len(_ODD.findall(line)) or
            (after.strip() and re.match(r'\s*', after).group(0) != lead)):
        return line, True
    return after, False


def _uncite_body(body, keep_block=False):
    """`(new body, rewritten, hand)`: `rewritten` the `[before, after]` of each line the rewrite
    changed, in order (`after` None for a line that was only cites and goes); `hand` the
    `(n, line)` of each line uncite_line left for a hand edit, `n` its 1-based place in the new
    body. `keep_block`: each `Blocked on` line that cites one is left for a hand edit unworded
    (it is a hold's only citation: plan_uncite). A rewritten line left opening on a stray mark (`, not this one.` once a parenthesis of
    cites went) gives the mark to the line above it, or drops it where that line is blank, a
    heading or already ends on a mark, capitalising what is left after a blank line, a heading or
    a finished sentence; it is left for a hand edit when the line above is one."""
    out, rewritten, hand = [], [], []
    at = {}                                  # out index -> its rewritten entry
    prev = ''
    for line in (body or '').split('\n'):
        opens = not prev.strip() or prev.rstrip()[-1:] in '.!?:'
        prev = line
        if keep_block and line.startswith(rmod.BLOCK_NOTE) and CITED.search(line):
            after, needs = line, True
        else:
            after, needs = uncite_line(line, opens)
        stray = re.match(r'(\s*)([,;.:])\s*', after)
        if stray and not needs and after != line and not re.match(r'\s*[,;.:]', line):
            above = out[-1] if out else ''
            if hand and hand[-1][0] == len(out):
                needs = True
            else:
                rest = after[stray.end():]
                if above.strip() and above.rstrip()[-1:] not in '.,;:!?' and \
                        not above.lstrip().startswith('#'):
                    out[-1] = above.rstrip() + stray.group(2)
                    if len(out) - 1 in at:
                        at[len(out) - 1][1] = out[-1]
                    else:
                        at[len(out) - 1] = [above, out[-1]]
                        rewritten.append(at[len(out) - 1])
                elif (not above.strip() or above.rstrip()[-1:] in '.!?' or
                      above.lstrip().startswith('#')):
                    # the line starts a sentence (after a blank line, a finished one or a
                    # heading): it opens with a capital
                    rest = rest[:1].upper() + rest[1:]
                after = stray.group(1) + rest if rest else ''
        if needs:
            out.append(line)
            hand.append((len(out), line))
        elif after != line and not after.strip():
            rewritten.append([line, None])
        elif after != line:
            out.append(after)
            at[len(out) - 1] = [line, after]
            rewritten.append(at[len(out) - 1])
        else:
            out.append(line)
    return '\n'.join(out), rewritten, hand


UNCITED_FIELDS = ('summary', 'ref')     # the header values uncite words as it words a body line


def _uncite_fields(pairs):
    """`(pairs, worded, hand)` for one record's header pairs: each UNCITED_FIELDS value that cites
    one worded as a line that opens a sentence (_uncite_text). `worded` the `(key, before,
    after)` of each value changed, `after` None for a `ref` of only cites, which is dropped;
    `hand` the `(n, key, value)` of each value kept for a hand edit, `n` its line in the written
    record: one no rule words cleanly, a `summary` of only cites (it is required) and a value
    left opening on a mark."""
    out, worded, hand = [], [], []
    for key, value in pairs:
        if key in UNCITED_FIELDS and (CITED.search(value) or GLUED.search(value)):
            after, needs = _uncite_text(value, True)
            if key == 'ref' and re.fullmatch(r'\s*' + _KEY + _GP + r'\s*\.?\s*', value):
                after, needs = '', False         # only cites: no rule takes a bare one
            if not needs and not after.strip() and key == 'ref':
                worded.append((key, value, None))
                continue
            if needs or not after.strip() or re.match(r'\s*[,;.:]', after):
                hand.append((len(out) + 1, key, value))
            else:
                worded.append((key, value, after.strip()))
                value = after.strip()
        out.append((key, value))
    return out, worded, hand


def _hold_needs_block_lines(rec, body):
    """Whether `rec` is a blocked record with a `deliberate` or `owner` hold whose citations
    (record.hold_citations) would all be gone with its `Blocked on` lines worded as in `body`:
    nothing in `q`, `owner_ask` or `blocked_on` cites one, and no `Blocked on` line of `body`
    does. Its `Blocked on` lines that cite one are then kept for a hand edit, so `check`'s hold
    citations still read the hold."""
    if rec.get('state') != 'blocked' or not any(
            m in rmod.UNCITED_KINDS for m in rec.all('blocked_on')):
        return False
    if any(rmod.cites(v) for key in ('q', 'owner_ask', 'blocked_on') for v in rec.all(key)):
        return False
    return not any(rmod.cites(line) for line in (body or '').split('\n')
                   if line.startswith(rmod.BLOCK_NOTE))


def plan_uncite(snap, ctx):
    """Rewrite every owner ruling and question id (CITE_ID) cited in the readable records' bodies,
    their `Blocked on` lines' reasons among them, and in their `summary` and `ref` values
    (UNCITED_FIELDS) into plain words (uncite_line), and drop the header keys that hold only one
    (UNCITED_KEYS), in one store commit. `owner_ask`, `q`, `resume`, `priority_per` and
    `requires` are kept as they are, and so is a `Blocked on` line that is a deliberate or owner
    hold's only citation (_hold_needs_block_lines). A line or value no rule words cleanly is left
    as it was and listed under NEEDS HAND EDIT, with the store, the record and its line in the
    written record, in the dry run and in the `--apply` output alike. A second run rewrites
    nothing; it lists the lines still left for a hand edit."""
    writes, out, hands = {}, [], []
    cites = body_lines = fields = headers = 0
    for rec in snap.readable:
        body, rewritten, hand = _uncite_body(rec.body)
        only = _hold_needs_block_lines(rec, body)
        if only:
            body, rewritten, hand = _uncite_body(rec.body, keep_block=True)
        drop = [(k, rec.get(k)) for k in UNCITED_KEYS if rec.has(k)]
        pairs, worded, field_hand = _uncite_fields(
            [(k, v) for k, v in rec.pairs() if k not in UNCITED_KEYS])
        hands += ['  %s %s line %d (%s): %s' % (snap.where, rec.stem, n, key, value.strip())
                  for n, key, value in field_hand]
        # the header, its blank line, then the body: line n of the body is line len(pairs)+1+n
        hands += ['  %s %s line %d: %s%s' % (
            snap.where, rec.stem, len(pairs) + 1 + n, line.strip(),
            " (the hold's only citation)" if only and line.startswith(rmod.BLOCK_NOTE) else '')
            for n, line in hand]
        if not rewritten and not drop and not worded:
            continue
        writes[rec.stem] = _text(pairs, body if rewritten else rec.body)
        out.append('  %s' % rec.stem)
        for key, before, after in worded:
            cites += len(CITED.findall(before))
            out += ['    - %s: %s' % (key, before),
                    '    + %s: %s' % (key, after if after is not None else '(dropped)')]
        fields += len(worded)
        for before, after in rewritten:
            cites += len(CITED.findall(before))
            out += ['    - %s' % before.strip(),
                    '    + %s' % (after.strip() if after is not None else '(line removed)')]
        for key, val in drop:
            out.append('    dropped %s: %s' % (key, val))
        body_lines += len(rewritten)
        headers += len(drop)
    tail = ['NEEDS HAND EDIT (%d line(s) left unchanged: no rule words their cites cleanly; edit '
            'them by hand)' % len(hands)] + hands if hands else []
    if not writes:
        return Change({}, 'backlog: uncite', ['UNCITE: nothing to rewrite in %s%s' % (
            snap.where, '; %d line(s) still cite one' % len(hands) if hands else
            ': no owner ruling or question id is cited')] + tail)
    lines = ['UNCITE: %d cite(s) in %d record(s): %d body line(s) rewritten, %d field(s) '
             'rewritten, %d header line(s) dropped, %d line(s) left for a hand edit'
             % (cites, len(writes), body_lines, fields, headers, len(hands))] + out + tail
    return Change(writes, 'backlog: uncite %d cite(s) in %d record(s)' % (cites, len(writes)),
                  lines)
