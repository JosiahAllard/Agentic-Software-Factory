"""CLAUDE-SPECIFIC: the models a subagent ran on, read from its transcript (tools/verdict.py add
checks a verdict's --reviewer-model with them through ADAPTER.agent_models(), hosts.claude.adapter.
host: kitlib.gate.verdicts.model_mismatch_why).

Claude Code writes each subagent's transcript to
`<config folder>/projects/<project>/<parent session id>/subagents/agent-<agent id>.jsonl`, one
JSON record a line; each assistant turn is a record `{"type": "assistant", "message": {"model":
"claude-haiku-5-5", ...}}`. An agent id is `a` and 16 hex digits (AGENT_ID_RE: every one of the
12436 subagent transcripts on the kit machine on 2026-10-30). Observed there: a pm-kit:analyst
dispatched with no model override ran on claude-haiku-5-5, its role's light tier.

The config folders searched: CLAUDE_CONFIG_DIR, then each `~/.claude*` folder
(claude_host.project_roots), a folder reached twice counted once. Only the model strings are
read: a file is read a line at a time, only a line holding `"assistant"` is parsed, and a file is
read no further once it shows MAX_MODELS distinct models. A host's task `.output` file is never
read. Nothing here prints or writes.

verdict() reads the same transcripts for the reviewer's final `Verdict:` line (tools/verdict.py
add, through ADAPTER.agent_verdict(): kitlib.gate.verdicts.reviewer_verdict_why). A subagent's
final report is the `message` of its SubagentHandback call, a tool_use block of an assistant
record (observed in the kit machine's transcripts on 2026-10-31), or, where it has no such tool,
the text of its last assistant turn.
"""
import glob
import json
import os
import re

from hosts.claude.adapter import claude_host

AGENT_ID_RE = re.compile(r'^a[0-9a-f]{16}$')
SKIP = frozenset(['<synthetic>'])     # a turn Claude Code made itself, no model ran
MAX_MODELS = 2                        # more than one model already says the reviewer was not one
HANDBACK = 'SubagentHandback'         # the tool a subagent hands its final report back with
# A report's verdict line: `Verdict: SEND BACK`, markdown decoration (`**Verdict:** LAND`, a list
# dash) allowed, anchored at the line start; LAND WITH FOLLOW-UP is tried before LAND.
VERDICT_RE = re.compile(r'^[\s>*_#-]*verdict[*_]*\s*:[*_\s]*'
                        r'(land\s+with\s+follow[- ]?up|land|send\s+back)(?![\w-])', re.IGNORECASE)


def is_agent_id(text):
    """True when `text` is a Claude subagent id (AGENT_ID_RE). Pure."""
    return isinstance(text, str) and bool(AGENT_ID_RE.match(text))


def projects_dirs(environ=None, home=None):
    """The `projects` folder of CLAUDE_CONFIG_DIR (in `environ`, default os.environ), then of each
    `~/.claude*` folder under `home` (default the user's), each real folder once."""
    environ = os.environ if environ is None else environ
    out, seen = [], set()
    first = (environ.get('CLAUDE_CONFIG_DIR') or '').strip()
    try:
        roots = claude_host.project_roots(home)
    except OSError:                    # no home folder to list
        roots = []
    for d in ([os.path.join(first, 'projects')] if first else []) + roots:
        real = os.path.normcase(os.path.realpath(d))
        if real not in seen and os.path.isdir(d):
            seen.add(real)
            out.append(d)
    return out


def transcripts(agent_id, environ=None, home=None):
    """Every transcript file of the subagent `agent_id` (an agent id: is_agent_id), each real file
    once, in folder order."""
    out, seen = [], set()
    for d in projects_dirs(environ, home):
        for p in sorted(glob.glob(os.path.join(d, '*', '*', 'subagents', 'agent-%s.jsonl' % agent_id))):
            real = os.path.normcase(os.path.realpath(p))
            if real not in seen:
                seen.add(real)
                out.append(p)
    return out


def file_models(path):
    """The distinct models the assistant records of the transcript `path` name, at most MAX_MODELS
    (the read stops there), SKIP left out; None when the file cannot be read."""
    found = set()
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                if '"assistant"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get('type') != 'assistant':
                    continue
                msg = rec.get('message')
                if not isinstance(msg, dict):
                    continue
                model = msg.get('model')
                if isinstance(model, str) and model.strip() and model not in SKIP:
                    found.add(model.strip())
                    if len(found) >= MAX_MODELS:
                        break
    except OSError:
        return None
    return found


def report_verdict(text):
    """The verdict the last line of `text` matching VERDICT_RE names, spelled LAND, LAND WITH
    FOLLOW-UP or SEND BACK whatever its case, or None when no line does. Pure."""
    found = None
    for line in (text or '').splitlines():
        m = VERDICT_RE.match(line)
        if m:
            said = m.group(1).upper()
            found = ('LAND WITH FOLLOW-UP' if said.startswith('LAND WITH')
                     else 'SEND BACK' if said.startswith('SEND') else 'LAND')
    return found


def _report_text(rec):
    """The report text an assistant record carries: its text blocks, or the message of its
    SubagentHandback call (how a subagent hands its final report back); None when it carries
    neither (a tool call, thinking)."""
    msg = rec.get('message')
    content = msg.get('content') if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content if content.strip() else None
    if not isinstance(content, list):
        return None
    parts = []
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get('type') == 'text' and isinstance(b.get('text'), str):
            parts.append(b['text'])
        elif (b.get('type') == 'tool_use' and b.get('name') == HANDBACK
              and isinstance(b.get('input'), dict) and isinstance(b['input'].get('message'), str)):
            parts.append(b['input']['message'])
    text = '\n'.join(parts)
    return text if text.strip() else None


def file_final_report(path):
    """The report text of the last assistant record of the transcript `path` that carries one
    (_report_text): '' when none does; None when the file cannot be read."""
    last = ''
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                if '"assistant"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get('type') != 'assistant':
                    continue
                text = _report_text(rec)
                if text is not None:
                    last = text
    except OSError:
        return None
    return last


def verdict(agent_id, environ=None, home=None):
    """What the subagent `agent_id` said in its final report: `(found, verdict)`, `found` True when
    a readable transcript of it was found, `verdict` the final report's last `Verdict:` line
    (report_verdict) or None. The final report is the last assistant turn carrying text or a
    SubagentHandback call, in the transcript last written when there is more than one (a resumed
    subagent appends to its own). None when `agent_id` is not a Claude agent id."""
    rid = agent_id.strip() if isinstance(agent_id, str) else agent_id
    if not is_agent_id(rid):
        return None
    best = None
    for p in transcripts(rid, environ, home):
        try:
            when = os.path.getmtime(p)
        except OSError:
            continue
        text = file_final_report(p)
        if text is not None and (best is None or when >= best[0]):
            best = (when, text)
    if best is None:
        return (False, None)
    return (True, report_verdict(best[1]))


def models(agent_id, environ=None, home=None):
    """The models the subagent `agent_id` ran on, sorted: the union over every transcript of it
    found (transcripts) that can be read. None when `agent_id` is not a Claude agent id (a session
    name, say): nothing to look for. [] when it is one and no readable transcript names a model."""
    rid = agent_id.strip() if isinstance(agent_id, str) else agent_id
    if not is_agent_id(rid):
        return None
    found = set()
    for p in transcripts(rid, environ, home):
        found |= file_models(p) or set()
    return sorted(found)
