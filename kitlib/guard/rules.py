"""The guard's rules (ARCHITECTURE.md "Guard rules"; docs/guard.md "Rules").

decide(action, ctx) -> kitlib.host.Decision ('allow' when no rule refuses). `action` is the neutral
kitlib.host.Action a host adapter parsed its hook payload into (hosts/claude/adapter/events.py for
Claude Code); no rule here reads a host's payload. Every rule is a read-only check of real state and
every deny names the exact command to use instead (also in Decision.instead). Each rule below
takes the Action as its first argument, named `event` from before the split; a rule answers None
for an allow, and decide() turns that into Decision('allow'). A deny reason ends with the tag
`[kit guard: rule=<rule> role=<role>]`; hosts.claude.adapter.guard_selftest reads those tags back out of
subagent transcripts.

Rules, in the order they are checked:
  lock-mcp         the lock MCP's owner-only tools: a subagent or a launcher-marked session is
                   denied; an unmarked main session (the owner's own) is allowed
  hub-mcp          the hub MCP server's owner tools (hub_answer, hub_todo_done, hub_story_submit,
                   hub_to_pm_send, a hub tool named for decide or approve), in any session but the
                   owner's own
  hub-browser      a browser or computer-use MCP call whose input names the hub's own origin (a
                   loopback host on a hub port), in any session but the owner's own
  owner-tool       a shell command running one of the owner's tools to act (tool_run reads the file
                   it runs): reserve_ui; a lock CLI owner-only write (reserve, release, hold
                   whole|half|slots, division --set, game on|off|set, top, stop/unbook --force);
                   machine setup with --apply (its PowerShell front ends with -Apply), which
                   writes the owner's email; in any session but the owner's own
  hub-secrets      a shell command with a literal path into the hub's token folder (a bare token
                   file's name, a hub-like name elsewhere, a computed word, the word hub and the
                   store are not refused; a wildcard path is read as the literal folder before
                   its first wildcard, glob_folder, so `cat ~/.pm-kit/hub/*` is), or python
                   or node code that names a token file or the folder (kitlib.guard.hub.INLINE_RE);
                   an edit of a file in the token folder or the store; a read tool's
                   read of the token folder or a file in it, or a Grep that can read one (Glob
                   is not hooked; the store stays readable); in any session but the owner's own
                   (kitlib.guard.hub)
  hub-owner-verb   a parsed hub tool run (hub_run_args: python, its options, then tools/hub.py or
                   -m tools.hub; tools/hub.py run as the program; python -m runpy tools.hub; at
                   any depth of the line) whose arguments run an owner verb
                   (answer, open, pair, unpair, stop, restart, todos done, stories add|withdraw,
                   to-pm add|send, inbox read, approvals approve|deny, todos add --owner; a word
                   the guard cannot read where the verb could be counts), in any session but the
                   owner's own. Text that only names the hub tool (prose, a pipe of a run into
                   another program) is not read
  ledger          an edit of the machine config's ledger_path, of the rulebooks' folder or a
                   `.md` file directly in it, or of the standing digest (kitlib.ledger.files
                   of_config: `rules` and STANDING.md in ledger_dir, else beside ledger_path), by
                   anyone; with a broken machine config, every subagent write
  lock-state       an agent session's edit of a file in the machine lock's state folder
  machine-config   an edit of a file in the PM repository's machine config folder
                   (<pointer>/.pm-kit) or in a .pm-kit folder at the top of a checkout, in any
                   session but the owner's own (its hub folder is hub-secrets', the owner's
                   delegation list owner-delegations')
  kit-json         an agent session's edit of a .kit.json in a checkout
  generated-file   an edit of a file tools/render_content.py writes in a kit checkout (a host's
                   agents, skills copies, plugin manifests: kitlib.guard.generated), in any session
  main-checkout    an edit in a main checkout by a role whose edit_main_checkout is false
  write-jail       an agent session's write outside its own checkout, its repository's other
                   worktrees, the temp folder and the .kit.json data roots (kitlib.guard.jail);
                   git that writes a checkout outside them
  wrapped-gate     (off by default) gate.py or `machine_lock.py run` piped into another command (P16)
  root-search      a recursive search from a drive or filesystem root
  heavy            (a warning by default) a heavy program run with no lock held (MACHINE_LOCK_RUN
                   verified by nest_check)
  git-*            damaging git in a main checkout; branch -D anywhere; a push that deletes or
                   force-pushes main, in every repository (with no .kit.json too); a push to main
                   with no gate-OK record when the repo sets repo.push_block; push --no-verify, send-pack,
                   http-push, a remote helper or a git alias for one in a repository with a PM
                   data record, and any push from a copy of PM data without one; a remote
                   repointed (git-remote-rewrite) in a repository with one; all in any session
                   but the owner's own
  git-shared-ref   git update-ref by an agent session (a subagent or a jailed mark but pm) from any
                   checkout, of a ref other than refs/tmp/<name>, a per-worktree ref, or in a linked
                   worktree its HEAD and the branch checked out there (never a guarded main); a
                   ref or a repository the text does not name is refused too. A coordinator's
                   main session may also delete a stray ref (refs/ outside refs/heads, refs/tags,
                   refs/stash and refs/backlog) in its own repository. The same allowance
                   judges a push into the repository itself, a fetch or pull refspec's
                   destination (a remote's own tracking ref passes), symbolic-ref with two
                   names or -d, replace and a notes write
  git-hooks-path   a change of core.hooksPath (git config, git -c, clone -c, GIT_CONFIG_*), in
                   any session
  git-stash-shared git stash in a linked worktree by an agent session that adds an untagged
                   entry or takes one it does not name (bare stash, pop, save, clear)
  owner-identity   the owner's email given to a commit, in any session but the owner's own
  kill-by-name     an agent session ending processes by name (taskkill /IM, Stop-Process -Name)
  cmd-reparse      a batch file run (.cmd, .bat, the bare kit command) or a Git Bash start run
                   with an argument cmd.exe would read again (shell.CMD_REPARSE), a computed one, a
                   splat, an unquoted bash wildcard or xargs' input, in any session (the shell
                   rules' last)
  busy-wait        (off by default) a foreground sleep loop with no counter, clock or timeout bounding it
  git-alias        (a warning by default) a git alias defined on the command line (-c alias.<name>=...)
A rule's mode (deny, warn or off) is kitlib.guard.modes'; a rule it does not name is deny.
A command line the parser cannot read cleanly also goes through kitlib.guard.fallback (its rule,
unreadable, is a warning by default).
"Agent session" and "the owner's own" are the session mark's (docs/guard.md "Session mark").
"""
import contextvars
import fnmatch
import json
import ntpath
import os
import posixpath
import re

from kitlib import agentname
from kitlib import config as kconfig
from kitlib.config import user as kuser
from kitlib import kitroot
from kitlib import pmdata
from kitlib.git import defaults as gitdefaults
from kitlib.git import repo
from kitlib import lockmcp as lockmcp_names
from kitlib.host import Decision

from . import commands as cm
from . import context
from . import generated
from . import hub as hubg
from . import jail
from . import modes
from . import paths
from . import shell as sh
from .rules_base import (AGENT_MARKS, ALLOW, ASK, CITE_MAX, DENY, ELIDED, LEDGER_GLOB_CHARS, NULL_TARGETS, PSCmdlet,
                         PS_ALIASES, PS_CMDLETS, PS_COMMON_ALIASES, PS_COMMON_SWITCHES, PS_COMMON_VALUES,
                         PS_NAMED_DYN_RE, SHELLS, TAG, WIN_OPT_RE, _COMMAND, _FILTERS, _PATHS, _split_commas,
                         advice_kit, agent_session, bare, cited, fwd, here, is_owner_session, kept_word, kit_tool,
                         literal_word, lock_tool, owner_host, ps_args, ps_bound, ps_native_unsure, ps_param_word,
                         ps_params, quote, refuse, reworded, same_dir, shell_word, subagent, tag, tag_role, tail_word,
                         unowned_chat)
from .rules_owner import (COMPUTED_TAIL_RE, HUB_NAMES_LIMIT, HUB_OWNER_WHY, HUB_SCRIPT, HUB_SECRETS_WHY, HubPlaces,
                          HubShell, LOCK_SCRIPT, LOCK_VALUE_OPTS, NODE_EVAL, NODE_VALUE_OPTS, OPENERS, OWNER_LOCK_WHY,
                          OWNER_SETUP_WHY, PY_FLAG_RE, PY_RUN_RE, SETUP_APPLY, SETUP_SCRIPTS, UI_NAME_RE, _tool_run,
                          computed_script, glob_folder, host_mcp_prefix, host_mcp_separator, hub_ask_alternative,
                          hub_config, hub_folder_read, hub_line, hub_mcp, hub_owner_run, hub_path_texts,
                          hub_read_alternative, hub_real, hub_run_args, hub_run_verb, hub_secret_edit,
                          hub_secret_folders, hub_secret_names, hub_secret_read, hub_secret_shell, hub_secrets_refusal,
                          hub_session, hub_texts, inline_code, kit_run, lock_mcp, lock_owner_write, mcp_owner_tool,
                          mcp_prefix, owner_tool, owner_tool_run, script_run, tool_run, tool_words, unowned_name)
from .rules_run import (BASH_LEAD_SKIP, BOUND_RE, BUSY_MINUTES, BUSY_WHY, DUP_RE, GATE_NOTHING, GLOB_ANY,
                        GLOB_HINT, KILL_VALUE_LONG, KILL_VALUE_SHORT, PS_PROC_ALIASES, PS_PROC_SWITCHES,
                        PS_PROC_VALUES, PS_SPLIT_C_RE, PS_STOP, ROOT_VAR_RE, SIGNAL_OPT_RE, SLEEPERS,
                        THREAD_SLEEP_RE, WILD, _ROOT_NAMES, _ROOT_VAR, _backgrounded, _bash_loop_do,
                        _bash_loops, _busy_loop, _extends, _find_paths, _first, _in_outer, _kill_patterns,
                        _lead, _match_bracket, _nested_under, _ps_loop_span, _ps_loops, _segments, _sleeps,
                        _under_timeout, bare_wrapper, busy_wait, gate_runs_nothing, heavy, is_root,
                        kill_by_name, killed_name, kit_script, kit_tool_dir, kit_wrapper, lock_alternative,
                        lock_run_help, plain_run, ps_proc_params, root_search, run_from, search_paths,
                        split_wild, wrapped_gate)
from .rules_gitcore import (ALIAS_BLANKS, ALIAS_DEPTH, CONFIG_LONGS, CONFIG_LONG_VALUES, CONFIG_READS, CONFIG_READ_OPTS,
                            CONFIG_SECTIONS, CONFIG_SHORTS, CONFIG_STRICT_READ_OPTS, CONFIG_WRITES, GIT_BUILTINS,
                            GIT_LONGS, GIT_LONG_VALUES, GIT_SHORT_VALUES, HOOKS_ENV, HOOKS_KEY, HOOKS_WHY, IDENTITY_ENV,
                            IDENTITY_KEYS, _canon_long, _config_files, _config_long, _config_scope_all, _is_kit_hooks,
                            _names_email, _one_letter, _opts, _owner_identity_use, _pos, _under_temp, _without,
                            clean_dry_run, clone_configs, config_args, env_assignments, git_call_judged,
                            git_config_call, git_hooks_path, git_prefix_configs, hooks_config_write, inline_alias,
                            kit_githooks, owner_identity, repo_config_file, restore_index_only, split_alias,
                            stash_reads)
from .rules_pmdata import (DataBranch, Judged, LAND_HOW, UNPLACED, _branch_of, _guarded_mains, _no_verify_word,
                           _push_names_branches, _unknown_dir_counts, data_copy, git_push, git_push_pm_data,
                           git_push_pmdata, home_config, kit_judged, landing_command, own_worktree, pmdata_recorded)
from .rules_edit import (BLIND_BRANCH_INSTEAD, DEV_DRIVE, DOS_WILDCARDS, GATE_RESULT, JAIL_ASK, KIT_JSON, LONG_PREFIX,
                         MACHINE_CONFIG, MACHINE_CONFIG_WHY, MAIN_CWD_HINT, OUTPUT_WRITERS, PLACE_RULES, PM_DATA_WHY,
                         PROVIDER_ITEM_WHY, REDIRECT_HINT, STANDING_WHY, UNPLACED_BRANCH_INSTEAD, UNPLACED_BRANCH_WHY,
                         WT_ADD, _short_path, backlog_edit, cd_line, checkout_branch_only, checkout_makes_branch,
                         checkout_moves, computed_option_word, delegations_edit, dev_drive_bound, edit_target, file_key,
                         gate_result, generated_file, git_every_path, glob_part, held_names, held_spec, in_dropped,
                         in_git_dir, jail_aim, jail_refusal, jail_roots, jail_view, jail_why, kit_json, ledger_git,
                         ledger_git_held, ledger_git_moves, ledger_git_path_form, ledger_git_tree_form,
                         ledger_git_unread, ledger_glob, ledger_guarded, ledger_instead, ledger_kind, ledger_rule,
                         ledger_unread, ledger_what, ledger_wide_binds, lock_state, lock_state_dir, machine_config,
                         machine_config_folder, machine_config_refusal, main_checkout, marked_not_pm, new_worktree,
                         only_new, outer_shell, pm_data_unknown, prefix_end, session_mark, short_parts, stream_free,
                         switch_creates_here, unplaced, unplaced_branch_instead, unprefixed, whole_line,
                         worktree_alternative, write_jail)


# ---------------------------------------------------------------- entry

def decide(event, ctx=None, environ=None, profile=None):
    """The Decision on the kitlib.host.Action `event`: 'allow', or the first rule's deny or ask.
    With no `ctx`, a context.Ctx of `environ` (default os.environ) and the host `profile` (a
    HostProfile or a callable returning one) is built here, so its reads fail inside decide.
    The shared rules judge first, so a call they refuse is refused as it always was; a call they
    allow or ask then meets the role policy (kitlib.guard.rolepolicy; its refusal wins over an ask), which is off for a role whose
    .kit.json entry names no mode, and, when it adds write-jail roots, the shared rules again.
    Each refusal meets its rule's mode (kitlib.guard.modes): an allow that a warn-mode rule
    refused carries the warnings (rule WARNED, reason WARNING plus each refusal's reason), each
    quoting at most CITE_MAX characters of a shell call's text (refuse, cited)."""
    from . import rolepolicy
    ctx = ctx or context.Ctx(environ=environ, profile=profile)
    command = getattr(event, 'command', None) if getattr(event, 'kind', None) == 'shell' else None
    token = _COMMAND.set(command if isinstance(command, str) else None)
    try:
        return _decide(event, ctx, rolepolicy)
    finally:
        _COMMAND.reset(token)


def _decide(event, ctx, rolepolicy):
    with modes.judging(ctx) as warnings:
        asked = Asked()                         # an ask still meets the role policy, whose refusal wins
        d = asked.firm(judge(event, ctx))
        if d:
            return d
        d, held = rolepolicy.screen(event, ctx)
        if d is None and held is not ctx:
            d = judge(event, held)
        d = asked.firm(d) or asked.first
        if d:
            return d
        if warnings:
            return Decision(ALLOW, WARNED, WARNING + ' '.join(warnings))
    return Decision(ALLOW)


WARNED = 'warn'
WARNING = '[kit guard warning] '


def judge(event, ctx):
    """decide() with None for an allow: the shared rules, before any role policy."""
    tool = event.tool or ''
    cwd = (event.workdir if event.kind == 'shell' else None) or event.cwd or os.getcwd()
    role = tag_role(event)
    d = lock_mcp(event, ctx, tool, role)
    if d:
        return d
    if event.kind == 'edit':
        target = event.path
        if not isinstance(target, str) or not target:
            return None
        target = sh.resolve_path(target, cwd, home=getattr(ctx, 'home', None)) or target
        form = event.retarget
        return edit_target(event, ctx, target, cwd, role, '%s of %s' % (tool, target),
                           rewrite=(lambda path: form.replace('{path}', fwd(path))) if form else None)
    if event.kind == 'shell':
        text = event.command
        if not isinstance(text, str) or not text.strip() or event.shell not in SHELLS:
            return None
        return shell_command(event, ctx, text, SHELLS[event.shell], cwd, role)
    if event.kind == 'mcp':
        return hub_mcp(event, ctx, tool, role)
    if event.kind == 'read':
        return hub_secret_read(event, ctx, cwd, role)
    return None


def shell_command(event, ctx, text, shell, cwd, role):
    parsed = cm.read(text, shell, cwd, home=ctx.home)
    hub_rules = HubShell(event, ctx, parsed)
    late = None             # cmd-reparse's, judged last: every other rule's refusal, the fallback's too, comes first
    asked = Asked()         # an ask (the unread ledger's) waits: any refusal on the line wins over it
    for cmd in parsed.commands:
        inv = cm.invocation(cmd)
        for rule in (owner_tool, hub_rules.secrets, hub_rules.owner_verb, owner_delegations, ledger_git,
                     wrapped_gate, root_search, heavy, git_rules, backlog_shell, gate_allow, git_hooks_path,
                     owner_identity, kill_by_name, shell_writes, git_plain_commit, interpreter_git):
            d = asked.firm(rule(event, ctx, cmd, inv, cwd, role))
            if d:
                return d
        late = late or asked.firm(cmd_reparse(event, ctx, cmd, inv, cwd, role))
    d = asked.firm(brace_limit(parsed, role))
    if d:
        return d
    d = asked.firm(busy_wait(event, ctx, parsed, cwd, role))
    if d:
        return d
    if not parsed.clean:
        from . import fallback
        d = asked.firm(fallback.check(event, ctx, text, cwd, role, parsed))
        if d:
            return d
    return late or asked.first


class Asked(object):
    """The first ask among a run of decisions, held back so that a refusal judged after it wins:
    firm(d) gives `d` unless it is an ask (kept in `first`, the earliest), when it gives None."""

    def __init__(self):
        self.first = None

    def firm(self, d):
        if d is not None and d.verdict == ASK:
            self.first = self.first or d
            return None
        return d


def brace_limit(parsed, role):
    """A line with a bash word whose brace groups give more than shell.BRACE_LIMIT words (rule
    brace-limit, every session): the guard does not write that word out, so no path rule sees the
    words bash makes from it, and the line is refused rather than read as a computed word. The
    alternative is the word's shell text with the word replaced by a placeholder (docs/guard.md
    "Rules")."""
    if parsed.brace_over is None:
        return None
    text, start, end = parsed.brace_over
    word = text[start:end]
    if len(word) > 80:
        word = word[:77] + '...'
    alt = (text[:start] + BRACE_NAMES % sh.BRACE_LIMIT + text[end:]).strip()
    return refuse('brace-limit', role, 'The guard could not read this command: its word %s gives more than %d '
                  'words by brace expansion, and the guard writes out at most %d, so it cannot tell what the '
                  'command names. Write the names out, or split them into words that give at most %d each.'
                  % (word, sh.BRACE_LIMIT, sh.BRACE_LIMIT, sh.BRACE_LIMIT), alt)


BRACE_NAMES = '<its names, written out or in brace words of at most %d each>'


CMD_CHAR_NAMES = {'\r': 'a carriage return', '\n': 'a line break'}


def cmd_reparse(event, ctx, cmd, inv, cwd, role):
    """A batch file run (commands.batch_run: a .cmd or .bat, cmd's call of one, the bare kit
    command) whose arguments cmd.exe would read again before the program does (rule cmd-reparse,
    every session; docs/guard.md "Batch files and cmd.exe"): an argument holding one of
    shell.CMD_REPARSE, a computed one (cmd.exe reads the value it gets), a PowerShell splat, an
    unquoted bash wildcard (bash fills in file names, which cmd.exe then reads), or arguments xargs
    gives it. Git Bash's start (commands.bash_start) hands every word to cmd.exe, whatever it
    starts, so each of its words is read the same way. The kit command's alternative is the same
    words through python and tools/kit.py, which no cmd.exe reads; another program's is to run it
    directly."""
    found = cm.batch_run(cmd, inv)
    started = None
    if found is None:
        started = cm.bash_start(cmd, inv)
        if started is None:
            return None
        progw, args = started
        prog = progw.text if progw is not None and not progw.dyn else None
        after = args[next(k for k, w in enumerate(args) if w is progw) + 1:] if progw is not None else []
    else:
        prog, args = found
        after = args
    ps = cmd.shell == sh.PS
    why = 'xargs gives it arguments the guard cannot read' if inv.via == 'xargs' else None
    for w in args:
        if why:
            break
        shown = w.text.replace('\r', '\\r').replace('\n', '\\n')
        shown = shown if len(shown) <= 60 else shown[:57] + '...'
        if w.dyn:
            why = 'its argument %s is computed, and cmd.exe reads the value it gets' % shown
        elif ps and w.start is not None and cm.PS_SPLAT_RE.match(w.text) and not cm.quoted(cmd, w):
            why = 'its argument %s is a splat, whose values the guard cannot read' % shown
        else:
            c = sh.cmd_unsafe(w.text)
            if c is not None:
                why = 'its argument %s holds %s, which cmd.exe reads' % (shown, CMD_CHAR_NAMES.get(c, c))
            elif cm.bash_glob(cmd, w):
                why = ('its argument %s is a wildcard bash fills in with file names, which cmd.exe then '
                       'reads (a file named x&whoami would run whoami)' % shown)
    if why is None:
        return None
    signs = ('& | < > run or redirect another command, ^ and quotes change the words, % and ! '
             'expand variables, a line break drops the words after it')
    if started is None:
        what = ('This runs the batch file %s, and Windows runs a batch file in cmd.exe, which reads its '
                'command line again before the program sees it: %s (%s).' % (prog, why, signs))
    else:
        what = ('This runs %s through Git Bash\'s start, a script that hands all its words to cmd.exe\'s '
                'start (escaping only &), so cmd.exe reads them before the program sees them, and again '
                'when the program is a batch file: %s (%s).'
                % (prog or 'a program', why, signs))
    if prog is not None and cm.is_batch(prog) and cm.base_name(prog.rstrip('. ')) == cm.KIT:
        words = []
        for w in after:
            if ps and w.text == '--%' and not w.dyn:
                continue
            unsure = (w.dyn or '\r' in w.text or '\n' in w.text
                      or (ps and w.start is not None and cm.PS_SPLAT_RE.match(w.text) and not cm.quoted(cmd, w)))
            if not unsure and cm.bash_glob(cmd, w):
                # through python no cmd.exe reads what bash fills in: the wildcard as written
                spelt_at = cmd.source[w.start:w.end] if w.start is not None and w.end is not None else None
                words.append(spelt_at if spelt_at and not sh.cmd_unsafe(spelt_at) else '<value>')
                continue
            words.append('<value>' if unsure else shell_word(w.text))
        instead = ' '.join(['python', kit_tool(ctx, 'kit')] + words)
    elif started is not None:
        instead = ('run %s directly, without start, so no cmd.exe reads the words, or give start words '
                   'without cmd.exe\'s characters (& | < > ^ %% ! ( ) " and line breaks) and no '
                   'unquoted wildcard.' % (prog or 'the program'))
    else:
        instead = ('run the program %s runs directly (python, node, the exe it starts), so no cmd.exe '
                   'reads the words again, or give it words without cmd.exe\'s characters '
                   '(& | < > ^ %% ! ( ) " and line breaks) and no unquoted wildcard.' % prog)
    return refuse('cmd-reparse', role, what, instead)


def backlog_shell(event, ctx, cmd, inv, cwd, role):
    """The backlog roles' shell rules (kitlib.guard.backlogrole.shell)."""
    from . import backlogrole
    return backlogrole.shell(event, ctx, cmd, inv, cwd, role)


def owner_delegations(event, ctx, cmd, inv, cwd, role):
    """A write, move or delete of the owner's delegation list (kitlib.guard.delegfile.shell)."""
    from . import delegfile
    return delegfile.shell(event, ctx, cmd, inv, cwd, role)


def gate_allow(event, ctx, cmd, inv, cwd, role):
    """Who may run `gate.py allow` (kitlib.guard.gaterole.shell)."""
    from . import gaterole
    return gaterole.shell(event, ctx, cmd, inv, cwd, role)


# ---------------------------------------------------------------- shell writes

BASH_WRITERS = {'tee', 'touch', 'mkdir', 'rm', 'rmdir', 'cp', 'mv', 'truncate', 'install', 'ln',
                'unlink', 'shred', 'dos2unix', 'unix2dos', 'mac2unix', 'unix2mac', 'sponge'}
CMD_WRITERS = {'copy', 'xcopy', 'robocopy', 'move', 'del', 'erase', 'md', 'mkdir', 'rd', 'rmdir',
               'ren', 'rename', 'type'}
COPIERS = {'robocopy', 'xcopy'}                 # Windows programs every shell runs
EVALS = {'node': ('-e', '--eval', '-p', '--print'), 'ruby': ('-e',)}
# a .NET file call that writes (OpenRead and OpenText only read)
IO_FILE_RE = re.compile(r'(?i)\[(?:System\.)?IO\.(?:File|Directory)\]::'
                        r'(?:Write|Append|Create|Copy|Move|Delete|Replace|Open(?!Read\b|Text\b))')
# a .NET type whose constructor opens a file to write: [IO.StreamWriter]::new(...), New-Object IO.FileStream ...
NET_TYPE_RE = re.compile(r'(?i)(?<![\w.])(?:System\.)?IO\.(?:StreamWriter|FileStream)(?![\w.])')
NET_ABS_RE = re.compile(r'^[A-Za-z]:[\\/]')     # a path .NET reads the same from any directory
# \\?\<drive>:\x and \\.\<drive>:\x are <drive>:\x (a device path); any other \\ or // path is a share the guard cannot place
NET_DEVICE_RE = re.compile(r'^[\\/]{2}[?.][\\/](?=[A-Za-z]:[\\/])')


# an Export-* cmdlet's parameters that never name the file it writes (ps_export_targets)
PS_EXPORT_NO_PATH = ('inputobject', 'depth', 'encoding') + PS_COMMON_VALUES


def ps_export_targets(cmd, words):
    """[Word]: every argument of an Export-* cmdlet with no spec in PS_CMDLETS that could name the
    file it writes. Its parameters are unknown, so a positional, the next word after any parameter
    and a colon value are each a target; only the value of a parameter named exactly in
    PS_EXPORT_NO_PATH is not (`Export-Alias -As Script $p` judges Script and $p)."""
    out, k = [], 0
    while k < len(words):
        w = words[k]
        t = ps_param_word(cmd, w)
        k += 1
        if t is None:
            out.extend(_split_commas(w))
            continue
        body, colon, val = t[1:].partition(':')
        body = body.lower()
        no_path = PS_COMMON_ALIASES.get(body, body) in PS_EXPORT_NO_PATH
        if colon and val:
            if not no_path:
                out.append(sh.Word(val, w.dyn, w.start, w.end))
        elif no_path and k < len(words) and ps_param_word(cmd, words[k]) is None:
            k += 1                              # its value
    return out


# Why a path a cmdlet takes from the pipeline is one the guard cannot place
PIPE_UNPLACED = 'the text does not show what the command before the | sends'
PIPE_WORD = '(a path from the pipeline)'


def pipe_stand_in(cmd):
    """The Word write_targets gives for a path a PowerShell cmdlet takes from the pipeline
    (PSCmdlet piped): no text to place, at the command's end (pipe_stand_in_word)."""
    return sh.Word(PIPE_WORD, False, cmd.end, cmd.end)


def pipe_stand_in_word(cmd, w):
    """True when the write `w` is pipe_stand_in(cmd)."""
    return w.text == PIPE_WORD and not w.dyn and w.start == w.end == cmd.end


class NetWrite(object):
    """One path a .NET file call writes (net_writes): `word` its argument, dyn when the line
    computes it (a variable, an expression, a string with $ or a backtick), with its span in the
    source; `end` the offset just past the call's closing parenthesis; `paths` every path argument
    of the call, written or read (Copy's source too), `word` among them (net_retarget)."""
    __slots__ = ('word', 'end', 'paths')

    def __init__(self, word, end, paths=None):
        self.word, self.end, self.paths = word, end, paths if paths is not None else [word]


NET_QUOTES = '‘’‚‛“”„'     # PowerShell reads these as quotes too


def _net_string_end(src, k):
    """The offset just past the PowerShell string that starts at src[k] (' or ")."""
    q, n = src[k], len(src)
    k += 1
    while k < n:
        c = src[k]
        if q == '"' and c == '`':
            k += 2
            continue
        if c == q:
            if src[k + 1:k + 2] == q:
                k += 2
                continue
            return k + 1
        k += 1
    return n


def _net_literal(raw):
    """The text of the argument `raw` when it is a plain string ('...', or "..." with no $ or
    backtick), else None."""
    if any(c in NET_QUOTES for c in raw):
        return None
    m = re.match(r"^'((?:[^']|'')*)'$", raw, re.S)
    if m:
        return m.group(1).replace("''", "'")
    m = re.match(r'^"((?:[^"$`]|"")*)"$', raw, re.S)
    return m.group(1).replace('""', '"') if m else None


def _net_args(src, k):
    """([(text or None, start, end)], end): the arguments of the call whose '(' is just before
    offset `k` of `src` (text None: not a plain string, _net_literal), and the offset just past
    its closing parenthesis (len(src) when it has none)."""
    args, n = [], len(src)
    while True:
        while k < n and src[k] in ' \t\r\n':
            k += 1
        start, depth = k, 0
        while k < n:
            c = src[k]
            if c in '\'"':
                k = _net_string_end(src, k)
                continue
            if c == '`':
                k += 2
                continue
            if c in '([{':
                depth += 1
            elif c in ')]}':
                if depth == 0:
                    break
                depth -= 1
            elif c == ',' and depth == 0:
                break
            k += 1
        raw = src[start:min(k, n)].rstrip()
        if raw:
            args.append((_net_literal(raw), start, start + len(raw)))
        if k >= n:
            return args, n
        if src[k] != ',':
            return args, k + 1
        k += 1


def _net_written(method, count):
    """(indexes, all found): the indexes of the arguments a .NET File or Directory method writes -
    Copy its destination, Move both, Replace all three, any other its first - among the `count`
    arguments read, and False when one of them is past the last (the guard did not read the call
    as .NET will)."""
    if method.startswith('copy'):
        idx = (1,)
    elif method.startswith('move'):
        idx = (0, 1)
    elif method.startswith('replace'):
        idx = (0, 1, 2)
    else:
        idx = (0,)
    return [i for i in idx if i < count], all(i < count for i in idx)


def _net_lost(span):
    """True when _net_args may have lost its place in the argument text `span`, so the literal it
    read as an argument may be part of another: a smart quote (PowerShell closes one with a plain
    quote), a here-string (@" or @'), a comment (#), a braced variable (${: PowerShell reads its
    name up to the }, quotes included), or a subexpression ($() where a double quote also appears
    (a " inside it does not end the outer string)."""
    if any(c in NET_QUOTES for c in span) or '@"' in span or "@'" in span or '#' in span or '${' in span:
        return True
    return '"' in span and '$(' in span


def net_mentioned(cmd):
    """True when the PowerShell command names a .NET file call or type that writes (IO_FILE_RE,
    NET_TYPE_RE): net_writes reads it."""
    return cmd.shell == sh.PS and bool(IO_FILE_RE.search(cmd.text) or NET_TYPE_RE.search(cmd.text))


def _net_word(raw):
    """The text of a command-mode argument `raw`: a plain string (_net_literal) or a bare word,
    else None."""
    text = _net_literal(raw)
    if text is None and re.match(r'^[^\s\'"`$(){}\[\]@#;,|&<>%s]+$' % NET_QUOTES, raw):
        return raw
    return text


def _net_list(src, k):
    """([(text or None, start, end)], end): the command-mode argument at offset `k` of `src`, a
    word or a comma list of them ('a', 'b'), read as _net_args reads a call's; a bare word ends at
    a '(' (PowerShell starts a new argument there)."""
    args, n = [], len(src)
    while True:
        start, depth = k, 0
        while k < n:
            c = src[k]
            if c in '\'"':
                k = _net_string_end(src, k)
                continue
            if c == '`':
                k += 2
                continue
            if c in '([{':
                if c == '(' and depth == 0 and k > start and src[k - 1] != '$':
                    break
                depth += 1
            elif c in ')]}':
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and c in ', \t\r\n;|&':
                break
            k += 1
        k = min(k, n)
        if k > start:
            args.append((_net_word(src[start:k]), start, k))
        j = k
        while j < n and src[j] in ' \t':
            j += 1
        if j >= n or src[j] != ',':
            return args, k
        k = j + 1
        while k < n and src[k] in ' \t\r\n':
            k += 1


NEW_OBJECT_PARAMS = ('typename', 'argumentlist', 'property', 'comobject', 'strict')


def _net_new_object(cmd, inv):
    """(type (text or None, start, end), [argument], end, lost) of the New-Object call `inv`, or
    None when the guard does not read it: its -TypeName and -ArgumentList, named or positional,
    each a word, a comma list, (...) or @(...)."""
    src, n = cmd.source, len(cmd.source)
    k, vals, slots, lost = inv.word.end, {}, ['typename', 'argumentlist'], ''
    while True:
        while k < n and src[k] in ' \t':
            k += 1
        if k >= n or src[k] in ';|&\r\n)}':
            break
        m = re.compile(r'-([A-Za-z]+)(:?)').match(src, k)
        if m and src[k + 1:k + 2].isalpha():
            body = m.group(1).lower()
            names = ['argumentlist'] if body == 'args' else [p for p in NEW_OBJECT_PARAMS if p.startswith(body)]
            if len(names) != 1:                 # a common parameter (-ErrorAction Stop, -Verbose)
                body = PS_COMMON_ALIASES.get(body, body)
                if not any(p.startswith(body) for p in PS_COMMON_VALUES + PS_COMMON_SWITCHES):
                    return None
                names = ['skip' if any(p.startswith(body) for p in PS_COMMON_VALUES) else 'strict']
            k = m.end()
            if names[0] == 'strict':
                continue
            while not m.group(2) and k < n and src[k] in ' \t':
                k += 1
            param = names[0]
            if param in slots:
                slots.remove(param)
        elif slots:
            param = slots.pop(0)
        else:
            return None
        stops = ' \t;|&\r\n)}'
        if src.startswith('@(', k) or src.startswith('(', k):
            open_ = k + (2 if src[k] == '@' else 1)
            args, k = _net_args(src, open_)
            lost += src[open_:k]
        else:
            args, k = _net_list(src, k)
            lost += ''.join(src[s:e] for _t, s, e in args)
            stops += '('                        # IO.StreamWriter('x'): the list starts a new argument
        if k < n and src[k] not in stops:
            return None                         # ('a') + 'b': an expression the guard does not read
        vals[param] = args
    kind = vals.get('typename') or []
    if len(kind) != 1:
        return None
    return kind[0], vals.get('argumentlist', []), k, _net_lost(lost)


def _net_ctor(src, kind, args, end, out):
    """Add the path a StreamWriter or FileStream constructor with `args` writes to `out`: its first
    argument. True when the guard does not read it: no arguments, or a StreamWriter's first argument
    that is not a plain string (a Stream it writes through, judged where it is opened, or a path the
    line computes). A FileStream opened only to read counts as a write (docs/guard-v2.md v2:114)."""
    if not args:
        return True
    text, s, e = args[0]
    if text is None and (kind == 'streamwriter' or src[s:e].lower() == '$null'):
        return kind == 'streamwriter'
    out.append(NetWrite(sh.Word(src[s:e] if text is None else text, text is None, s, e), end))
    return False


def net_writes(cmd):
    """([NetWrite], unread): each path the PowerShell command's .NET file calls (IO_FILE_RE) and
    StreamWriter and FileStream constructors (NET_TYPE_RE: ::new(...) or New-Object) write, and True
    when a match is a write the guard does not read the path of, so write_targets judges the
    directory the command runs in, as a path it cannot place: one inside a quoted word, a method named with no '(' after it (called
    later, through .Invoke or a variable), a call with fewer arguments than the ones it writes
    (_net_written), one whose arguments the parse may have lost its place in (_net_lost), a type
    named any other way (a cast, [type] as a value) and a New-Object call _net_new_object does
    not read. A $null argument (Replace's backup) writes nothing."""
    if cmd.shell != sh.PS:
        return [], False
    out, unread, src = [], False, cmd.source
    read = []                                   # the span of the type a New-Object call names
    inv = cm.invocation(cmd) if NET_TYPE_RE.search(cmd.text) else None
    if inv is not None and inv.name == 'new-object':
        got = _net_new_object(cmd, inv)
        kind = got and got[0][0] and re.match(r'(?i)^(?:System\.)?IO\.(StreamWriter|FileStream)$', got[0][0])
        if kind:
            read.append(got[0][1:])
            unread = _net_ctor(src, kind.group(1).lower(), got[1], got[2], out) or got[3]
    for m in NET_TYPE_RE.finditer(cmd.text):
        pos, after = cmd.start + m.start(), cmd.start + m.end()
        if any(s <= pos < e for s, e in read):
            continue
        w = next((x for x in cmd.words if x.start <= pos < x.end), None)
        call = re.compile(r'\]::(\w+)').match(src, after)
        if w is None or src[w.start:w.start + 1] in '\'"' + NET_QUOTES or src[pos - 1:pos] != '[':
            unread = True                       # in a string, a type name passed as a value
            continue
        if not call:
            unread = True                       # [type] passed as a value, a cast
            continue
        if call.group(1).lower() != 'new':
            continue                            # a static member: [IO.StreamWriter]::Null
        k = call.end()
        while k < len(src) and src[k] in ' \t':
            k += 1
        if src[k:k + 1] != '(':
            unread = True
            continue
        args, end = _net_args(src, k + 1)
        kind = re.search(r'(?i)(StreamWriter|FileStream)$', m.group(0)).group(1).lower()
        unread = _net_ctor(src, kind, args, end, out) or _net_lost(src[k + 1:end]) or unread
    for m in IO_FILE_RE.finditer(cmd.text):
        pos = cmd.start + m.start()
        w = next((x for x in cmd.words if x.start <= pos < x.end), None)
        if w is None or src[w.start:w.start + 1] in '\'"' + NET_QUOTES:
            unread = True
            continue
        rest = re.compile(r'\w*').match(src, cmd.start + m.end())
        method = (m.group(0).rpartition('::')[2] + rest.group(0)).lower()
        k = rest.end()
        while k < len(src) and src[k] in ' \t':
            k += 1
        if src[k:k + 1] != '(':
            unread = True
            continue
        args, end = _net_args(src, k + 1)
        written, found = _net_written(method, len(args))
        unread = unread or not found or _net_lost(src[k + 1:end])
        words = {}
        for i in sorted(set(written) | ({0} if method.startswith('copy') and args else set())):
            text, s, e = args[i]
            if text is None and src[s:e].lower() == '$null':
                continue
            words[i] = sh.Word(src[s:e] if text is None else text, text is None, s, e)
        for i in written:
            if i in words:
                out.append(NetWrite(words[i], end, [words[j] for j in sorted(words)]))
    for x in out:
        m = None if x.word.dyn else NET_DEVICE_RE.match(x.word.text)
        if m:                                   # \\?\<drive>:\x is <drive>:\x; the span stays the argument's
            x.word = sh.Word(x.word.text[m.end():], False, x.word.start, x.word.end)
    return out, unread


def net_placed(w):
    """True when the .NET argument Word `w` is a literal absolute path on a drive (NET_ABS_RE;
    net_writes reads \\\\?\\<drive>:\\x as <drive>:\\x). A share (\\\\host\\<drive>$\\x) is not: the guard cannot tell
    which folder it is, so write_targets judges it as a path it cannot place."""
    return not w.dyn and bool(NET_ABS_RE.match(w.text))


def net_relative(w):
    """True when the .NET argument Word `w` is a literal relative path (no drive, no leading slash,
    no ~): write_targets judges it where the PowerShell location puts it, as a bash one is, though
    .NET reads it against the process's directory (NET_PROCESS_DIR), which the guard cannot see;
    so an agent session's is refused as well (net_process_dir)."""
    return not w.dyn and bool(w.text) and w.text[0] not in '\\/~' and ':' not in w.text


# where .NET reads a path that is not absolute
NET_PROCESS_DIR = ('.NET reads a relative or driveless path against the process\'s working directory '
                   '([Environment]::CurrentDirectory), not the PowerShell location')
# why a .NET write the guard cannot place is counted as a write to the directory the line runs in
NET_UNPLACED = ('a .NET call whose path the guard does not read or cannot place: a driveless path, a share or '
                'device path, or one the line computes. ' + NET_PROCESS_DIR)


def net_process_dir(event, ctx, role, what, relative, target, rewrite):
    """An agent session's .NET write whose call names a literal relative path (net_relative, the
    texts `relative`: the path written, or Copy's source), already judged where the PowerShell
    location puts it (`target`): refused all the same, under write-jail, since .NET reads it
    against the process's directory, which Set-Location does not move and the guard cannot see (a
    docs-writer's [IO.File]::WriteAllText('docs/ROSTER.md', ...) from its worktree wrote the main
    checkout). The alternative is the same call with every relative path the absolute one the
    location gives (net_retarget), which .NET reads the same from any directory."""
    if not agent_session(event, ctx):
        return None
    return refuse('write-jail', role, '%s; the call names the relative path %s: .NET reads it against the process\'s '
                  'working directory ([Environment]::CurrentDirectory), which cd and Set-Location do not move, so '
                  'the guard cannot tell what it names. An agent session gives .NET absolute paths. The same call '
                  'with the paths the PowerShell location gives:' % (what, ', '.join(relative)), rewrite(fwd(target)))


def net_retarget(cmd, x, path, place=None):
    """The command `cmd` through the end of its .NET call, with the NetWrite `x`'s argument
    replaced by `path` and, with `place` (Word -> path or None), each other literal relative path
    argument of the call (net_relative: Copy's source, Move's other path) by the path it gives, so
    the alternative names no path .NET would read from the process's directory."""
    start = cmd.words[0].start if cmd.words else cmd.start
    out, k = [], start
    for o in sorted(x.paths, key=lambda o: o.start):
        own = (o.start, o.end) == (x.word.start, x.word.end)     # net_writes may have re-read x.word (a device path)
        new = path if own else place(o) if place is not None and net_relative(o) else None
        if new is None:
            continue
        out.append(cmd.source[k:o.start] + literal_word(new))
        k = o.end
    return (''.join(out) + cmd.source[k:x.end]).strip()


def _cluster_values(args, value_shorts, value_longs, k):
    """Step over one option at args[k]: (next k, [(option, value Word or None)])."""
    t = args[k].text
    if t.startswith('--'):
        name, eq, val = t.partition('=')
        if eq:
            return k + 1, [(name, sh.Word(val, args[k].dyn, args[k].start, args[k].end))]
        if name in value_longs:
            return k + 2, [(name, args[k + 1] if k + 1 < len(args) else None)]
        return k + 1, [(name, None)]
    out = []
    body = t[1:]
    for j, c in enumerate(body):
        if c in value_shorts:
            rest = body[j + 1:]
            if rest:
                return k + 1, out + [('-' + c, sh.Word(rest, args[k].dyn, args[k].start, args[k].end))]
            return k + 2, out + [('-' + c, args[k + 1] if k + 1 < len(args) else None)]
        out.append(('-' + c, None))
    return k + 1, out


def unix_opts(args, value_shorts='', value_longs=()):
    """([(option, value Word or None)], [positional Word]) of a Unix command's arguments."""
    opts, pos = [], []
    k = 0
    while k < len(args):
        t = args[k].text
        if t == '--':
            pos.extend(args[k + 1:])
            break
        if t.startswith('-') and len(t) > 1:
            k, got = _cluster_values(args, value_shorts, value_longs, k)
            opts.extend(got)
            continue
        pos.append(args[k])
        k += 1
    return opts, pos


def _sed(args):
    in_place = False
    for w in args:
        t = w.text
        if t.startswith('--in-place'):
            in_place = True
        elif t.startswith('-') and not t.startswith('--') and 'i' in t:
            body = t[1:]
            stop = min([body.index(c) for c in 'efl' if c in body] or [len(body)])
            in_place = in_place or body.index('i') < stop
    if not in_place:
        return []
    opts, pos = unix_opts(args, 'efl', ('--expression', '--file', '--line-length'))
    script = any(o in ('-e', '-f', '--expression', '--file') for o, _v in opts)
    return pos if script else pos[1:]


def _perl(args, cmd_word=None):
    in_place, script, pos = False, False, []
    k = 0
    while k < len(args):
        t = args[k].text
        if t == '--':
            pos.extend(args[k + 1:])
            break
        if t.startswith('-') and len(t) > 1:
            body = t[1:]
            k += 1
            for j, c in enumerate(body):
                if c == 'i':
                    in_place = True
                    break                       # -i.bak: the rest is the suffix
                if c in 'eEMmI':
                    script = script or c in 'eE'
                    if not body[j + 1:]:
                        k += 1                  # the value is the next word
                    break
            continue
        pos.append(args[k])
        k += 1
    if not in_place:
        return [here(cmd_word)] if script else []     # perl -e: as python -c
    return pos if script else pos[1:]


# gawk 5's options (its getopt list "+F:f:v:W;bcCd::D::e:E:ghi:l:L::nNo::Op::MPrSstVYZ:", options
# ending at the first operand): the short ones that take a value, the ones whose value is only the
# rest of their word, and the long names (a unique prefix names one) that take a value, take one
# only after `=`, or take none
AWK_VALUE_SHORTS = 'FfvieElZ'
AWK_OPTIONAL_SHORTS = 'dDLop'
AWK_LONGS = {'assign': 'v', 'exec': 'E', 'field-separator': 'F', 'file': 'f', 'include': 'i', 'load': 'l',
             'source': 'e', 'locale': 'Z'}
AWK_OPTIONAL_LONGS = {'dump-variables': 'd', 'debug': 'D', 'lint': 'L', 'pretty-print': 'o', 'profile': 'p'}
AWK_FLAG_LONGS = ('bignum', 'characters-as-bytes', 'copyright', 'csv', 'gen-pot', 'help', 'lint-old',
                  'no-optimize', 'non-decimal-data', 'optimize', 'parsedebug', 'persist', 'posix',
                  're-interval', 'sandbox', 'trace', 'traditional', 'use-lc-numeric', 'version')
AWK_ASSIGN_RE = re.compile(r'^[A-Za-z_]\w*(?:::[A-Za-z_]\w*)?=')   # an operand gawk reads as var=value


def _awk_inplace_lib(w):
    """True when the -i/-f/-l value Word `w` may load gawk's inplace library: its name, or a value
    the line computes (fail closed)."""
    if w is None or w.dyn:
        return w is not None
    base = re.split(r'[\\/]', w.text)[-1].lower()
    return base in ('inplace', 'inplace.awk')


def _awk_inplace_text(w):
    """True when the program text Word `w` may @include the inplace library."""
    return w is not None and not w.dyn and '@include' in w.text and 'inplace' in w.text


def _awk_long(name):
    """The letter the long option `name` (without its dashes) stands for, or None when gawk does
    not know it or it is ambiguous (gawk then runs nothing)."""
    names = list(AWK_LONGS) + list(AWK_OPTIONAL_LONGS) + list(AWK_FLAG_LONGS)
    if name in names:
        hits = [name]
    else:
        hits = [n for n in names if n.startswith(name)]
    if len(hits) != 1:
        return None
    return AWK_LONGS.get(hits[0]) or AWK_OPTIONAL_LONGS.get(hits[0]) or ''


def _awk(args, cmd_word):
    """What awk/gawk writes: every file operand under the inplace library (`-i inplace` in any
    spelling, `-f inplace`, `-l inplace`, `@include "inplace"` in the program text; counted even
    with -v inplace::enable=0), and the file -d, -o or -p names (else the directory it runs in,
    for awkvars.out or awkprof.out). A run that is neither writes nothing."""
    in_place, program_given, dumps = False, False, []
    k = 0

    def take(letter, value):
        nonlocal in_place, program_given
        if letter in 'ifl' and _awk_inplace_lib(value):
            in_place = True
        if letter in 'feE':
            program_given = True
        if letter == 'e' and _awk_inplace_text(value):
            in_place = True
        if letter in 'dop':
            dumps.append(value if value is not None and value.text else here(cmd_word))

    def word(t, w):
        return sh.Word(t, w.dyn, w.start, w.end)

    while k < len(args):
        w = args[k]
        t = w.text
        if t == '--':
            k += 1
            break
        if not t.startswith('-') or len(t) < 2:
            break
        k += 1
        if t.startswith('--'):
            spec = t[2:]
        else:
            body, spec = t[1:], None
            for j, c in enumerate(body):
                rest = body[j + 1:]
                if c == 'W':
                    if rest:
                        spec = rest
                    elif k < len(args):
                        spec = args[k].text
                        w = args[k]
                        k += 1
                    break
                if c in AWK_VALUE_SHORTS:
                    if rest:
                        value = word(rest, w)
                    else:
                        value = args[k] if k < len(args) else None
                        k += 1
                    take(c, value)
                    break
                if c in AWK_OPTIONAL_SHORTS:
                    take(c, word(rest, w) if rest else None)
                    break
            if spec is None:
                if c == 'E':
                    break                       # -E file: the rest are the program's arguments
                continue
        name, eq, val = spec.partition('=')     # --name[=value], or -W name[=value]
        letter = _awk_long(name)
        if letter is None or letter == '':
            continue
        if eq:
            value = word(val, w)
        elif letter in AWK_OPTIONAL_SHORTS:
            value = None
        else:
            value = args[k] if k < len(args) else None
            k += 1
        take(letter, value)
        if letter == 'E':
            break
    rest = args[k:]
    if not program_given and rest:
        if _awk_inplace_text(rest[0]):
            in_place = True
        rest = rest[1:]
    if not in_place:
        return dumps
    files = [w for w in rest if w.dyn or (w.text != '-' and not AWK_ASSIGN_RE.match(w.text))]
    return dumps + files


def _ruby(args, cmd_word):
    """ruby -i[ext]: the files it edits, as perl -i (after the script unless -e gives the code),
    placed under each -C or -x folder in turn; without -i, code on the line (_evals)."""
    code = _evals('ruby', args, cmd_word)
    in_place, script, pos, dirs = False, False, [], []
    k = 0
    while k < len(args):
        w = args[k]
        t = w.text
        if t == '--':
            pos.extend(args[k + 1:])
            break
        if t.startswith('--') or t == '-':
            if t == '-':
                pos.append(w)
            k += 1
            continue                            # a long option: any value it takes is an operand (fail closed)
        if t.startswith('-') and len(t) > 1:
            body = t[1:]
            k += 1
            for j, c in enumerate(body):
                rest = body[j + 1:]
                if c == 'i':
                    in_place = True
                    break                       # -i.bak: the rest is the suffix
                if c in 'eIrCE':
                    script = script or c == 'e'
                    value = sh.Word(rest, w.dyn, w.start, w.end) if rest else (args[k] if k < len(args) else None)
                    if not rest:
                        k += 1
                    if c == 'C' and value is not None:
                        dirs.append(value)
                    break
                if c == 'x':
                    if rest:
                        dirs.append(sh.Word(rest, w.dyn, w.start, w.end))
                    break
                if c in 'FWT0K':
                    break                       # their value is the rest of the word
            continue
        pos.append(w)
        k += 1
    if not in_place:
        return code
    files = pos if script else pos[1:]
    for d in reversed(dirs):
        files = [_under(d, f) for f in files]
    return code + files


# vi, vim, nvim, ex and ed: a scripted run writes the files it is given, so every file operand
# counts, an operand-only read too; the options whose value is the next word (a short one last in
# its word, as `-sc <cmd>`) are passed over, bar -w/-W's script output file, which is written
EDITORS = {'vi', 'vim', 'nvim', 'ex', 'ed'}
EDITOR_VALUE_SHORTS = 'cuUiTtwW'


def _editor(name, args):
    out, k = [], 0
    while k < len(args):
        w = args[k]
        t = w.text
        if t == '--':
            out.extend(args[k + 1:])
            break
        if name != 'ed' and t.startswith('+'):
            k += 1                              # +cmd
            continue
        if t.startswith('-') and len(t) > 1:
            short = not t.startswith('--')
            if (t == '-p') if name == 'ed' else (t == '--cmd' or (short and t[-1] in EDITOR_VALUE_SHORTS)):
                if short and t[-1] in 'wW' and k + 1 < len(args):
                    out.append(args[k + 1])     # the script output file
                k += 2
            else:
                k += 1
            continue
        out.append(w)
        k += 1
    return [w for w in out if w.dyn or w.text != '-']


def _curl(args, cmd_word):
    out = []
    opts, _pos = unix_opts(args, 'oDcHduXAebTFxmrwKEQYyzCtP',
                           ('--output', '--dump-header', '--cookie-jar', '--header', '--data', '--user',
                            '--request', '--user-agent', '--referer', '--cookie', '--upload-file', '--form',
                            '--proxy', '--max-time', '--range', '--write-out', '--config', '--cert',
                            '--quote', '--time-cond', '--url', '--output-dir'))
    folder = None
    for o, v in opts:
        if o == '--output-dir' and v is not None:
            folder = v
    for o, v in opts:
        if o in ('-o', '--output') and v is not None:
            if v.text != '-':
                placed = _under(folder, v)
                out.append(placed)
                if folder is not None and placed is v:
                    out.append(Under(folder, v))    # curl joins --output-dir to an absolute name too
        elif o in ('-D', '--dump-header', '-c', '--cookie-jar') and v is not None:
            if v.text != '-':
                out.append(v)
        elif o in ('-O', '--remote-name', '--remote-name-all'):
            out.append(folder if folder is not None else here(cmd_word))
    return out


class Under(sh.Word):
    """A file a folder option places (_under): its text is the folder's joined with the name's;
    `folder` and `name` are the two words, which only the refusal's rewrite reads (word_rewrite)."""
    __slots__ = ('folder', 'name')

    def __init__(self, folder, name):
        cut = '/' if paths.posix() else '/\\'          # on Linux `\` is a name's character
        sh.Word.__init__(self, folder.text.rstrip(cut) + '/' + name.text, folder.dyn or name.dyn,
                         name.start, name.end)
        self.folder, self.name = folder, name


def _under(folder, w):
    """`w` placed in `folder` (curl --output-dir), unless it is absolute (on Linux: starts with `/`)."""
    if folder is None or (w.text.startswith('/') if paths.posix() else re.match(r'^(?:[A-Za-z]:)?[\\/]', w.text)):
        return w
    return Under(folder, w)


def _wget(args, cmd_word):
    out, prefix = [], None
    opts, pos = unix_opts(args, 'OoaPiBetTwQUlA',
                          ('--output-document', '--output-file', '--append-output', '--directory-prefix',
                           '--input-file', '--base', '--execute', '--tries', '--timeout', '--wait',
                           '--quota', '--user-agent', '--level', '--accept', '--reject', '--user', '--password'))
    if any(o == '--spider' for o, _v in opts):
        return []                               # checks the URL, writes nothing
    document = False
    for o, v in opts:
        if o in ('-O', '--output-document'):
            document = True
            if v is not None and v.text != '-':
                out.append(v)
        elif o in ('-o', '--output-file', '-a', '--append-output') and v is not None:
            out.append(v)
        elif o in ('-P', '--directory-prefix') and v is not None:
            prefix = v
    if not document and pos:
        out.append(prefix if prefix is not None else here(cmd_word))
    return out


def _patch(args, cmd_word):
    opts, _pos = unix_opts(args, 'odipFzBYVDrg',
                           ('--output', '--directory', '--input', '--strip', '--fuzz', '--prefix', '--suffix',
                            '--basename-prefix', '--version-control', '--reject-file', '--get'))
    names = [o for o, _v in opts]
    if '--dry-run' in names:
        return []
    for o, v in opts:
        if o in ('-o', '--output') and v is not None:
            return [v] if v.text != '-' else []
    for o, v in opts:
        if o in ('-d', '--directory') and v is not None:
            return [v]
    return [here(cmd_word)]


def _rsync(args):
    opts, pos = unix_opts(args, 'eTfB', ('--rsh', '--temp-dir', '--filter', '--exclude', '--include',
                                        '--exclude-from', '--include-from', '--files-from', '--block-size',
                                        '--partial-dir', '--backup-dir', '--suffix', '--chmod', '--log-file'))
    if any(o in ('-n', '--dry-run') for o, _v in opts):
        return []                               # a dry run lists, writes nothing
    return pos[-1:] if len(pos) >= 2 else []


# tar's short options that take no value in GNU tar and in bsdtar (Windows' tar.exe) alike, and
# those that take one besides -C; a letter in neither may take one. Long options known to take none.
TAR_FLAGS = set('xtcruvzjJZapkmOohPUwSBln')
TAR_VALUES = set('bfFgHIKLNTVX')
TAR_LONG_FLAGS = {'extract', 'get', 'list', 'create', 'append', 'update', 'verbose', 'gzip', 'gunzip', 'ungzip',
                  'bzip2', 'xz', 'lzma', 'zstd', 'lzip', 'compress', 'uncompress', 'auto-compress',
                  'keep-old-files', 'keep-newer-files', 'overwrite', 'preserve-permissions', 'same-permissions',
                  'no-same-owner', 'same-owner', 'no-same-permissions', 'unlink-first', 'absolute-names',
                  'dereference', 'totals', 'to-stdout', 'interactive', 'sparse', 'numeric-owner',
                  'no-recursion', 'touch', 'ignore-zeros', 'check-links', 'verify'}


class _TarDirs:
    """The folders a tar line may extract into: `bases` the folders in force (None is the working
    directory), `seen` every folder a -C named, `unsure` once a -C was read that may not be one."""

    def __init__(self):
        self.bases, self.seen, self.unsure = [None], [], False

    def move(self, folder, sure):
        """A -C to `folder`, relative to the one in force as tar reads it (-C /a -C b is /a/b);
        not `sure`: it may not be a -C, so the folders in force stay possible too."""
        new = []
        for b in self.bases:
            if paths.posix():                   # Linux: `\` is a name's character, no drives
                rooted, join = folder.text.startswith(('/', '~')), posixpath.join
            else:
                rooted = folder.text.startswith(('/', '\\', '~')) or ntpath.splitdrive(folder.text)[0]
                join = ntpath.join
            new.append(folder if b is None or rooted else
                       sh.Word(join(b.text, folder.text), folder.dyn or b.dyn, folder.start, folder.end))
        self.bases = new if sure else self.bases + new
        self.seen += new
        self.unsure = self.unsure or not sure


def _tar_old(args, dirs):
    """(index of the first word after the values, the state after them) for the old-style first
    word (tar xCf <dir> a.tar): each letter that takes a value takes the next word in turn. A letter
    the guard does not know may take one too, so a C after one names any of the words it could."""
    k, unknown = 1, 0
    for c in args[0].text:
        if c == 'C':
            for j in range(k, min(k + unknown + 1, len(args))):
                dirs.move(args[j], not unknown)
            k += 1
        elif c in TAR_VALUES:
            k += 1
        elif c not in TAR_FLAGS:
            unknown += 1
    return k, ('maybe' if unknown else None)


def _tar_word(args, k, state, dirs):
    """The state after tar's word args[k] (None, 'value': the next word is a value, 'maybe': it may
    be one, 'folder': it is -C's), moving `dirs` for a -C. A -C is sure only in a word that is no
    value of the word before it and, in a cluster, after letters that take none (-xvC <dir>);
    otherwise (-xfC <dir>: a file C; -NC<dir>: a date; --exclude -C<dir>) both readings stay."""
    w, t = args[k], args[k].text
    if state == 'folder':
        dirs.move(w, True)
        return None
    doubt = state is not None
    nxt = args[k + 1] if k + 1 < len(args) else None
    if t.startswith('--'):
        name, eq, value = t.partition('=')
        # --di is also --diff; bsdtar (Windows' tar.exe) reads --cd as -C
        if name == '--cd' or len(name) >= 5 and '--directory'.startswith(name):
            if eq:
                dirs.move(tail_word(w, value), not doubt)
                return None
            if not doubt:
                return 'folder'
            if nxt is not None:
                dirs.move(nxt, False)
            return 'maybe'
        return None if eq or name[2:] in TAR_LONG_FLAGS or state == 'value' else 'maybe'
    if not t.startswith('-') or len(t) < 2:
        return None
    clean = not doubt
    for j, c in enumerate(t[1:], 1):
        if c == 'C':
            rest = t[j + 1:]
            if clean:
                if rest:
                    dirs.move(tail_word(w, rest), True)
                    return None
                return 'folder'
            folder = tail_word(w, rest) if rest else nxt
            if folder is not None:
                dirs.move(folder, False)
            return 'maybe'
        if c not in TAR_FLAGS:
            clean = False
    if state == 'value':
        return None
    i = next((j for j, c in enumerate(t[1:], 1) if c not in TAR_FLAGS), None)
    if i is None:
        return None
    if t[i] not in TAR_VALUES:
        return 'maybe'
    if i + 1 < len(t):                          # -fa.tar: the value is attached
        return None
    return 'maybe' if doubt else 'value'


def _tar_stdout(t):
    """True when tar's word `t`, no value of the word before it, is -O/--to-stdout (GNU tar and
    bsdtar alike: -x prints each file and writes none): --to-stdout, or an O in a cluster before
    any letter that takes or may take a value (-xvOf; in -xfO the O is f's file)."""
    if t.startswith('--'):
        return t == '--to-stdout'
    if not t.startswith('-'):
        return False
    for c in t[1:]:
        if c == 'O':
            return True
        if c not in TAR_FLAGS:
            return False
    return False


def _tar(args, cmd_word):
    """[Word]: the folders tar -x writes: each folder a -C/--directory names, relative to the one
    before it (_TarDirs), with none the working directory, and the working directory too once a -C
    was read that may not be one (_tar_word, _tar_old: fail closed). None with -O/--to-stdout
    where tar reads it as that option (_tar_stdout; any O of the old-style first word)."""
    first = args[0].text if args else ''
    extract = bool(re.match(r'^[A-Za-z]*x', first)) and not first.startswith('-')       # tar xf a.tar
    dirs, k, state = _TarDirs(), 0, None
    stdout = extract and 'O' in first
    if extract:
        k, state = _tar_old(args, dirs)
    while k < len(args):
        t = args[k].text
        if t == '--' and state is None:
            break
        if t in ('--extract', '--get') or re.match(r'^-[A-Za-z]*x', t) and not t.startswith('--'):
            extract = True
        stdout = stdout or state is None and _tar_stdout(t)
        state = _tar_word(args, k, state, dirs)
        k += 1
    if not extract or stdout:
        return []
    if not dirs.seen:
        return [here(cmd_word)]
    return dirs.seen + ([here(cmd_word)] if dirs.unsure else [])


def _unzip(args, cmd_word):
    for k, w in enumerate(args):
        t = w.text
        if t == '-d' and k + 1 < len(args):
            return [args[k + 1]]
        if t.startswith('-') and not t.startswith('--') and re.search(r'[ltvZpz]', t[1:]):
            return []                           # lists, tests or prints to standard output
    return [here(cmd_word)]


def _copier(args):
    """robocopy src dst [files] [/opts]; xcopy src [dst] [/opts]: the destination Word."""
    pos = [w for w in args if not WIN_OPT_RE.match(w.text)]
    return pos[1:2]


def _evals(name, args, cmd_word):
    """node -e, ruby -e: code given on the line writes where it likes, as python -c does."""
    for w in args:
        if w.text in EVALS[name] or w.text.partition('=')[0] in EVALS[name]:
            return [here(cmd_word)]
        if not w.text.startswith('-'):
            return []                           # a script file
    return []


# python's short options that take a value: the rest of the word, or the next word (-c CODE,
# -cCODE, -Ic CODE); its long option that takes one
PY_SHORT_VALUE = 'cmWX'
PY_LONG_VALUE = {'--check-hash-based-pycs'}
# the py launcher's version choice (-3, -3.12, -3.12-64), taken before python's own options
PY_LAUNCHER_VER_RE = re.compile(r'^-\d+(\.\d+)*(-(32|64))?$')


def py_program(name, args):
    """(kind, value): how python reads its program from `args`. kind is 'code' (value the -c
    Word), 'module' (-m), 'stdin' (`-`), 'script' or 'none' (no program named: python reads
    standard input)."""
    k = 0
    if name == 'py' and args and PY_LAUNCHER_VER_RE.match(args[0].text):
        k = 1
    while k < len(args):
        w = args[k]
        t = w.text
        if t == '-':
            return 'stdin', None
        if t == '--':
            return ('script' if k + 1 < len(args) else 'none'), None
        if t.startswith('--'):
            k += 2 if t in PY_LONG_VALUE else 1
            continue
        if not t.startswith('-') or len(t) < 2:
            return 'script', None
        for j in range(1, len(t)):
            ch = t[j]
            if ch not in PY_SHORT_VALUE:
                continue
            if t[j + 1:]:
                value, k = sh.Word(t[j + 1:], w.dyn, w.start, w.end), k + 1
            else:
                value, k = (args[k + 1] if k + 1 < len(args) else None), k + 2
            if ch == 'c':
                return 'code', value
            if ch == 'm':
                return 'module', value
            break
        else:
            k += 1
    return 'none', None


def _python(cmd, name, args, cmd_word, via=None):
    return [here(cmd_word)] if python_unread(cmd, name, args, via) else []


PY_SP_WHY = 'Start-Process hands python a command line the guard does not read'
PY_PS_WHY = 'PowerShell may hand python other words than the guard read'
PY_UNREAD_WHY = 'the guard does not read what the code python is given writes'


def python_unread(cmd, name, args, via=None):
    """Why a python run counts as a write of the directory it runs in, or None when its own file
    decides what it writes (a script or module, or nothing to run)."""
    # Start-Process hands python one command line the guard re-split with its quotes taken out
    # (commands._split_list), so what python reads is not the words below
    if via == 'start-process':
        return PY_SP_WHY
    c = cmd
    while c is not None:                        # every shell on the way to python
        # a word PowerShell may hand on as other than one argument (dropped when empty, split at a
        # double quote): python's own words, or the text of a bash -c around it, are not the
        # ones read here
        if c.shell == sh.PS and any(ps_native_unsure(w) for w in c.words):
            return PY_PS_WHY
        c = c.nested_in
    kind, _ = py_program(name, args)
    if kind in ('code', 'stdin'):
        return PY_UNREAD_WHY                    # the guard does not read what the code writes
    if kind in ('module', 'script'):
        return None                             # its own file decides what it writes
    if cmd.piped_in or any(r.op in ('<<', '<<<', '<') for r in cmd.redirects):
        return PY_UNREAD_WHY                    # a script read from standard input
    return None


# The coarse rule for a PowerShell writer (ps_unread). Parameters whose value is no path: their one
# value is passed over when it holds no command (_ps_value_end).
PS_VALUE_PARAMS = frozenset(('value', 'encoding', 'filter', 'include', 'exclude', 'newname', 'inputobject',
                             'delimiter', 'itemtype', 'uri', 'body', 'headers'))
_PS_SQ = r"'(?:[^'‘’‚‛]|'')*'"
_PS_DQ_PLAIN = r'"[^"“”„`$]*"'
_PS_BARE = r'[^\s$(){}\[\]@`\'"“”„‘’‚‛,;|&<>#]+'
_PS_VAR = r'\$(?:\{[A-Za-z_]\w*(?::[A-Za-z_]\w*)?\}|[A-Za-z_]\w*(?::[A-Za-z_]\w*)?)'
_PS_DQ = r'"(?:[^"“”„`$]|`[^\r\n]|%s)*"' % _PS_VAR
_PS_END = r'(?=[\s;|)}]|$)'
# a native program's argument: plain words and strings with no variable
PS_PLAIN_ARG_RE = re.compile(r'(?:%s|%s|%s)+%s' % (_PS_BARE, _PS_SQ, _PS_DQ_PLAIN, _PS_END))
# a cmdlet's argument: a variable, or a string with one, is one argument too
PS_CMDLET_ARG_RE = re.compile(r'(?:%s|%s|%s|%s(?:\.[A-Za-z_]\w*)*)+%s' % (_PS_BARE, _PS_SQ, _PS_DQ, _PS_VAR, _PS_END))
_PS_CALL_ARG = r"\s*(?:%s|%s|-?\d+)\s*" % (_PS_SQ, _PS_VAR)
_PS_CHAIN = re.compile(r'(?:\.[A-Za-z_]\w*(?:\((?:%s(?:,%s)*)?\))?)*' % (_PS_CALL_ARG, _PS_CALL_ARG))
_PS_VALUE_ATOM = re.compile(r'%s|%s|%s|%s' % (_PS_VAR, _PS_SQ, _PS_DQ, _PS_BARE))
_PS_VALUE_END = re.compile(_PS_END)
# a here-string body line PowerShell may end it on: any quote of its kind then @, indented or not
_PS_HERE_CLOSE = {"'": re.compile("(?m)^\\s*['‘’‚‛]@"), '"': re.compile('(?m)^\\s*["“”„]@')}


def _ps_group_end(src, k):
    """The offset just past the ( ) that opens at src[k], or None when it holds what this does not
    follow (a comment, a here-string, a typographic quote) or does not close."""
    depth, n = 0, len(src)
    while k < n:
        c = src[k]
        if c == '`':
            k += 2
            continue
        if c in "'\"":
            if src[k - 1:k] == '@':
                return None
            k = _net_string_end(src, k)
            continue
        if c in '#“”„‘’‚‛':
            return None
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return k + 1
        k += 1
    return None


def _ps_value_end(cmd, p):
    """The offset just past the one value that starts at source offset p, when it holds no command
    and PowerShell passes it as one argument: a plain word or string, a variable, a ( ) the
    scanner's word spans too, each with member calls on plain arguments after it ($s.Trim(),
    (Get-Date).AddDays(1)), then a space or the end; else None."""
    src = cmd.source
    if src.startswith('(', p):
        e = _ps_group_end(src, p)
        if e is None or not any(w.start <= p and w.end == e for w in cmd.words if w.start is not None):
            return None
    elif src.startswith(("@'", '@"'), p):
        q = src[p + 1]
        w = next((w for w in cmd.words if w.start == p and w.end is not None and
                  src[w.end - 2:w.end] == q + '@' and src[p + 2:p + 4].lstrip('\r')[:1] == '\n'), None)
        if w is None:
            return None
        body = src[p + 2:w.end - 2]
        # a $( ) runs a command; a line PowerShell would close on (a typographic quote) is not
        # the end the scanner read
        if (q == '"' and '$(' in body) or _PS_HERE_CLOSE[q].search(body):
            return None
        e = w.end
    else:
        m = _PS_VALUE_ATOM.match(src, p)
        if not m:
            return None
        e = m.end()
    e = _PS_CHAIN.match(src, e).end()
    return e if _PS_VALUE_END.match(src, e) else None


def ps_unread(cmd, args, spec=None):
    """[Word]: for a PowerShell writer (a write cmdlet, an Export-*, robocopy or xcopy, curl, wget
    or tar), a target that is not literal and has no span when any argument is one the guard does
    not read as the scanner did, else []. Coarse by design, it does not depend on how the scanner
    read a ( or a { : the arguments of a cmdlet (`spec`) other than one value of a parameter in
    PS_VALUE_PARAMS must each be a plain word, string or variable (PS_CMDLET_ARG_RE), a native
    program's a plain word or string (PS_PLAIN_ARG_RE: PowerShell spreads a splat or an array into
    its arguments); none may follow a comma (an array binds to the parameter before it); and the
    command may be neither split nor followed by a ( ) or { } that no value accounts for."""
    stand_in = [sh.Word('(an argument the guard does not read)', True, cmd.end, cmd.end)]
    if cmd.split:
        return stand_in
    src = cmd.source
    spans = []                          # [start, end) of the values passed over
    if spec is not None:
        for k, w in enumerate(args):
            t = sh.ps_dashed(w.text)
            if w.start is None or cm.quoted(cmd, w) or not t.startswith('-') or len(t) < 2:
                continue
            body, colon, val = t[1:].partition(':')
            body = body.lower()
            names = spec.names()
            cands = [body] if body in names else [spec.aliases[body]] if body in spec.aliases \
                else [x for x in names if x.startswith(body)]
            if not cands or not all(c in PS_VALUE_PARAMS for c in cands) or not any(c in spec.values for c in cands):
                continue
            if colon and val:
                p = src.find(':', w.start) + 1
            elif k + 1 < len(args) and args[k + 1].start is not None:
                p = args[k + 1].start
            else:
                continue
            e = _ps_value_end(cmd, p)
            if e is not None:
                spans.append((p, e))
    arg_re = PS_CMDLET_ARG_RE if spec is not None else PS_PLAIN_ARG_RE
    first = next((w.start for w in args if w.start is not None), None)
    prev = None                         # the word before the first argument: the program's name
    if first is not None:
        prev = max((x for x in cmd.words if x.end is not None and x.end <= first),
                   key=lambda x: x.end, default=None)
    for w in args:
        if w.start is None or w.start == w.end:
            if w.dyn:
                return stand_in
            continue
        if prev is not None and prev.end is not None and ',' in src[prev.end:w.start]:
            return stand_in
        prev = w
        if any(s <= w.start and w.end <= e for s, e in spans):
            continue
        if sh.ps_dashed(w.text).startswith('-') and not cm.quoted(cmd, w) and spec is not None and \
                any(s == src.find(':', w.start) + 1 and w.start < s and w.end <= e for s, e in spans):
            continue                    # -Value:$v: the value passed over
        m = arg_re.match(src, w.start)
        if not m or m.end() != w.end:
            return stand_in
    if cmd.next_op in ('(', '{') and not any(s < cmd.end < e for s, e in spans):
        return stand_in
    return []


def write_targets(cmd, inv):
    """[Word]: what the command writes, by its redirections and the file commands the guard
    knows. A Word whose text is '.' stands for the directory the command runs in."""
    out = []
    for r in cmd.redirects:
        if not r.writes:
            continue
        t = r.target
        if t is None:
            if cmd.shell == sh.PS:      # `> <#c#>(...)`: a target the scanner did not read
                out.append(sh.Word('(a redirection target the guard does not read)', True, cmd.end, cmd.end))
            continue
        if t.text.lower() in NULL_TARGETS:
            continue
        if cmd.shell == sh.PS and not t.dyn and t.start is not None and t.start != t.end:
            # the coarse rule (ps_unread) for a redirection: a target that is not one plain word,
            # string or variable (`> `<newline>(...)`) is one the line computes
            m = PS_CMDLET_ARG_RE.match(cmd.source, t.start)
            if not m or m.end() != t.end:
                t = sh.Word(t.text, True, t.start, t.end)
        out.append(t)
    if net_mentioned(cmd):
        net, unread = net_writes(cmd)
        # a literal path is judged where it points, a relative one where the location puts it (and,
        # for an agent session, refused: net_process_dir), a computed one as a cmdlet's computed
        # target is; for any other the directory the command runs in stands in, as a path it
        # cannot place (NET_UNPLACED)
        out.extend(x.word for x in net if net_placed(x.word) or net_relative(x.word) or x.word.dyn)
        if unread or any(not (net_placed(x.word) or net_relative(x.word) or x.word.dyn) for x in net):
            out.append(here(cmd.words[0] if cmd.words else None))
    if inv is None:
        return out
    out.extend(inv.writes)
    name, args = inv.name, inv.args
    word = inv.word
    if inv.via == 'xargs':
        # xargs appends the names it reads: a stand-in name in the directory it runs in
        args = list(args) + [here(word)]
    if name in COPIERS:
        return out + _copier(args) + (ps_unread(cmd, args) if cmd.shell == sh.PS else [])
    if cmd.shell != sh.PS and name == 'find':
        if any(w.text == '-delete' for w in args):
            return out + (_find_paths(args)[0] or [here(word)])
        return out
    if cmd.shell == sh.PS and not inv.path.lower().endswith('.exe'):
        cmdlet = PS_ALIASES.get(name, name)
        if cmdlet in PS_CMDLETS and cmdlet != 'get-childitem':
            spec = PS_CMDLETS[cmdlet]
            bound = ps_args(cmd, args, cmdlet)
            # a provider-aware cmdlet's item on a drive that holds no files (env:FOO) is no file;
            # every other writer (a native program, a script function, Out-File) gets a file
            native = name not in PS_PROVIDER_CMDLETS or inv.via is not None
            for p in spec.writes:
                out.extend(x for x in bound.get(p, []) if native or not ps_provider_item(cmd, x))
            if spec.writes:
                out.extend(ps_unread(cmd, args, spec))
            piped = [g for g in spec.piped if cmd.piped_in and not any(bound.get(p) for p in g)]
            if piped:
                out.append(pipe_stand_in(cmd))
            if spec.here and not bound.get(spec.here) and not any(spec.here in g for g in piped):
                out.append(here(word))
            if cmdlet == 'new-item' and bound.get('name'):
                base = (bound.get('path') or [None])[0]
                for n in bound['name']:
                    x = sh.Word(ntpath.join(base.text, n.text) if base is not None else n.text,
                                n.dyn or (base is not None and base.dyn), n.start, n.end)
                    if native or not ps_provider_item(cmd, x):
                        out.append(x)
            if name not in ('curl', 'wget'):
                return out
        elif cmdlet.startswith('export-') and cmdlet not in PS_CMDLETS:
            return out + ps_export_targets(cmd, args) + ps_unread(cmd, args, PS_CMDLETS['export-clixml'])
    if cmd.shell == sh.CMD and name in CMD_WRITERS:
        pos = [w for w in args if not w.text.startswith('/')]
        if name == 'type':
            return out
        if name in ('copy', 'xcopy', 'robocopy', 'ren', 'rename'):
            return out + pos[-1:] if name != 'robocopy' else out + pos[1:2]
        return out + pos
    # a native writer PowerShell runs (curl and wget are also Invoke-WebRequest's aliases there)
    grouped = ps_unread(cmd, args) if cmd.shell == sh.PS and name in ('curl', 'wget', 'tar') else []
    if grouped and any(x.start == x.end == cmd.end and x.dyn for x in out):
        grouped = []                            # the cmdlet branch's stand-in already
    if name == 'curl':
        return out + _curl(args, word) + grouped
    if name == 'wget':
        return out + _wget(args, word) + grouped
    if cm.PYTHON_RE.match(name):
        return out + _python(cmd, name, args, word, inv.via)
    if name == 'ruby' and cmd.shell != sh.PS:
        return out + _ruby(args, word)
    if name in EVALS:
        return out + _evals(name, args, word)
    if name == 'tar':                           # Windows' own tar.exe runs from PowerShell too
        return out + _tar(args, word) + grouped
    if cmd.shell == sh.PS:
        return out
    if name == 'sed':
        return out + _sed(args)
    if name == 'perl':
        return out + _perl(args, word)
    if name in ('awk', 'gawk'):
        return out + _awk(args, word)
    if name in EDITORS:
        return out + _editor(name, args)
    if name == 'unzip':
        return out + _unzip(args, word)
    if name == 'dd':
        return out + [sh.Word(w.text[3:], w.dyn, w.start, w.end) for w in args if w.text.startswith('of=')]
    if name == 'patch':
        return out + _patch(args, word)
    if name == 'rsync':
        return out + _rsync(args)
    if name in BASH_WRITERS:
        if name in COPY_LONGS:
            return out + copy_writes(name, args, word)
        pos = [w for w in args if not (w.text.startswith('-') and len(w.text) > 1)]
        return out + pos
    return out


GLOB_CHARS = set('*?[]{}+:')                   # ':' a drive-qualified name (D:x is relative to D:'s folder)

# GNU coreutils cp, mv, ln and install: the long options each takes (getopt_long reads a unique
# prefix as the option; a name missing here reads as unknown, and an unknown option makes every
# operand count), those whose value may be the next word, the short options with a value, and the
# options a copy into an existing folder may carry for the guard to judge it by the sources' names
# (with any other option the folder itself is judged).
COPY_LONGS = {
    'cp': ('archive', 'attributes-only', 'backup', 'copy-contents', 'context', 'debug', 'dereference', 'force',
           'help', 'interactive', 'keep-directory-symlink', 'link', 'no-clobber', 'no-dereference',
           'no-preserve', 'no-target-directory', 'one-file-system', 'parents', 'preserve', 'recursive',
           'reflink', 'remove-destination', 'sparse', 'strip-trailing-slashes', 'suffix', 'symbolic-link',
           'target-directory', 'update', 'verbose', 'version'),
    'mv': ('backup', 'context', 'debug', 'exchange', 'force', 'help', 'interactive', 'no-clobber', 'no-copy',
           'no-target-directory', 'strip-trailing-slashes', 'suffix', 'target-directory', 'update', 'verbose',
           'version'),
    'ln': ('backup', 'directory', 'force', 'help', 'interactive', 'logical', 'no-dereference',
           'no-target-directory', 'physical', 'relative', 'suffix', 'symbolic', 'target-directory', 'verbose',
           'version'),
    'install': ('backup', 'compare', 'context', 'debug', 'directory', 'group', 'help', 'mode',
                'no-target-directory', 'owner', 'preserve-context', 'preserve-timestamps', 'strip',
                'strip-program', 'suffix', 'target-directory', 'verbose', 'version'),
}
COPY_VALUE_LONGS = {'suffix', 'target-directory', 'no-preserve', 'sparse', 'group', 'mode', 'owner', 'strip-program'}
COPY_VALUE_SHORTS = {'cp': 'St', 'mv': 'St', 'ln': 'St', 'install': 'Stmog'}
COPY_INTO_SHORTS = {'cp': set('rRapvnt'), 'mv': set('vnt'), 'ln': set('svt'), 'install': set('vmt')}
COPY_INTO_LONGS = {'cp': {'recursive', 'archive', 'verbose', 'no-clobber', 'target-directory'},
                   'mv': {'verbose', 'no-clobber', 'target-directory'},
                   'ln': {'symbolic', 'verbose', 'target-directory'},
                   'install': {'verbose', 'mode', 'target-directory'}}


class CopyArgs(object):
    """cp, mv, ln or install's arguments: `dirs` the -t/--target-directory values, `pos` the
    operands, `shorts` and `longs` the options (a long one by its full name), `unsure` an option
    the text does not name (not literal, or a long prefix that is unknown or ambiguous)."""

    def __init__(self, name, args):
        self.dirs, self.pos, self.shorts, self.longs, self.unsure = [], [], set(), set(), False
        names = COPY_LONGS[name]
        k = 0
        while k < len(args):
            w = args[k]
            t = w.text
            if t == '--':
                self.pos.extend(args[k + 1:])
                break
            if not (t.startswith('-') and len(t) > 1):
                self.pos.append(w)
                k += 1
                continue
            if w.dyn:
                self.unsure = True
                k += 1
                continue
            value = None
            if t.startswith('--'):
                body, eq, val = t[2:].partition('=')
                full = body if body in names else None
                if full is None:
                    cands = [n for n in names if n.startswith(body)]
                    full = cands[0] if len(cands) == 1 else None
                if full is None:
                    self.unsure = True
                    k += 1
                    continue
                self.longs.add(full)
                if eq:
                    value = sh.Word(val, w.dyn, w.start, w.end)
                elif full in COPY_VALUE_LONGS:
                    value = args[k + 1] if k + 1 < len(args) else None
                    k += 1
                if full == 'target-directory' and value is not None:
                    self.dirs.append(value)
                k += 1
                continue
            body = t[1:]
            for j, c in enumerate(body):
                self.shorts.add(c)
                if c in COPY_VALUE_SHORTS[name]:
                    rest = body[j + 1:]
                    if rest:
                        value = sh.Word(rest, w.dyn, w.start, w.end)
                    else:
                        value = args[k + 1] if k + 1 < len(args) else None
                        k += 1
                    if c == 't' and value is not None:
                        self.dirs.append(value)
                    break
            k += 1

    def no_target(self):
        return 'T' in self.shorts or 'no-target-directory' in self.longs

    def makes_dirs(self, name):
        return name == 'install' and ('d' in self.shorts or 'directory' in self.longs)


def copy_writes(name, args, word):
    """[Word]: what cp, mv, ln or install writes. An option the text does not name may shift
    the operands, so then every operand counts."""
    ca = CopyArgs(name, args)
    if ca.unsure or name == 'mv' or ca.makes_dirs(name):
        return ca.dirs + ca.pos                 # mv also removes its sources
    if ca.dirs:
        return ca.dirs
    if name == 'ln' and len(ca.pos) == 1 and not ca.no_target():
        return [here(word)]                     # ln <target>: a link in the directory it runs in
    return ca.pos[-1:]


def copy_into(cmd, inv):
    """(destination Word, [source Word], sources must be files) of a copy, move or link that,
    when the destination is an existing folder, writes <folder>/<each source's name>: cp, mv, ln
    and install with only the options COPY_INTO_SHORTS/LONGS allow, Copy-Item and Move-Item,
    cmd's copy and move (of a file: cmd copies a folder's files, not the folder). None for
    anything else, and then the folder itself is judged."""
    if inv is None or inv.via == 'xargs':
        return None
    name, args = inv.name, inv.args
    if cmd.shell == sh.PS and not inv.path.lower().endswith('.exe'):
        cmdlet = PS_ALIASES.get(name, name)
        if cmdlet not in ('copy-item', 'move-item'):
            return None
        bound = ps_args(cmd, args, cmdlet)
        dest = bound.get('destination') or []
        srcs = bound.get('path', []) + bound.get('literalpath', [])
        return (dest[0], srcs, False) if len(dest) == 1 else None
    if cmd.shell == sh.CMD and name in ('copy', 'move'):
        pos = [w for w in args if not w.text.startswith('/')]
        return (pos[-1], pos[:-1], name == 'copy') if len(pos) > 1 else None
    if cmd.shell == sh.PS or name not in COPY_LONGS:
        return None
    ca = CopyArgs(name, args)
    if ca.unsure or ca.no_target() or ca.makes_dirs(name) or len(ca.dirs) > 1 or \
            ca.shorts - COPY_INTO_SHORTS[name] or ca.longs - COPY_INTO_LONGS[name]:
        return None
    if ca.dirs:
        return ca.dirs[0], ca.pos, False
    if name == 'ln' and len(ca.pos) == 1:
        return here(inv.word), ca.pos, False
    return (ca.pos[-1], ca.pos[:-1], False) if len(ca.pos) > 1 else None


def into_folder(target, w, into, cwd, home=None):
    """[path]: what a write of `target` (the Word `w`) writes. When `into` (copy_into) names `w`
    as the destination and `target` is an existing folder, <target>/<each source's name>; a
    source the guard cannot name (not literal, a wildcard, a drive-qualified name, no name, or a
    folder where only a file is read so) keeps the folder itself. `~` is `home` (ctx.home)."""
    if into is None or not os.path.isdir(target):
        return [target]
    dest, srcs, files_only = into
    if (dest.start, dest.end, dest.text) != (w.start, w.end, w.text) or not srcs:
        return [target]
    out = []
    for s in srcs:
        if s.dyn:
            base = ''
        elif paths.posix():
            base = s.text.rstrip('/').split('/')[-1]    # Linux: `\` is a name's character
        else:
            base = re.split(r'[\\/]', s.text.rstrip('\\/'))[-1]
        if not base or base in ('.', '..') or GLOB_CHARS & set(base):
            return [target]
        if files_only:
            src = sh.resolve_path(s.text, cwd, home=home)
            if not src or not os.path.isfile(src):
                return [target]
        out.append(os.path.join(target, base))
    return out


# A PowerShell item on a built-in drive that holds no files: env:FOO, Env:\FOO, variable:x,
# function:f, alias:a, HKCU:\x, HKLM:/x, Cert:\x, WSMan:\x, or the provider spelled out
# (Environment::FOO, Microsoft.PowerShell.Core\Registry::HKEY_CURRENT_USER\x). Temp: and
# FileSystem:: hold files and are not here.
PS_PROVIDER_ITEM_RE = re.compile(
    r'(?i)^(?:(?:env|variable|function|alias|hkcu|hklm|cert|wsman):'
    r'|(?:microsoft\.(?:powershell\.(?:core|security)|wsman\.management)\\)?'
    r'(?:environment|variable|function|alias|registry|certificate|wsman)::)')
DOTDOT_RE = re.compile(r'(?:^|[\\/:])\.\.(?:[\\/]|$)')
# A line that may remap a drive name to a folder (New-PSDrive env FileSystem <folder>) or run code
# the text does not show: there env:x may be a file, so it is judged as one. Matched against the
# raw line and against each word as the parser decodes it (n`dr reads ndr, 'New-PSDr'+'ive' reads
# New-PSDr+ive).
PS_DRIVE_REMAP_RE = re.compile(r'(?im)drive|psdr|\b(?:ndr|rdr|mount|iex|ipmo)\b|invoke-expression|import-module'
                               r'|\.ps1\b|\.psm1\b|scriptblock|sessionstate|(?:^|[;&|({])\s*\.\s')
# Session objects, .NET types and static calls a line may reach a drive through
PS_DOTNET_RE = re.compile(r'(?im)\$executioncontext|\$host\b|new-object|add-type|^\s*using\b|[\])]\s*::')
# The provider-aware item and content cmdlets (PSCmdlet provider), as the line names them: the
# canonical name or one of PS_PROVIDER_ALIASES (any other alias is judged as a file write). Their
# item on a drive that holds no files is no file. Script functions (Compress-Archive, mkdir) and
# the file-only writers (Out-File, Tee-Object, Export-*, Start-Transcript, Invoke-WebRequest
# -OutFile) are not here and are judged as files.
PS_PROVIDER_ALIASES = ('ni', 'sc', 'ac', 'clc', 'ri', 'del', 'rni', 'mi', 'move', 'cpi', 'copy', 'sp')
PS_PROVIDER_CMDLETS = frozenset([n for n, s in PS_CMDLETS.items() if s.provider] +
                                [a for a in PS_PROVIDER_ALIASES if PS_CMDLETS[PS_ALIASES[a]].provider])
# Read-only cmdlets a line of provider items may also hold
PS_READ_CMDLETS = frozenset(('get-item', 'gi', 'get-content', 'gc', 'get-childitem', 'gci', 'test-path',
                             'get-location', 'gl', 'pwd', 'write-output', 'echo', 'write-host'))
# The one native program such a line may also run: git, named literally (git.exe cannot remap a
# PowerShell drive, and its words meet the git rules)
PS_PLAIN_GIT = ('git', 'git.exe')
# A word that only reads an environment variable, with a literal tail: $env:TEMP, "$env:TEMP\x.txt"
PS_ENV_READ_WORD_RE = re.compile(r'(?i)^\$(?:env:[A-Za-z_]\w*|\{env:[A-Za-z_]\w*\})(?:[\\/][^$`()\[\]{}]*)?$')


def ps_line_plain(cmd):
    """True when every command on the PowerShell line `cmd` comes from is a plain call: the parser
    reads the line cleanly; each command's first word is a literal name of a cmdlet in
    PS_PROVIDER_CMDLETS or PS_READ_CMDLETS, or git (PS_PLAIN_GIT) (no call or dot-source operator,
    no assignment or other expression statement, no computed name, no Start-Process); every word is
    literal or only reads an environment variable (PS_ENV_READ_WORD_RE; no other variable, member
    call or subexpression); and neither the raw line nor any decoded word matches
    PS_DRIVE_REMAP_RE, commands.PS_SCRIPT_TEXT_RE or PS_DOTNET_RE. Anything else may remap a drive.
    The line is read again with `~` as the home its own parse read (Command.home)."""
    src = cmd.source
    if any(r.search(src) for r in (PS_DRIVE_REMAP_RE, cm.PS_SCRIPT_TEXT_RE, PS_DOTNET_RE)):
        return False
    parsed = cm.read(src, sh.PS, cmd.cwd, home=cmd.home)
    if not parsed.clean:
        return False
    for c in parsed.commands:
        if not c.words:
            return False
        inv = cm.invocation(c)
        if inv is None or inv.dyn or inv.via is not None or inv.index != 0 or c.words[0].dyn:
            return False
        git = inv.name == 'git' and c.words[0].text.lower() in PS_PLAIN_GIT
        if not git and inv.name not in PS_PROVIDER_CMDLETS | PS_READ_CMDLETS:
            return False
        for w in c.words:
            if w.dyn and not PS_ENV_READ_WORD_RE.match(w.text) or any(r.search(w.text) for r in (PS_DRIVE_REMAP_RE, cm.PS_SCRIPT_TEXT_RE, PS_DOTNET_RE)):
                return False
    return True


def ps_provider_word(cmd, inv, w):
    """True when the Word `w`, written by the command `cmd` (invocation `inv`), is a literal item
    of a provider-aware cmdlet on a drive that holds no files (PS_PROVIDER_ITEM_RE, no `..` part),
    whatever the line around it: ps_provider_item without ps_line_plain. Its refusal says why
    (PROVIDER_ITEM_WHY) instead of naming a file to write."""
    return cmd.shell == sh.PS and inv is not None and inv.via is None and inv.name in PS_PROVIDER_CMDLETS and \
        not w.dyn and bool(PS_PROVIDER_ITEM_RE.match(w.text)) and not DOTDOT_RE.search(w.text)


def ps_provider_item(cmd, w):
    """True when the Word `w`, an item a cmdlet in PS_PROVIDER_CMDLETS writes (write_targets calls
    it only for those, never for a native program, curl, wget, Start-Process or another cmdlet),
    names an item on a built-in drive that holds no files (PS_PROVIDER_ITEM_RE): a literal word,
    no `..` part, on a line of plain calls (ps_line_plain). Writing such an item writes no file. A
    native program reads env:FOO as the stream FOO of a file named env: a file write."""
    if cmd.shell != sh.PS or w.dyn or not PS_PROVIDER_ITEM_RE.match(w.text) or DOTDOT_RE.search(w.text):
        return False
    return ps_line_plain(cmd)


# a PowerShell word that is the location and a literal rest: $PWD, "${PWD}\x", $pwd/x/y
PS_PWD_WORD_RE = re.compile(r'(?i)^(?:\$pwd|\$\{pwd\})((?:[\\/][^$`]*)?)$')
PS_BRACED_VAR_RE = re.compile(r'\$\{[^}]*\}')     # ${name}: a variable, no code


def ps_pwd_path(cmd, w):
    """The path text a PowerShell word spells as $PWD (or ${PWD}) and a literal rest, with the
    location as cmd.cwd, or None: the word is not one, the directory is unknown, or the write is
    not the line's only command. $PWD can be given another value under many spellings of its
    name (sv PWD, -OutVariable ('P'+'WD'), [scriptblock]::Create(...).Invoke()), and only code
    run before the target is read can do it: another command (a ; && || or newline, a pipeline,
    a script block or ( ) group around it) or an argument of its own that runs code (a ( ),
    $( ) or { } in a computed word). Each of those fails closed."""
    if cmd.shell != sh.PS or not w.dyn or cmd.cwd is None:
        return None
    m = PS_PWD_WORD_RE.match(w.text)
    if not m:
        return None
    src = cmd.source or ''
    if cmd.start is None or cmd.nested_in is not None or cmd.groups or cmd.subs or cmd.prev_op or cmd.next_op or \
            cmd.piped_in or cmd.piped_out or src[:cmd.start].strip() or src[cmd.end:].strip():
        return None                             # not the line's one command
    args = list(cmd.words) + [r.target for r in cmd.redirects if r.target is not None]
    if any(x is not w and x.dyn and re.search(r'[({]', PS_BRACED_VAR_RE.sub('', x.text)) for x in args):
        return None                             # an argument that runs code: (...), $(...), {...}
    return cmd.cwd + m.group(1)


BASH_DEVICE_RE = re.compile(r'^/dev/')         # /dev/stderr, /dev/tty, /dev/fd/3: no file
BASH_TMP_RE = re.compile(r'^/tmp(?:/|$)')        # Git Bash mounts /tmp on a temp folder: jail.bash_tmp


def py_unread(cmd, inv, w):
    """Why python's stand-in write `w` (_python's here()) is one (python_unread), or None for any
    other write: the reason a refusal gives, not a file it names. The verdict is unchanged."""
    if inv is None or not cm.PYTHON_RE.match(inv.name or '') or w.text != '.' or \
            cmd.source[w.start:w.end] == '.':
        return None
    return python_unread(cmd, inv.name, inv.args, inv.via) or PY_UNREAD_WHY


def unread_write(cmd, inv, w):
    """True when the write `w` stands for the directory a program runs in (here()) because the
    guard does not read what the program writes: python's code (py_unread), node -e, ruby -e or
    perl -e code, the names xargs reads, a .NET call's path. Such a write is unplaced(); a stand-in
    for a program that does write there (tar -x, curl -O, ln) is not."""
    if w.text != '.' or cmd.source[w.start:w.end] == '.':
        return False
    if py_unread(cmd, inv, w) is not None:
        return True
    if inv is not None and (inv.name in EVALS or inv.name == 'perl' or inv.via == 'xargs'):
        return True
    return net_stand_in(cmd, w)


def net_stand_in(cmd, w):
    """True when the write `w` is the stand-in write_targets gives a .NET write it cannot place
    (NET_UNPLACED): the directory the command runs in, at its first word."""
    return net_mentioned(cmd) and bool(cmd.words) and w.text == '.' and         (w.start, w.end) == (cmd.words[0].start, cmd.words[0].end) and cmd.source[w.start:w.end] != '.'


def shell_writes(event, ctx, cmd, inv, cwd, role):
    d = git_jail(event, ctx, cmd, inv, cwd, role)
    if d:
        return d
    net = {}
    if net_mentioned(cmd):
        net = dict(((x.word.start, x.word.end), x) for x in net_writes(cmd)[0])
    into = copy_into(cmd, inv)
    searched = False
    # A Start-Process program with a -WorkingDirectory runs there (commands.run_dir), while
    # PowerShell places the call's redirections and the -RedirectStandard* files from its own
    # location, the command's directory. The program's own targets are judged from both folders,
    # so a workdir never lets a write pass that the command's own directory refuses. git (and a
    # git-* program) gives none: the git rules judge its writes, from the workdir (commands.git_call).
    moved = len(write_targets(cmd, None)) + len(inv.writes) \
        if inv is not None and inv.via == 'start-process' and inv.workdir is not None else None
    asked = Asked()                             # every target's refusal wins over another's ask
    for k, w in enumerate(write_targets(cmd, inv)):
        if cmd.shell == sh.BASH and not w.dyn and BASH_DEVICE_RE.match(w.text):
            continue
        bases = (OUTER,) if moved is None or k < moved else (OUTER, cm.run_dir(inv))
        for base in bases:
            d, searched = _write_from(event, ctx, cmd, inv, cwd, role, w, base, net, into, searched)
            d = asked.firm(d)
            if d:
                return d
    return asked.first


def _write_from(event, ctx, cmd, inv, cwd, role, w, base, net, into, searched):
    """(Decision or None, searched) for the write target `w` of shell_writes read from `base`
    (OUTER, or the folder a Start-Process -WorkingDirectory names)."""
    runs_in = cmd.cwd if base is OUTER else base
    asked = Asked()                             # find's ask waits for this target's own refusal
    piped = pipe_stand_in_word(cmd, w)
    target, why = (None, PIPE_UNPLACED) if piped else write_place(ctx, cmd, w, base)
    if target is None and w.dyn and (w.start, w.end) in net:
        why = '%s; %s' % (why, NET_PROCESS_DIR)
    unread = py_unread(cmd, inv, w) or (NET_UNPLACED if net_stand_in(cmd, w) else None)
    # a kit tool acts on the checkout its -C names: that folder, not the one it runs in
    checked = kit_tool_dir(event, ctx, cmd, inv, cwd) if base is OUTER and unread == PY_PS_WHY else None
    if checked is not None:
        target, why = checked[1], None
    if target is None and not searched:
        searched = True                         # find's start paths answer for every such target at once
        d = asked.firm(find_exec_writes(event, ctx, cmd, cwd, role))
        if d:
            return d, searched
    computed = target is None and cmd.shell == sh.PS and w.dyn
    if computed:
        pass                                    # PowerShell's, from any location: one $p or (...) can name the main checkout (B15)
    elif target is None and runs_in is not None and \
            ctx.place(runs_in).kind not in (repo.MAIN, repo.UNKNOWN):
        # a target it cannot place, from a worktree or elsewhere: passes, but while a ledger key is
        # unread it can be the ledger
        return asked.first or ledger_unread(ctx, role, '`%s` writes %s' % (cmd.text, w.text)), searched
    what = '`%s` writes the path the pipeline gives it' % cmd.text if piped else \
        '`%s` writes %s' % (cmd.text, w.text if w.text != '.' else 'files in the directory it runs in') \
        if unread is None else \
        'the directory `%s` runs in (counted as written: %s)' % (cmd.text, unread) if checked is None else \
        'the checkout `%s` acts on, %s (counted as written: %s)' % (cmd.text, checked[0].text, unread)
    # here() or ps_unread(): no word to aim
    stand_in = (w.text == '.' and cmd.source[w.start:w.end] != '.') or w.start == w.end
    # a value its option's word also spells (of=, --output=, -tDIR) keeps that option before it
    rewrite = None if stand_in else word_rewrite(cmd, w)
    attached = attached_lead(cmd, w) if rewrite is not None and not isinstance(w, Under) else None
    if (w.start, w.end) in net:
        rewrite, attached = (lambda path, x=net[(w.start, w.end)]: net_retarget(
            cmd, x, path, lambda o: write_place(ctx, cmd, o, base)[0])), None
    if checked is not None:
        x = checked[0]
        # -C'dir' spelt other than its text: the value's span is the whole word, so the rewrite spells -C again
        lead = '' if any(a is x for a in inv.args) or cmd.source[x.start:x.end] == x.text else '-C '
        rewrite = (lambda path: retarget(cmd, x, path if path.startswith('<') else
                                         fwd(paths.norm(path, fold=False)), lead)) if x.start is not None else None
    if stand_in and base is not OUTER and base is not None:
        # the folder it runs in is the -WorkingDirectory value: aim that word instead
        rewrite = lambda path, x=inv.workdir: retarget(cmd, x, paths.norm(path, fold=False))
    placed = target is not None and not unread_write(cmd, inv, w)
    output = any(r.writes and r.target is not None and r.target.start is not None and
                 (r.target.start, r.target.end) == (w.start, w.end) for r in cmd.redirects) or (getattr(inv, 'name', None) or '').lower() in OUTPUT_WRITERS
    item = target is None and ps_provider_word(cmd, inv, w)
    for t in into_folder(target, w, into, runs_in, getattr(ctx, 'home', None)) if target else [target]:
        aim = rewrite
        if attached is not None and checked is None and t != target and rewrite is not None:
            # -tDIR names the folder: the same copy into the folder that holds the file it would write
            aim = lambda path, r=rewrite: r(fwd(path).rsplit('/', 1)[0] if '/' in fwd(path) else path)
        # a provider item's refusal runs the whole line from the worktree: a statement before the
        # call may set what it reads (PROVIDER_ITEM_WHY)
        d = edit_target(event, ctx, t, cwd, role, what, why, rewrite=aim,
                        run=whole_line(ctx, cmd) if item else bare(cmd), redirect=output,
                        shell=outer_shell(cmd), item=item,
                        stand_in=piped or w.start == w.end or unread_write(cmd, inv, w))
        if not placed:
            d = unplaced(d, role) or ledger_unread(ctx, role, what)   # a warned refusal leaves the ask
        d = asked.firm(d)
        if d:
            return d, searched
    # the relative path this write names, or a relative one the call only reads (Copy's source);
    # another relative path it writes is judged as its own target first (Move's into the main checkout)
    others = net[(w.start, w.end)].paths if (w.start, w.end) in net else []
    relative = [o.text for o in others if net_relative(o)]
    if target is not None and base is OUTER and others and \
            (net_relative(w) or any(net_relative(o) and (o.start, o.end) not in net for o in others)):
        d = asked.firm(net_process_dir(event, ctx, role, what, relative, target, rewrite))
        if d:
            return d, searched
    # A computed PowerShell target, or a path read from a directory the guard cannot tell (after a
    # `cd $x`): for a role main_checkout lets through, the jail cannot judge it either. A .NET
    # call's path is refused under write-jail, not unplaced (ps_drive_jail).
    if computed or (target is None and runs_in is None):
        d = ps_drive_jail(event, ctx, cwd, role, what, why, rewrite, net=(w.start, w.end) in net,
                          here=runs_in)
        if d:
            return d, searched
    return asked.first, searched


OUTER = object()                                # write_place: the command's own directory


def write_place(ctx, cmd, w, base=OUTER):
    """(path, why) of the Word `w` the command `cmd` writes, read from cmd's directory and shell:
    path None when the guard cannot place it (why says why), a PowerShell drive or provider path
    shell.resolve_path cannot place among them (docs/guard-v2.md B16). `base`, when given, is the
    folder a relative `w` is read from instead of cmd's directory: the -WorkingDirectory a
    Start-Process program runs in (commands.run_dir), None when the guard cannot place that."""
    here_dir = cmd.cwd if base is OUTER else base
    pwd = ps_pwd_path(cmd, w)
    if pwd is not None:
        target, why = sh.resolve_path(pwd, cmd.cwd, cmd.shell, getattr(ctx, 'home', None)), None
    elif w.dyn:
        target, why = None, 'a target that is not literal: %s' % w.text
    elif cmd.shell == sh.BASH and BASH_TMP_RE.match(w.text) and not paths.posix():
        target, why = paths.norm(os.path.join(jail.bash_tmp(ctx), w.text[5:]), fold=False), None
    else:
        target = sh.resolve_path(w.text, here_dir, cmd.shell, getattr(ctx, 'home', None))
        why = None if target else 'the directory it runs in is unknown' if here_dir is None \
            else 'a path the text cannot place: %s' % w.text
        if target is None and base is None:
            why = 'the folder its Start-Process -WorkingDirectory names is computed or not one the guard can place'
    return target, why


def find_runs(cmd):
    """[(find command, its invocation)]: each bash find whose -exec, -execdir, -ok or -okdir runs
    `cmd`, nearest first, through any shell nested between (commands.expand nests only those
    words under a find)."""
    out, outer = [], cmd.nested_in
    while outer is not None:
        if outer.shell == sh.BASH:
            inv = cm.invocation(outer)
            if inv is not None and not inv.dyn and inv.name == 'find':
                out.append((outer, inv))
        outer = outer.nested_in
    return out


def find_exec_writes(event, ctx, cmd, cwd, role):
    """A write the guard cannot place by a command a bash find runs (find_runs): the found path
    filled into {}, a path the line computes ("$1" in a shell given {}), or a relative path in a
    found file's folder (-execdir). It is judged as each such find's start paths, or the directory
    find runs in when it names none, as -delete is (write_targets): every path find hands the
    command is under them. A start path the guard cannot place passes from a worktree, as any
    target does (shell_writes), unless a ledger key is unread (ledger_unread). An ask waits for
    every start path's refusal (Asked)."""
    asked = Asked()
    for f, inv in find_runs(cmd):
        for w in _find_paths(inv.args)[0] or [here(inv.word)]:
            if not w.dyn and BASH_DEVICE_RE.match(w.text):
                continue
            target, why = write_place(ctx, f, w)
            if target is None and f.cwd is not None and ctx.place(f.cwd).kind not in (repo.MAIN, repo.UNKNOWN):
                d = asked.firm(ledger_unread(ctx, role, '`%s` runs `%s` on what it finds in %s'
                                             % (bare(f), bare(cmd), w.text)))
                if d:
                    return d                    # a subagent's refusal
                continue
            what = '`%s` runs `%s` on what it finds in %s' % (
                bare(f), bare(cmd), w.text if w.text != '.' else 'the directory it runs in')
            stand_in = (w.text == '.' and f.source[w.start:w.end] != '.') or w.start == w.end
            part = any((x.start, x.end) == (w.start, w.end) and x.text != w.text for x in f.words)
            swaps = execdir_swaps(f, cmd)
            if swaps:
                what += ' (with -exec in place of -execdir, find hands it the same files by their full paths)'
            rewrite = None if stand_in or part else \
                (lambda path, w=w, f=f, s=swaps: retarget(f, w, path, swaps=s))
            d = edit_target(event, ctx, target, cwd, role, what, why, rewrite=rewrite, run=bare(f),
                            shell=outer_shell(f))
            if target is None:
                d = unplaced(d, role) or ledger_unread(ctx, role, what)   # a warned refusal leaves the ask
            d = asked.firm(d)
            if d:
                return d
    return asked.first


def execdir_swaps(f, cmd):
    """[(word, '-exec' or '-ok')] for the one -execdir or -okdir of the find command `f` when it runs
    `cmd` and `cmd` takes only options and {}: -exec runs it on the same files, named by full path,
    which the guard can place; [] otherwise (a relative name the command writes would land
    elsewhere)."""
    dirs = [i for i, x in enumerate(f.words) if not x.dyn and x.text in ('-execdir', '-okdir')]
    words = [x.text for x in cmd.words]
    # find's {} is the one word computed at run time (the path it fills in)
    if len(dirs) != 1 or len(words) < 2 or any(x.dyn and x.text != '{}' for x in cmd.words):
        return []
    if not all(t == '{}' or t.startswith('-') for t in words[1:]) or '{}' not in words[1:]:
        return []
    i = dirs[0]
    x = f.words[i]
    # the words after it are the command find runs, as `cmd` spells them (a nested command has its own source)
    if x.start is None or [y.text for y in f.words[i + 1:i + 1 + len(words)]] != words:
        return []
    return [(x, x.text.replace('dir', ''))]


def ps_drive_jail(event, ctx, cwd, role, what, why, rewrite, net=False, here=None):
    """An agent session's PowerShell write to a computed target ($p, a ( ) expression: _write_from),
    or any shell's write of a path read from a directory the guard cannot tell (after `cd $x`),
    by a role main_checkout lets through (it may edit a main checkout): the jail cannot judge a
    place it cannot tell, so it refuses, as git_jail_unknown does for git. With `net`, the path a
    .NET call writes: refused under write-jail, not unplaced (whose default mode only warns), as
    net_process_dir refuses a literal relative one, since .NET may read it against the process's
    directory (a docs-writer's `cd <worktree>; $p='docs\\guard.md'; ...;
    [IO.File]::WriteAllText($p,$t)` wrote the main checkout under a warning). Its alternative
    aims at `here`, the PowerShell location, when that is a linked worktree the session may write
    (the session's own folder may be the main checkout it cd'd out of)."""
    if not agent_session(event, ctx):
        return None
    rule = 'write-jail' if net else UNPLACED
    rs = jail_roots(event, ctx, cwd)
    dest = jail.destination(rs)
    there = dest.path if dest is not None else None
    if net and here is not None and ctx.place(here).kind == repo.LINKED and jail.judge(here, rs)[0] is None:
        there = here
    head = '%s: the guard cannot tell where it writes (%s). Write to a literal path inside the folders an ' \
           'agent session may write%s.' % (what, why, '; an agent session gives .NET a literal absolute path'
                                          if net else '')
    if there is not None and rewrite is not None:
        return refuse(rule, role, head + ' The same write in %s:' % fwd(there), rewrite(fwd(there) + '/<file>'))
    return refuse(rule, role, head + ' To see where this session runs:', 'pwd')


DUPS_RE = re.compile(r'(?:[ \t]*[0-9]*[<>]&[0-9-]+)+')


def retarget(cmd, w, path, lead='', swaps=()):
    """The command `cmd` (from its program word, redirections kept) with the word `w` replaced
    by `path`, quoted so both shells read it literally (literal_word: a `::$DATA` stream name is
    not expanded), after `lead` (an option the word's span also covers, as -C'dir' does); each
    (word, text) of `swaps` replaces another word of the line as well."""
    start = cmd.words[0].start if cmd.words else w.start
    start = min(start, w.start)
    end = max(cmd.end, w.end)
    m = DUPS_RE.match(cmd.source, end)          # a dup (2>&1) has no word, so no span
    end = m.end() if m else end
    out, at = [], start
    for x0, x1, text in sorted([(w.start, w.end, lead + literal_word(path))] +
                               [(x.start, x.end, t) for x, t in swaps]):
        out.append(cmd.source[at:x0] + text)
        at = x1
    return (''.join(out) + cmd.source[at:end]).strip()


def attached_lead(cmd, w):
    """The option text the word holding the value Word `w` also spells before it (`of=`,
    `--output=`, `-d`, `-t`), or None when `w` is a word of its own or not such a value."""
    for x in cmd.words:
        if (x.start, x.end) != (w.start, w.end) or x.text == w.text:
            continue
        if w.dyn or x.dyn or len(x.text) <= len(w.text) or not x.text.endswith(w.text):
            return None
        lead = x.text[:-len(w.text)]
        return lead if ATTACHED_LEAD_RE.match(lead) else None
    return None


# an option an attached value follows: -d, -sSLo, --output=, of=
ATTACHED_LEAD_RE = re.compile(r'^(?:--?[A-Za-z][\w-]*=?|[A-Za-z]+=)$')


def word_rewrite(cmd, w):
    """rewrite(path) for the write target Word `w` of `cmd`: the word aimed at `path`, an attached
    value after its option (attached_lead), a file curl or ruby places under a folder (Under) by
    aiming that folder; None when no word of the line can be aimed."""
    if isinstance(w, Under):
        aim = word_rewrite(cmd, w.folder) if w.folder.start is not None and not w.folder.dyn else None
        if aim is None:
            return None
        name = fwd(w.name.text)

        def under(path):
            p = fwd(path)
            # the folder that keeps the name as written; a destination named by its last part only: its folder
            p = p[:-len(name) - 1] if p.endswith('/' + name) else p.rsplit('/', 1)[0]
            return aim(p)
        return under
    if not any((x.start, x.end) == (w.start, w.end) and x.text != w.text for x in cmd.words):
        return lambda path: retarget(cmd, w, path)
    lead = attached_lead(cmd, w)
    if lead is None:
        return None
    return lambda path: retarget(cmd, w, path, lead)


# ---------------------------------------------------------------- git

def _short_flags(args):
    out = set()
    for w in args:
        t = w.text
        if t.startswith('-') and not t.startswith('--') and len(t) > 1:
            out.update(t[1:])
    return out


def git_alias(ctx, cmd, gc, cwd, role, event=None):
    name, val = gc.alias
    rest = ' '.join(w.text for w in gc.rest)
    if val.startswith('!'):
        how, instead = 'Run the command itself:', ('%s %s' % (val[1:], rest)).strip()
        instead = alias_heavy_run(ctx, cmd, cwd, instead) or instead
    else:
        how, instead = 'Run the git command itself:', ('git %s %s' % (val if gc.sub == name else gc.sub, rest)).strip()
        # the command git runs makes a commit, in an agent session's worktree: plain git there is
        # refused for its missing trailer (git_plain_commit), so the advice is that rule's kit tool
        place = ctx.place(cmd.cwd or cwd) if (cmd.cwd or cwd) and event is not None and \
            agent_session(event, ctx) else None
        alt = commit_alternative(ctx, inline_alias(gc), place.top) \
            if place is not None and place.kind == repo.LINKED and place.top else None
        if alt is not None:
            how, instead = 'Run the git command itself, which makes a commit: %s' % alt[0][0].lower() + alt[0][1:], alt[1]
    return refuse('git-alias', role, '`%s` defines a git alias on the command line (alias.%s=%s), which hides '
                  'the command git runs. %s' % (bare(cmd), name, val, how), instead)


def alias_heavy_run(ctx, cmd, cwd, text):
    """The machine_lock run line for a shell alias's command `text` when it is one heavy program
    (heavy) and the session holds no machine lock, so the alternative git_alias prints is not
    refused as heavy in turn; else None."""
    where = cmd.cwd or cwd
    try:
        parsed = cm.read(text, cmd.shell, where, home=getattr(ctx, 'home', None))
    except Exception:  # noqa: BLE001 - an alternative's text only: the plain one stands
        return None
    if not parsed.clean or len(parsed.commands) != 1:
        return None
    inv = cm.invocation(parsed.commands[0])
    if inv is None or inv.dyn:
        return None
    cfg = ctx.config(where) if where else None
    entry = kconfig.heavy_match(inv.path, inv.argv, kconfig.heavy_programs(cfg))
    if not entry or ctx.lock_held()[0]:
        return None
    return lock_alternative(ctx, cmd, entry, cwd, text)


def git_rules(event, ctx, cmd, inv, cwd, role):
    d = git_send_pack_pmdata(event, ctx, inv, role) or call_subexpr_pmdata(event, ctx, cmd, inv, cwd, role)
    if d:
        return d
    gc = cm.git_call(inv)
    if gc is None:
        return None
    if gc.alias:
        d = git_alias(ctx, cmd, gc, cwd, role, event)
        if d:
            return d                            # a warning by default: the command git runs is judged below
        gc = inline_alias(gc)
    if gc.sub_dyn or (gc.sub == '' and gc.inv is not None and gc.inv.via == 'xargs'):
        d = git_push_pmdata(event, ctx, gc, role)       # a computed subcommand may be push
        if d:
            return d
    d = git_remote_rewrite(event, ctx, gc, role)
    if d:
        return d
    sub, args = gc.sub, gc.rest
    text = bare(cmd)
    if sub == 'worktree':
        # from any place: remove removes another tree, add -B or -f writes a shared branch
        return git_worktree_remove(ctx, gc, role, text) or git_shared_ref_forms(event, ctx, cmd, gc, cwd, role)
    if sub == 'push':
        # git_push's refusal first, so git-shared-ref in warn mode never hides it
        return git_push(event, ctx, gc, role) or git_shared_push(event, ctx, cmd, gc, cwd, role)
    if sub in SHARED_REF_FORMS:
        d = git_shared_ref_forms(event, ctx, cmd, gc, cwd, role)    # from any place, as update-ref
        if d:
            return d
    if sub == 'branch':
        d = git_branch_delete(ctx, gc, role) or git_shared_ref_forms(event, ctx, cmd, gc, cwd, role)
        if d:
            return d
    if sub == 'update-ref':
        d = git_shared_ref(event, ctx, cmd, gc, cwd, role)    # from any place: every worktree shares refs
        if d:
            return d
    placed = gc.dir and not gc.unknown
    place = ctx.place(gc.dir) if placed else \
        repo.Place(repo.UNKNOWN, None, 'the directory git runs in is unknown', None, None)
    if place.kind == repo.LINKED and sub == 'stash':
        return git_stash_shared(event, ctx, cmd, gc, role, place)
    if place.kind not in (repo.MAIN, repo.UNKNOWN):
        return None
    d = git_main_rules(event, ctx, cmd, inv, gc, cwd, role, place)
    if place.kind == repo.UNKNOWN and not gc.blind and not (placed and in_git_dir(gc.dir)):
        return unplaced(d, role)                # a computed -C, or a folder git cannot tell
    # gc.blind (GIT_DIR and the like, --git-dir, --work-tree, core.worktree) re-points git on
    # purpose, at a place the line nearly always names literally: its refusal stands
    return d


# git verbs that make a commit, each refused for an agent session in a linked worktree
# (git_plain_commit): plain git writes no Co-Authored-By trailer, so the gate's trailer check fails
# the branch. Known limits, none read here:
# - a git alias from git's config files (an inline -c alias.<name> is expanded);
# - a verb the shell computes or a script runs;
# - commit-tree and rebase;
# - a computed -C, --git-dir or --work-tree: no place is known, so the unplaced handling and
#   git-main-history judge the line, not this rule;
# - a --continue of a revert or cherry-pick sequence started with --no-commit makes no commit, but
#   it is refused like any other: git keeps that mode in the sequence's state, which is not read;
# - a word the shell computes after a hold (`git cherry-pick -n $X`) is read as a commit, though
#   it may be an option that undoes the hold (`--commit`): a computed commit is the common case,
#   so failing closed there would refuse honest lines;
# - commit.py commits only the change in progress of a revert, cherry-pick or am sequence: it does
#   not go on with the commits or patches left, so the refusal says to end the sequence (--quit).
COMMIT_VERBS = ('commit', 'merge', 'revert', 'cherry-pick', 'am', 'pull')
# the words after which a verb makes no commit, spelled exactly
COMMIT_STOPS = {'commit': ('--dry-run',), 'merge': ('--abort', '--quit'), 'revert': ('--abort', '--quit'),
                'cherry-pick': ('--abort', '--quit'),
                'am': ('--abort', '--quit', '--show-current-patch', '--show-current-patch=diff',
                       '--show-current-patch=raw')}
# the options, spelled exactly, that leave the verb's change uncommitted; an abbreviation is none
# (revert's and cherry-pick's -n is read as --no-commit)
COMMIT_HOLDS = {'merge': ('--no-commit', '--squash', '--ff-only'), 'pull': ('--no-commit', '--squash', '--ff-only'),
                'revert': ('--no-commit',), 'cherry-pick': ('--no-commit',)}
# the long options that undo a hold (a prefix of one counts): the last of --commit and --no-commit
# (or -n) wins, of --squash and --no-squash, and of --ff, --no-ff and --ff-only
COMMIT_UNDOES = {'commit': '--no-commit', 'no-squash': '--squash', 'ff': '--ff-only', 'no-ff': '--ff-only'}
# the words that go on with a sequence: the commit is made by commit.py, not by the verb again
COMMIT_STEPS = ('--continue', '--skip', '--resolved')
# the short options that take a value (attached, or the next word), per verb (git 2.5x)
COMMIT_SHORT_VALUES = {'commit': 'mFcCt', 'merge': 'mFsX', 'pull': 'sXj', 'revert': 'mX', 'cherry-pick': 'mX',
                       'am': 'pC'}
# the short options whose value, if any, is attached only (-S<key>, commit's -u<mode>): each ends
# its cluster and takes no next word
COMMIT_SHORT_ATTACHED = {'commit': 'Su', 'merge': 'S', 'pull': 'S', 'revert': 'S', 'cherry-pick': 'S', 'am': 'S'}
# the short options that take no value, per verb: any other letter may take the rest of its cluster
# or the next word, so a hold after it is not counted (commit_scan)
COMMIT_SHORT_FLAGS = {'commit': 'apsnevqzio', 'merge': 'nqve', 'pull': 'nqvrfkpa46t', 'revert': 'ens',
                      'cherry-pick': 'enxs', 'am': 'skcq3imunrb'}
# the long options that take a value in the next word when no `=` gives it
COMMIT_LONG_VALUES = {'message', 'file', 'strategy', 'strategy-option', 'cleanup', 'into-name', 'mainline',
                      'depth', 'deepen', 'shallow-since', 'shallow-exclude', 'upload-pack', 'jobs',
                      'negotiation-tip', 'server-option', 'refmap', 'reuse-message', 'reedit-message',
                      'fixup', 'squash', 'author', 'date', 'template', 'trailer', 'pathspec-from-file',
                      'directory', 'exclude', 'include', 'patch-format', 'quoted-cr', 'whitespace', 'resolvemsg',
                      'empty'}
# the options of a revert or cherry-pick that its --no-commit form keeps (commit_scan's `keep`)
COMMIT_KEPT_LONG = ('strategy', 'strategy-option')
# the long options, spelled in full, that take no next word; any other long option without `=`
# (one git does not know, or an abbreviation, which may be of one that takes a value) may take the
# next word, so a hold after it is not counted (commit_scan)
COMMIT_LONG_FLAGS = {
    'commit', 'no-commit', 'squash', 'no-squash', 'ff', 'no-ff', 'ff-only', 'edit', 'no-edit', 'signoff',
    'no-signoff', 'stat', 'no-stat', 'summary', 'no-summary', 'log', 'no-log', 'verbose', 'quiet', 'progress',
    'no-progress', 'verify', 'no-verify', 'verify-signatures', 'no-verify-signatures', 'gpg-sign', 'no-gpg-sign',
    'allow-unrelated-histories', 'autostash', 'no-autostash', 'rerere-autoupdate', 'no-rerere-autoupdate',
    'overwrite-ignore', 'no-overwrite-ignore', 'rebase', 'no-rebase', 'all', 'append', 'force', 'keep', 'prune',
    'tags', 'no-tags', 'unshallow', 'update-shallow', 'ipv4', 'ipv6', 'recurse-submodules',
    'no-recurse-submodules', 'allow-empty', 'allow-empty-message', 'keep-redundant-commits', 'no-reference',
    'amend', 'no-post-rewrite', 'reset-author', 'short', 'branch', 'porcelain', 'long', 'null', 'only',
    'interactive', 'patch', 'status', 'no-status', '3way', 'no-3way', 'keep-cr', 'no-keep-cr', 'scissors',
    'no-scissors', 'utf8', 'no-utf8', 'keep-non-patch', 'ignore-space-change', 'ignore-whitespace',
    'committer-date-is-author-date', 'ignore-date', 'message-id', 'no-message-id', 'reject', 'retry', 'binary'}


def commit_scan(sub, args):
    """(makes, revs, mainline, keep): whether git `sub` (COMMIT_VERBS) with the Words `args` after
    it makes a commit, the words that are neither options nor their values (a revert's or
    cherry-pick's commits; None after an option the scan cannot place that may take a value, so a
    word after it may be that value),
    the -m/--mainline value given, or None, and the strategy options of a revert or cherry-pick
    (-X, COMMIT_KEPT_LONG) its --no-commit form keeps, as kept_word's (lead, sep, value, dyn). Read left to right as git
    reads them: a stop word (COMMIT_STOPS) makes none; a hold (COMMIT_HOLDS, or -n in a short
    cluster of revert or cherry-pick; merge's and pull's -n is --no-stat) holds until a long option
    that undoes it (COMMIT_UNDOES), or a prefix of one, comes after it; a sequence's next step
    (COMMIT_STEPS) commits. Option values are skipped, so `-m --no-commit` is a message; `--` ends
    the options. It fails closed: after a word it cannot place (a computed word, a long option
    without `=` that COMMIT_LONG_FLAGS and COMMIT_LONG_VALUES do not name, or a short letter
    COMMIT_SHORT_FLAGS does not), no later hold or stop word counts, since that word may have taken
    it as its value."""
    stops, holds = COMMIT_STOPS.get(sub, ()), COMMIT_HOLDS.get(sub, ())
    values, attached = COMMIT_SHORT_VALUES.get(sub, ''), COMMIT_SHORT_ATTACHED.get(sub, '')
    flags = COMMIT_SHORT_FLAGS.get(sub, '')
    held, revs, mainline, unsure, keep, unplaced = set(), [], None, False, [], False
    k = 0
    while k < len(args):
        w = args[k]
        t = w.text
        k += 1
        if w.dyn or not t.startswith('-') or t == '-':
            revs.append(t)
            unsure = unsure or w.dyn
            continue
        if t == '--':
            revs.extend(x.text for x in args[k:])
            break
        if t.startswith('--'):
            body, eq, val = t[2:].partition('=')
            if t in stops and not unsure:
                return False, None if unplaced else revs, mainline, keep
            if t in COMMIT_STEPS:
                return True, None if unplaced else revs, mainline, keep
            if t in holds:
                if not unsure:
                    held.add(t)
                continue
            if len(body) >= 2:
                held -= {h for n, h in COMMIT_UNDOES.items() if n.startswith(body)}
            kept = body in COMMIT_KEPT_LONG and sub in ('revert', 'cherry-pick')
            if eq:
                if body == 'mainline':
                    mainline = val
                if kept:
                    keep.append(('--%s=' % body, '', val, False))
                continue
            if body in COMMIT_LONG_VALUES:
                if body == 'mainline' and k < len(args):
                    mainline = args[k].text
                if kept and k < len(args):
                    keep.append((t, ' ', args[k].text, args[k].dyn))
                k += 1
            elif body not in COMMIT_LONG_FLAGS:
                unsure = True
                # an abbreviation of flags alone takes no value, so the words after it stay commits
                unplaced = unplaced or any(n.startswith(body) for n in COMMIT_LONG_VALUES) or \
                    not any(n.startswith(body) for n in COMMIT_LONG_FLAGS)
            continue
        for j, c in enumerate(t[1:]):
            if c in values:
                rest, dyn = t[j + 2:], False
                if not rest:
                    rest, dyn = (args[k].text, args[k].dyn) if k < len(args) else ('', False)
                    k += 1
                if c == 'm' and sub in ('revert', 'cherry-pick'):
                    mainline = rest
                if c == 'X' and sub in ('revert', 'cherry-pick'):
                    keep.append(('-X', '', rest, dyn))
                break
            if c in attached:
                break
            if c not in flags:
                unsure = unplaced = True
                break
            if c == 'n' and sub in ('revert', 'cherry-pick') and not unsure:
                held.add('--no-commit')
    return sub in ('commit', 'am') or not held, None if unplaced else revs, mainline, keep


def git_plain_commit(event, ctx, cmd, inv, cwd, role):
    """A git verb that makes a commit (COMMIT_VERBS, commit_scan), run by an agent session
    (agent_session) in a linked worktree: plain git writes no Co-Authored-By trailer, so the gate's
    trailer check fails the branch. It runs after every other shell rule, so a refusal another
    rule owns (owner-identity, write-jail and the rest) stands. The alternative is the kit tool
    that does the job with the trailer: commit.py for a commit or a sequence's next step,
    sync_base.py for a merge or pull, the verb's --no-commit form (its -m, -X and --strategy kept) and then commit.py
    for a revert or cherry-pick, git apply --index and then commit.py for am. A --skip drops the
    change in progress and goes on with plain commits: the alternative ends the sequence, drops the
    change and takes what is left one at a time."""
    gc = git_call_judged(inv)
    if gc is None or gc.sub not in COMMIT_VERBS or not gc.dir or gc.unknown or gc.blind:
        return None
    if not agent_session(event, ctx):
        return None
    place = ctx.place(gc.dir)
    if place.kind != repo.LINKED or not place.top:
        return None
    alt = commit_alternative(ctx, gc, place.top)
    if alt is None:
        return None
    why = '`%s` makes a commit with plain git, which writes no Co-Authored-By trailer: the gate\'s trailer check ' \
          'then fails the branch. ' % bare(cmd)
    return refuse('git-plain-commit', role, why + alt[0], alt[1])


# a commit hash as typed: 7 to 40 hex digits (git's default abbreviation up to a full SHA-1)
TYPED_HASH_RE = re.compile(r'[0-9a-fA-F]{7,40}')


def typed_hash(revs):
    """The one commit hash among a merge's or pull's words `revs` (commit_scan), which sync_base.py
    then merges; `<hash>` when there is none, more than one, or the words could not be placed
    (None). A branch name (origin/main) is never resolved: the coordinator names the hash."""
    found = [r for r in revs or () if TYPED_HASH_RE.fullmatch(r)]
    return found[0] if len(found) == 1 else '<hash>'


def commit_alternative(ctx, gc, where):
    """(advice, command) for git `gc` when it makes a commit (COMMIT_VERBS, commit_scan), run in
    the linked worktree `where`: the kit tool that does the job with the trailer (git_plain_commit
    names the forms); None when it makes no commit."""
    if gc.sub not in COMMIT_VERBS:
        return None
    sub = gc.sub
    makes, revs, mainline, kept = commit_scan(sub, gc.rest)
    if not makes:
        return None
    top = quote(where)
    commit = 'python %s -C %s -m "<subject>" --model "<your model>" <each path you changed>' % (
        kit_tool(ctx, 'commit'), top)
    words = {w.text for w in gc.rest}
    one = 'git -C %s apply --index <patch>' % top if sub == 'am' else \
        'git -C %s %s --no-commit <commit>' % (top, sub)
    # --quit forgets the commits or patches still to come: the advice says where to read them first
    left = 'the patches from the one in progress on are in the folder git -C %s rev-parse --git-path ' \
        'rebase-apply names' % top if sub == 'am' else \
        'the commits still to come are listed in the file git -C %s rev-parse --git-path sequencer/todo ' \
        'names' % top
    note = ' --quit forgets what is left, so read it first: %s.' % left
    if sub in ('revert', 'cherry-pick', 'am') and '--skip' in words:
        return ('--skip drops the change in progress and goes on with the rest as plain commits.%s End the sequence '
                'here, then in their own calls drop the change in progress with git -C %s reset --merge and take '
                'each one left with %s, then %s:' % (note, top, one, commit),
                'git -C %s %s --quit' % (top, sub))
    if sub == 'commit' or words & set(COMMIT_STEPS) or (sub == 'am' and '-r' in words):
        rest = ''
        if sub in ('revert', 'cherry-pick', 'am'):
            rest = ' The kit tool commits only the change in progress: it does not go on with the rest of the ' \
                   'sequence. After it, end the sequence with git -C %s %s --quit and take each one left with %s, ' \
                   'then commit.py.%s' % (top, sub, one, note)
        return 'Commit with the kit tool, which stages the paths you name and adds the trailer.%s The commit:' % rest, \
            commit
    if sub in ('merge', 'pull'):
        return ('Sync with the commit your coordinator named; the tool merges exactly it and adds the trailer (to '
                'merge anything else, run git -C %s merge --no-commit <rev>, then in its own call %s):' % (top, commit),
                'python %s -C %s %s --model "<your model>"' % (kit_tool(ctx, 'sync_base'), top, typed_hash(revs)))
    if sub == 'am':
        return ('Apply the patch to the index, then in its own call commit it with %s:' % commit,
                'git -C %s apply --index <patch>' % top)
    keep = ''
    if mainline is not None:
        keep = ' -m %s' % (mainline if mainline.isdigit() else '<n>')
    keep += ''.join(' ' + kept_word(*w) for w in kept)
    return ('Run it with --no-commit, then in its own call commit what it changed with %s:' % commit,
            'git -C %s %s --no-commit%s %s' % (top, sub, keep, ' '.join(revs or ()) or '<commit>'))


# git that python or node code given on the line runs (interpreter_git): the guard reads git only
# as a typed shell command, so every git rule (commit.py's trailer, the shared refs, the main
# checkout) is blind to it. Known limits, none read here:
# - a script file (one in a temp or scratch folder too) and python -m: only code on the line
#   (-c, -e) or on standard input (a heredoc, a here-string, text piped in) is read;
# - a program or a git name the code builds at run time ('g' + 'it', importlib, getattr);
# - other interpreters (ruby, perl, deno) and compiled programs; PowerShell's Start-Process and
#   .NET's Process;
# - a library that writes the repository itself (dulwich, pygit2, open('.git/refs/...'));
# - a line the parser cannot read: the fallback does not run this rule;
# - a git command that is not at the start of its string ('cd x && git commit', shell=True);
# - a git name the code is given from outside its text: an argument (python -c
#   "...subprocess.run(sys.argv[1:])" git commit) or an environment variable (G=git python -c
#   "...os.environ['G']...");
# - python or node run by a launcher (uv run python -c ..., npx node -e ...): the line's program
#   is the launcher, and its arguments are not read as code;
# - an option that takes the next word as its value and is not named (python's
#   --check-hash-based-pycs always, a node option outside NODE_VALUE_OPTS): its value is read as
#   the script, so the code after it is not read.
# It refuses honest code in two ways, both kept: a git read (rev-parse, log) is refused like a
# write, since a read allowance would pass a verb the code computes; and a 'git ...' string in code
# whose launch runs another program is read as git.
# a call that starts a program: subprocess, os.system and its kin, asyncio's, node's child_process
INTERPRETER_LAUNCH_RE = re.compile(r'\b(subprocess|os\.(system|popen|exec\w*|spawn\w*|posix_spawn\w*)|from os import'
                                   r'|pty\.spawn|create_subprocess_\w+|child_process)\b')
# git named as a program: a quoted string that is git or git.exe (a path before it allowed), or
# starts with `git ` (a list's first element, a shell=True string, an f-string, shutil.which('git'))
INTERPRETER_GIT_RE = re.compile(r'''(['"`])(?:[^'"`\s]*[\\/]|[A-Za-z]:[\\/][^'"`\n]*[\\/])?git(?:\.exe)?'''
                                r'''(?:\1|\s)''', re.I)
# a git verb near the git name, which only chooses the alternative's line
INTERPRETER_VERB_RE = re.compile(r'''(?:['"`]|\bgit(?:\.exe)?\s+(?:-C\s+\S+\s+)?)'''
                                 r'''(commit-tree|commit|revert|cherry-pick|am|merge|pull)(?=['"`\s])''')
INTERPRETER_COMMITS = ('commit-tree', 'commit', 'revert', 'cherry-pick', 'am')


def interpreter_git(event, ctx, cmd, inv, cwd, role):
    """git run by python or node code given on the line (rules.inline_code: -c, -e, a heredoc, a
    here-string, text piped in), in an agent session (agent_session), from any directory: one
    text of that code that both starts a program (INTERPRETER_LAUNCH_RE) and names git as one
    (INTERPRETER_GIT_RE). The guard reads git only as a typed shell command, so no git rule judges
    git that code runs. It runs after every other shell rule, so a refusal another rule owns (the
    main checkout's unplaced code, owner-identity and the rest) stands. The alternative is the git
    command typed: commit.py for a commit the code makes, sync_base.py for a merge or pull, else
    `git -C <worktree> <the git command your code ran>`; a git verb in the code only chooses it."""
    if not agent_session(event, ctx):
        return None
    texts = [t for t in inline_code(cmd, inv, skip_pipe=lambda c: True)
             if INTERPRETER_LAUNCH_RE.search(t) and INTERPRETER_GIT_RE.search(t)]
    if not texts:
        return None
    verbs = {m.group(1) for t in texts for m in INTERPRETER_VERB_RE.finditer(t)}
    top = own_worktree(ctx, cwd)
    wt = quote(top) if top else '"<worktree>"'
    commit = 'python %s -C %s -m "<subject>" --model "<your model>" <each path you changed>' % (
        kit_tool(ctx, 'commit'), wt)
    sync = 'python %s -C %s <hash> --model "<your model>"' % (kit_tool(ctx, 'sync_base'), wt)
    which = 'node' if inv.name == 'node' else 'python'
    why = ('`%s` runs git from %s code. The guard reads git only as a typed shell command, so none of its git '
           'rules (the shared refs, the main checkout, the commit trailer) judge git that code runs. Type the git '
           'command itself, one per call; a commit goes through the kit\'s commit.py and a sync through its '
           'sync_base.py. If the guard refuses the typed git command, that refusal stands: do not route it through '
           'a program or a script file.' % (hub_line(bare(cmd)), which))
    if verbs & set(INTERPRETER_COMMITS):
        return refuse('interpreter-git', role, why + ' The commit:', commit)
    if verbs & {'merge', 'pull'}:
        return refuse('interpreter-git', role, why + ' The sync:', sync)
    return refuse('interpreter-git', role, why + ' The git command:',
                  'git -C %s <the git command your code ran>' % wt)


STASH_READS = ('list', 'show', 'create')        # no entry added or taken
STASH_NAMED = ('apply', 'drop')                 # pass with the entry named: `apply <sha>`, `drop stash@{1}`


def git_stash_shared(event, ctx, cmd, gc, role, place):
    """git stash in a linked worktree by an agent session (agent_session), when it adds an entry
    with no tag or takes one it does not name: every worktree of the repository shares the stash
    stack, so a bare `pop` can take another session's entry, and while the stack holds any entry
    the gate's stash guard refuses every gate and landing (docs/gate.md "The stash guard").
    Passes: list, show, create, a tagged push (-m/--message), apply or drop naming the entry,
    branch <name> <stash>. git reads a verb only as the first word after `stash`: a line that
    starts with an option or `--` is a push (`git stash -- show` stashes the path `show`) or one
    git refuses (`git stash -q list`), so only the first word is taken as the verb. An entry or branch name the shell computes
    (`drop $E`) fails closed: empty, it leaves a bare drop of the top entry. A main checkout's
    stash is git-stash's (git_main_rules)."""
    if not agent_session(event, ctx) or not stash_shared(gc):
        return None
    how, instead = stash_alternative(ctx, place.top)
    return refuse('git-stash-shared', role, '`%s` adds to or takes from the stash stack every worktree of this '
                  'repository shares; while it holds an entry every gate and landing refuses (docs/gate.md "The '
                  'stash guard"). %s' % (bare(cmd), how), instead)


def stash_shared(gc):
    """True when git stash `gc` adds an entry with no tag or takes one it does not name
    (git_stash_shared)."""
    first = gc.rest[0] if gc.rest else None
    if first is not None and (first.dyn or not first.text.startswith('-')):
        verb, args = first, gc.rest[1:]
    else:
        verb, args = None, gc.rest              # no verb, or an option or `--` first: a push (or a refused line)
    short, longs, pos = _opts('stash', args)
    if verb is not None and not verb.dyn:
        need = 2 if verb.text == 'branch' else 1
        named = len(pos) >= need and not any(w.dyn for w in pos[:need])
        if verb.text in STASH_READS or (verb.text in STASH_NAMED + ('branch',) and named):
            return False
    if (verb is None or (verb.text == 'push' and not verb.dyn)) and \
            ('m' in short or any(n and 'message'.startswith(n) for n in longs)):
        return False                            # a tagged push: its owner finds it again by the tag
    return True


def stash_alternative(ctx, where):
    """(advice, command): the WIP commit with the kit tool in the linked worktree `where` that
    takes the place of a shared stash entry (git_stash_shared)."""
    return ('Set the work aside as a WIP commit on your branch with the kit tool, which stages each path you name, '
            'a new file too, and adds the trailer:',
            'python %s -C %s -m "WIP: <what>" --model "<your model>" <each path you changed>'
            % (kit_tool(ctx, 'commit'), quote(where)))


def git_main_rules(event, ctx, cmd, inv, gc, cwd, role, place):
    """git_rules' checks of git that changes a main checkout, or a checkout git cannot tell
    (`place`: kitlib.git.repo.Place of the directory git runs in)."""
    sub, args = gc.sub, gc.rest
    text = bare(cmd)
    where = 'the main checkout %s' % place.top if place.kind == repo.MAIN else \
        'a checkout git cannot place (%s)' % place.detail
    top = place.top or '<main checkout>'
    short, longs, pos = _opts(sub, args, wide=True)
    if writes_main(sub, short, longs):
        cfg = ctx.config(place.top or gc.dir)
        if not kconfig.permissions(event, kconfig.roles(cfg)).get('edit_main_checkout'):
            why = lander_check(event, ctx, cmd, inv, gc)[1] if place.kind == repo.MAIN else None
            if why is not None:
                return lander_refusal(ctx, cmd, gc, 'git-main-history', role, why)
            how, instead = ref_delete_hint(ctx, gc, top, cwd, short, longs, pos) or \
                git_in_worktree(ctx, cmd, gc, top, cwd, agent_session(event, ctx))
            return refuse('git-main-history', role, '`%s` writes history or files in %s; this role may not change a '
                          'main checkout. %s' % (text, where, how), instead)
    no_wt, wt = new_worktree(ctx, place.top)
    if sub == 'restore':
        if not restore_index_only(short, longs):
            return refuse('git-restore', role, '`%s` overwrites working files in %s, where landers work. To undo a '
                          'change, make it in a worktree (%s). To unstage only:' % (text, where, WT_ADD % quote(top)),
                          'git -C %s restore --staged -- <path>' % quote(top)) if not no_wt else \
                refuse('git-restore', role, '`%s` overwrites working files in %s. %s' % (text, where, no_wt), wt)
    if sub in ('checkout', 'switch'):
        return refuse('git-checkout', role, '`%s` changes files in %s, where landers work. Read a file with git -C %s '
                      'show <rev>:<path>, or check the branch out in a worktree:' % (text, where, quote(top)),
                      'git -C %s worktree add .claude/worktrees/<slug> <branch>' % quote(top)) if not no_wt else \
            refuse('git-checkout', role, '`%s` changes files in %s. %s' % (text, where, no_wt), wt)
    if sub == 'reset' and 'hard' in longs:
        rev = ' '.join(w.text for w in pos) or 'HEAD'
        if place.kind == repo.MAIN and not kconfig.permissions(
                event, kconfig.roles(ctx.config(place.top or gc.dir))).get('edit_main_checkout'):
            # reset --keep changes the main checkout too, which git-main-history refuses this role
            mine = own_worktree(ctx, cwd, place.top)
            # <worktree> for a session the call shows no worktree of: a new one only in a clause (only_new)
            return refuse('git-reset-hard', role, '`%s` discards work in %s; this role may not change a main '
                          'checkout. In your worktree%s, reset --keep refuses to drop uncommitted changes:'
                          % (text, where, '' if mine or no_wt or not wt else only_new(wt)),
                          'git -C %s reset --keep %s' % (quote(mine or '<worktree>'), rev))
        return refuse('git-reset-hard', role, '`%s` discards work in %s. Reset --keep refuses to drop uncommitted '
                      'changes:' % (text, where), 'git -C %s reset --keep %s' % (quote(top), rev))
    if sub == 'clean':
        if clean_dry_run(args):
            return None
        return refuse('git-clean', role, '`%s` deletes untracked files in %s. Remove what must go one named path at a '
                      'time; to list them:' % (text, where), 'git -C %s clean -n' % quote(top))
    if sub == 'stash':
        if stash_reads(args):
            return None
        return refuse('git-stash', role, '`%s` moves work in %s into a stash every worktree shares. %s' % (
            text, where, no_wt or 'Commit the change by explicit path on a branch in a worktree:'), wt)
    if sub in ('add', 'stage'):
        bulk = {'A', 'u'} & short or {'all', 'update'} & longs or \
            any(w.text in ('.', ':/', '*') for w in pos)
        if bulk:
            return refuse('git-bulk-add', role, '`%s` stages everything in %s, other sessions\' work included. Stage '
                          'by explicit path:' % (text, where), 'git -C %s add -- <each path you changed>' % quote(top))
    if sub == 'commit':
        if 'a' in short or 'all' in longs:
            return refuse('git-commit-all', role, '`%s` commits every tracked change in %s, other sessions\' work '
                          'included. Stage by explicit path, then commit with git -C %s commit -m "<message>":' %
                          (text, where, quote(top)), 'git -C %s add -- <each path you changed>' % quote(top))
    return None


HISTORY_SUBS = ('commit', 'merge', 'rebase', 'cherry-pick', 'revert', 'am', 'pull', 'apply', 'rm', 'mv',
                'checkout-index', 'update-ref')
APPLY_READ_ONLY = {'check', 'stat', 'numstat', 'summary'}


def writes_main(sub, short, longs):
    """True when git `sub` with these options writes history, refs or tracked files (a reset
    --hard has a rule of its own)."""
    if sub == 'apply':
        return not (APPLY_READ_ONLY & longs)
    if sub in HISTORY_SUBS:
        return True
    if sub == 'reset':
        return 'hard' not in longs
    if sub == 'read-tree':
        return 'u' in short
    if sub == 'branch':
        return 'f' in short or 'force' in longs
    return False


# git subcommands that change nothing in the checkout they run in, whatever their arguments
GIT_READS = {'status', 'log', 'diff', 'show', 'rev-parse', 'ls-files', 'ls-tree', 'ls-remote', 'cat-file', 'blame',
             'annotate', 'grep', 'describe', 'shortlog', 'rev-list', 'merge-base', 'for-each-ref', 'show-ref',
             'name-rev', 'help', 'version', 'check-ignore', 'check-attr', 'check-ref-format', 'var', 'count-objects',
             'diff-tree', 'diff-files', 'diff-index', 'cherry', 'range-diff', 'whatchanged', 'verify-commit',
             'verify-tag', 'show-branch', 'fsck', 'patch-id'}
# git apply --check applies nothing, nor do --stat, --numstat and --summary: each turns applying
# off, and only --apply turns it back on (git 2.55, observed). These are the only options such an
# apply may carry, spelt in full: git expands a unique prefix (--app is --apply, which applies
# after all), so any other option fails closed (apply_checks)
APPLY_REPORTS = ('--check', '--stat', '--numstat', '--summary')
APPLY_CHECK_OPTS = set(APPLY_REPORTS) | {'-v', '--verbose', '-R', '--reverse', '--'}
APPLY_STRIP_RE = re.compile(r'-p\d+$')
# subcommands that only read when their first word that is no option is one of these (None: when
# they have none); stash's verb is its first word of all (stash_reads)
GIT_READ_VERBS = {'worktree': ('list',), 'remote': (None, 'show', 'get-url'),
                  'reflog': (None, 'show', 'exists'), 'notes': (None, 'list', 'show'),
                  'submodule': (None, 'status', 'summary')}
GIT_LIST_FLAGS = {'-l', '--list', '--show-current', '--merged', '--no-merged', '--contains', '--no-contains',
                  '--points-at'}
# git symbolic-ref <name> with only these options, spelt in full, prints where <name> points (a
# second name, -d/--delete or -m writes it)
SYMREF_READ_OPTS = {'-q', '--quiet', '--short', '--recurse', '--no-recurse'}
GIT_REF_WRITE_FLAGS = {'-d', '-D', '-m', '-M', '-c', '-C', '-f', '-u', '-a', '-s', '-e', '--delete', '--move',
                       '--copy', '--force', '--set-upstream-to', '--unset-upstream', '--edit-description',
                       '--annotate', '--sign'}
# strict reads (git_reads): the only options branch and tag may carry, spelt in full, and the
# short-flag clusters they may combine (branch -av, tag -n5); config's are CONFIG_STRICT_READ_OPTS
GIT_LISTING_OPTS = GIT_LIST_FLAGS | {'-i', '--ignore-case', '--sort', '--format', '--color', '--no-color', '--column',
                                     '--no-column', '--omit-empty'}
GIT_STRICT_READ_OPTS = {'branch': (GIT_LISTING_OPTS | {'--all', '--remotes', '-vv', '--verbose', '--abbrev',
                                                       '--no-abbrev'}, re.compile(r'-[ailrv]+$')),
                        'tag': (GIT_LISTING_OPTS, re.compile(r'-(?=.)[il]*(?:n\d*)?$'))}
# strict reads (strict_dyn_read): reads no option of which writes a file or runs a program the line
# names, so a computed word (any number of words) leaves them reads; and reads with such an option
# (log, show, diff and rev-list: --output), where a computed word stays a read only when the text pins it to
# one argument that is no option, or the value of one of GIT_DYN_VALUE_OPTS
GIT_DYN_ANY_READS = {'rev-parse', 'ls-files', 'cat-file', 'merge-base', 'for-each-ref', 'ls-tree'}
# rev-list takes the diff options too (--output opens a file for writing)
GIT_DYN_PINNED_READS = {'log', 'show', 'diff', 'rev-list'}
# a literal word after which git reads no option (log "$A" --end-of-options "$B"): the computed
# words behind it are revisions or paths, so they cannot be --output; not when it is the value of
# the option before it (--grep --end-of-options), unless that option is known to take no value
END_OF_OPTIONS = '--end-of-options'
NO_VALUE_OPTS = frozenset(('--oneline', '--stat', '--patch', '-p', '--name-only', '--name-status', '--numstat',
                           '--shortstat', '--cached', '--staged', '--no-color', '--count', '--graph', '--all'))
NO_VALUE_NUM_RE = re.compile(r'-\d+')
GIT_DYN_VALUE_OPTS = ('--format=', '--pretty=')
# characters a pinned word may carry outside quotes: none the shell expands, splits or joins on
# (bash globs and braces, PowerShell's array comma and splat @), and no backslash or backtick
DYN_SAFE_BARE = {sh.BASH: frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./~^:=%+-@,'),
                 sh.PS: frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./~^:=%+-')}
DYN_VAR_RE = re.compile(r'\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*|[0-9])')
DYN_PS_VAR_RE = re.compile(r'(?i)\$(?:\{[A-Za-z_][A-Za-z0-9_:]*\}|(?:env:|script:|global:|local:)?[A-Za-z_][A-Za-z0-9_]*)')


def writes_output_file(t):
    """True for log/show/diff's --output[=<file>], spelt in full or abbreviated (--outp=x)."""
    name = t.split('=', 1)[0]
    return len(name) >= 4 and '--output'.startswith(name)


# git archive's long options in full (git expands a unique prefix: --outp is --output), those that
# take a value, and its short ones (-o takes a value; -0..-9 are compression levels)
ARCHIVE_LONGS = ('format', 'prefix', 'add-file', 'add-virtual-file', 'output', 'worktree-attributes', 'verbose',
                 'list', 'mtime', 'remote', 'exec')
ARCHIVE_VALUES = {'format', 'prefix', 'add-file', 'add-virtual-file', 'output', 'mtime', 'remote', 'exec'}
ARCHIVE_FLAGS = set('vl0123456789')
# the -c keys (lower-case) an archive may carry and stay a read: none names a program, a filter or
# another file to read config or attributes from. Any other key may make archive run a program in
# the checkout it reads (tar.<format>.command; filter.<driver>.smudge or .process with
# core.attributesFile or the checkout's own attributes; include.path), so it fails closed
ARCHIVE_SAFE_KEY_RE = re.compile(r'^(?:core\.quotepath|core\.autocrlf|core\.eol|tar\.umask|tar\..+\.remote)$')


def cmd_native_unsure(cmd, w):
    """True when the C runtime may split the word `w` that cmd.exe passes on other than cmd reads
    it: cmd toggles quoting at every `"` and keeps backslashes, the C runtime reads `\\"` as a quote
    and `""` inside quotes as one; so only a word with no `"`, or one wholly in one pair of quotes
    with no backslash before the closing one, is read the same (cmd reads a\\" -o f \\"b as one
    word, git.exe gets a", -o, f, "b). A word with no valid place in the source is unsure: its
    text has cmd's quotes taken out already (fail closed)."""
    src = cmd.source or ''
    if w.start is None or w.end is None or not 0 <= w.start <= w.end <= len(src):
        return True
    raw = src[w.start:w.end]
    if '"' not in raw:
        return False
    return not (len(raw) >= 2 and raw[0] == raw[-1] == '"' and '"' not in raw[1:-1] and not raw[:-1].endswith('\\'))


def native_unsure(cmd, w):
    """True when the shell may hand the word `w` to a native program as other than one argument,
    the word the guard reads (ps_native_unsure, cmd_native_unsure)."""
    if cmd.shell == sh.PS:
        return ps_native_unsure(w)
    if cmd.shell == sh.CMD:
        return cmd_native_unsure(cmd, w)
    return False


def dyn_head(cmd, w):
    """The literal text before the first variable of the computed word `w`, read from its source
    when the shell (bash or PowerShell) is sure to hand it to git as one argument that starts with
    that text: outside quotes only DYN_SAFE_BARE characters, inside '...' anything but the quote,
    inside "..." plain text and $name or ${name} only (no $( or backtick that runs code, no "$@" or
    other variable that is many words, no backslash, no embedded quote); else None. PowerShell
    ends an argument that opens with a quote at its closing quote and passes what follows as
    another ('HEAD'"$f" is two), so such a word with text after its closing quote is None. A word
    with no variable, or no valid place in the source, is None too (fail closed)."""
    src = cmd.source or ''
    if cmd.shell not in DYN_SAFE_BARE or w.start is None or w.end is None or not 0 <= w.start < w.end <= len(src):
        return None
    raw, ps = src[w.start:w.end], cmd.shell == sh.PS
    if ps and (cm.PS_SPLAT_RE.match(raw) or any(c in raw for c in '“”„‘’‚‛')):
        return None
    # a doubled quote or a backtick inside returns None below, so the first matching quote after
    # the opening one is its close
    if ps and raw[0] in '\'"' and raw.find(raw[0], 1) != len(raw) - 1:
        return None
    var_re, bare = (DYN_PS_VAR_RE if ps else DYN_VAR_RE), DYN_SAFE_BARE[cmd.shell]
    head, seen, i, quote = [], False, 0, None
    while i < len(raw):
        c = raw[i]
        if quote == "'":
            if c == "'":
                if ps and raw[i + 1:i + 2] == "'":
                    return None                 # '' is a quote inside the word
                quote = None
            elif not seen:
                head.append(c)
            i += 1
            continue
        if c in '\\`':
            return None
        if c == '"':
            if quote == '"' and ps and raw[i + 1:i + 2] == '"':
                return None                     # "" is a quote inside the word
            quote = None if quote == '"' else '"'
            i += 1
            continue
        if c == "'" and quote is None:
            quote = "'"
            i += 1
            continue
        if c == '$':
            m = var_re.match(raw, i)
            if quote != '"' or not m:
                return None
            seen = True
            i = m.end()
            continue
        if quote is None and c not in bare:
            return None
        if not seen:
            head.append(c)
        i += 1
    if quote is not None or not seen:
        return None
    return ''.join(head)


def respelt(cmd, w):
    """True unless the source of the word `w` is exactly its text (a plain literal): a quoted, escaped,
    locale-quoted or brace-expanded word may reach git as other than its text. No place: True."""
    src = cmd.source or ''
    return w.start is None or w.end is None or not 0 <= w.start <= w.end <= len(src) or src[w.start:w.end] != w.text


def strict_dyn_read(gc):
    """True when every computed word of git read `gc` leaves it a read whatever it stands for
    (git_reads strict): a GIT_DYN_ANY_READS subcommand in bash or PowerShell, or a
    GIT_DYN_PINNED_READS one whose every computed word is one argument (dyn_head) that starts with
    literal text that keeps it from being an option (HEAD~$n, origin/$b, ./$f) or with
    --format= or --pretty=; or any computed word after a literal --end-of-options that is not the
    value of the option before it (END_OF_OPTIONS). Residual (inferred): PowerShell 5.1 splits an argument whose value
    holds a double quote (ps_native_unsure), which the text cannot show."""
    cmd = gc.inv.cmd if gc.inv is not None else None
    if cmd is None or cmd.shell not in DYN_SAFE_BARE:
        return False
    if gc.sub in GIT_DYN_ANY_READS:
        return True
    if gc.sub not in GIT_DYN_PINNED_READS:
        return False
    done, prev = False, None
    for w in gc.rest:
        before, prev = prev, w
        if not w.dyn:
            # the word before is read as written, so one the shell changes (_computed, native_unsure,
            # or any whose source differs from its text: $"--grep", {\--grep,}) fails closed
            if w.text == END_OF_OPTIONS and (before is None or not _computed(before)
                                             and not native_unsure(cmd, before) and not respelt(cmd, before) and (
                    not before.text.startswith('-') or before.text in NO_VALUE_OPTS
                    or NO_VALUE_NUM_RE.fullmatch(before.text))):
                done = True
            continue
        if done:
            continue
        head = dyn_head(cmd, w)
        if not head or (head.startswith('-') and not head.startswith(GIT_DYN_VALUE_OPTS)):
            return False
    return True


def ps_splat(gc, w):
    """True when the word `w` of git `gc` is a PowerShell splat (@a) the shell spreads into words
    the text does not show; a quoted '@a' is plain text."""
    return (gc.inv is not None and gc.inv.cmd.shell == sh.PS and not w.dyn and cm.PS_SPLAT_RE.match(w.text)
            and (w.start is None or not cm.quoted(gc.inv.cmd, w)))


def archive_words(gc):
    """The Words after git archive `gc`'s subcommand as the shell hands them to git (bash drops
    every backslash it would, a leading one too: `\\-o` is -o; commands.passed)."""
    cmd = gc.inv.cmd
    return list(gc.rest[:gc.synth]) + [sh.Word(cm.passed(cmd, w), w.dyn, w.start, w.end, w.synth)
                                       for w in gc.after()]


def archive_filter_set(gc):
    """True when the line may give git archive `gc` a program to run that the checkout's own config
    does not, or the guard cannot tell: a git the text cannot place (gc.blind: --git-dir,
    --work-tree, --config-env, a GIT_DIR-like variable), a commands.GIT_ENV_NAMES variable the
    line sets (commands.mark_git_env), a user config moved (HOME and the like, Command.home_env), PATH changed (Command.path_env), and
    before the subcommand --exec-path (it and PATH say where git finds a filter's bare program
    name, as Git for Windows' filter.lfs.process = git-lfs), a word the guard cannot read
    (computed, or one PowerShell or cmd may split: native_unsure) or a -c whose key, read as the
    shell hands it to git (commands.passed), is not on ARCHIVE_SAFE_KEY_RE."""
    cmd = gc.inv.cmd
    if gc.blind or cmd.git_env or cmd.home_env or cmd.path_env:
        return True
    pre = gc.before()
    k = 0
    while k < len(pre):
        if any(w.dyn or native_unsure(cmd, w) for w in pre[k:k + 2]):
            return True
        t = cm.passed(cmd, pre[k])
        name = t.partition('=')[0]
        if name.startswith('--exec-path') or len(name) > 2 and '--exec-path'.startswith(name):
            return True
        if t == '-c':
            if k + 1 >= len(pre):
                return True
            body, k = cm.passed(cmd, pre[k + 1]), k + 2
        elif t.startswith('--config-env'):
            return True
        elif t.startswith('-c'):
            body, k = t[2:], k + 1
        else:
            k += 1
            continue
        if not ARCHIVE_SAFE_KEY_RE.match(body.partition('=')[0].lower()):
            return True
    return False


def archive_long(name):
    """The archive long option `name` (no dashes) stands for, or None when it is unknown or an
    ambiguous prefix (--add)."""
    if name in ARCHIVE_LONGS:
        return name
    hits = [o for o in ARCHIVE_LONGS if name and o.startswith(name)]
    return hits[0] if len(hits) == 1 else None


def archive_opened(words):
    """[Word]: the files git archive's first pass (builtin/archive.c cmd_archive) opens. That pass
    knows only -o/--output, --remote and --exec, keeps every other option without taking a value
    for it and reads on past words that are not options, up to `--`; so in `--prefix -o f HEAD` the
    -o is its own and f is opened, though the later pass takes -o as the prefix. Every -o/-o<f>,
    --output[=]<f> and prefix of --output counts wherever it sits (a prefix fails closed), and no
    other option takes the word after it (fail closed for --remote and --exec)."""
    outs, k = [], 0
    while k < len(words):
        w, t = words[k], words[k].text
        if t == '--':
            break
        name, eq, value = t[2:].partition('=')
        if t.startswith('--') and name and 'output'.startswith(name):
            if eq:
                outs.append(tail_word(w, value))
            elif k + 1 < len(words):
                k += 1
                outs.append(words[k])
        elif t.startswith('-o'):
            if t[2:]:
                outs.append(tail_word(w, t[2:]))
            elif k + 1 < len(words):
                k += 1
                outs.append(words[k])
        k += 1
    return outs


def archive_outputs(gc):
    """(outputs, read) for git archive `gc`: the Words its -o/--output name, those git's first pass
    opens (archive_opened) and, failing closed, those the later pass would read (-vo <f>, an -o
    after `--prefix`'s value), and True when the archive only reads the repository it runs in: no
    option the guard does not know (a --no-* among them) and no word that is not literal (an unquoted $X may hold -o) or that PowerShell or cmd
    may split (native_unsure). An unknown option does not end the scan, so an -o after it is still
    an output. Every word is read as the shell hands it to git (archive_words)."""
    words = archive_words(gc)
    read = not any(native_unsure(gc.inv.cmd, w) for w in words)
    outs, k = [], 0
    while k < len(words):
        w, t = words[k], words[k].text
        if w.dyn:
            read = False
        if t == '--':
            read = read and not any(x.dyn for x in words[k + 1:])
            break
        if t.startswith('--'):
            name, eq, value = t[2:].partition('=')
            opt = archive_long(name)
            if opt is None:
                read = False
            if opt in ARCHIVE_VALUES and not eq:
                k += 1
                if k < len(words):
                    w, value = words[k], words[k].text
                    read = read and not w.dyn
                else:
                    w = None
            if opt == 'output' and w is not None:
                outs.append(tail_word(w, value) if eq else w)
        elif t.startswith('-') and len(t) > 1:
            for j, c in enumerate(t[1:], 1):
                if c == 'o':
                    if t[j + 1:]:
                        outs.append(tail_word(w, t[j + 1:]))
                    elif k + 1 < len(words):
                        k += 1
                        outs.append(words[k])
                        read = read and not words[k].dyn
                    break
                if c not in ARCHIVE_FLAGS:
                    read = False
                    break
        k += 1
    spans = {(o.text, o.start, o.end) for o in outs}
    outs += [o for o in archive_opened(words) if (o.text, o.start, o.end) not in spans]
    return outs, read


def strict_read_opts(sub, words):
    """True when every option of branch, tag or config is one GIT_STRICT_READ_OPTS or
    CONFIG_STRICT_READ_OPTS names in full: git expands an abbreviation (branch --unset-up), so an
    option the guard does not know may be a write."""
    if sub == 'config':
        known, cluster = CONFIG_STRICT_READ_OPTS, None
    else:
        known, cluster = GIT_STRICT_READ_OPTS[sub]
    return all(t.split('=', 1)[0] in known or cluster is not None and cluster.fullmatch(t)
               for t in words if t.startswith('-'))


def git_reads(gc, strict=False):
    """True when git `gc` changes nothing in the checkout it runs in. `strict` (a checkout the text
    cannot place) counts only what is plainly a read: no computed word but those strict_dyn_read
    allows, no PowerShell splat (ps_splat), no --output file, and branch, tag and config only with
    options it names in full (strict_read_opts). symbolic-ref reads with one ref and only
    SYMREF_READ_OPTS."""
    sub = gc.sub
    words = [w.text for w in gc.rest]
    # a word bash changes (`\--output=f`) is also read as the shell hands it to git (Word.alt)
    passed = words + [w.alt for w in gc.rest if w.alt is not None]
    if strict and (any(ps_splat(gc, w) for w in gc.rest) or any(writes_output_file(t) for t in passed)):
        return False
    if strict and any(w.dyn for w in gc.rest) and not strict_dyn_read(gc):
        return False
    if sub == 'archive':
        # its -o file is a write: git_targets hands it to git_jail, which judges it first; where
        # the checkout cannot be placed, neither can a relative -o, so it is no plain read
        outs, read = archive_outputs(gc)
        return read and not (strict and outs) and not archive_filter_set(gc)
    if sub in GIT_READS:
        return True
    if sub == 'stash':
        return stash_reads(gc.rest)
    pos = [t for t in words if not t.startswith('-')]
    if sub in GIT_READ_VERBS:
        return (pos[0] if pos else None) in GIT_READ_VERBS[sub]
    if strict and sub in ('branch', 'tag', 'config') and not strict_read_opts(sub, words):
        return False
    if sub in ('branch', 'tag'):
        flags = {t.split('=', 1)[0] for t in words if t.startswith('-')}
        writes = flags & GIT_REF_WRITE_FLAGS
        if sub == 'branch':
            writes -= {'-a'}                    # branch -a lists every branch
        return not writes and (not pos or bool(flags & GIT_LIST_FLAGS))
    if sub == 'config':
        return config_reads(gc)
    if sub == 'apply':
        return apply_checks(gc)
    if sub == 'symbolic-ref':
        return len(pos) == 1 and all(t in SYMREF_READ_OPTS for t in words if t.startswith('-'))
    return False


def apply_checks(gc):
    """True when git apply `gc` only checks or reports on its patch: one of APPLY_REPORTS among its
    options (the words before the first --; after it git reads every word as a patch file,
    `--check` too) and every option one APPLY_CHECK_OPTS names (or -p<n>; --apply, which applies
    after all, is not one), read as the shell hands it to git (archive_words), and no
    word the guard cannot read (computed, a PowerShell splat, or one PowerShell or cmd may split:
    native_unsure). Any other apply writes the checkout."""
    if gc.inv is None:
        return False
    words = archive_words(gc)
    if any(w.dyn or ps_splat(gc, w) or native_unsure(gc.inv.cmd, w) for w in words):
        return False
    texts = [w.text for w in words]
    if '--' in texts:                           # git reads no option after --: those words are patches
        texts = texts[:texts.index('--')]
    opts = [t for t in texts if t.startswith('-') and t != '-']
    return any(t in APPLY_REPORTS for t in opts) and all(t in APPLY_CHECK_OPTS or APPLY_STRIP_RE.match(t)
                                                          for t in opts)


def config_reads(gc):
    """True when git config `gc` carries a read flag (CONFIG_READ_OPTS, spelt in full) as its own
    option. git config reads options only up to its first word that is no option (git 2.55,
    observed: `config a.b --list` sets a.b to "--list"), so the flag counts only there, before any
    `--` and not as the value of an option that takes one (-f/--file, -t/--type, --blob, --default,
    --comment, --value, --url, abbreviated too: _config_long). A computed word there ends the scan
    (fail closed), as does a first `--`, which git 2.55 passes over (`config -- --list` lists)."""
    words, k = gc.rest, 0
    while k < len(words):
        w, t = words[k], words[k].text
        if w.dyn or t == '--' or len(t) < 2 or not t.startswith('-'):
            return False
        if t in CONFIG_READ_OPTS:
            return True
        if t.startswith('--'):
            body, eq, _v = t[2:].partition('=')
            if not eq and any(n in CONFIG_LONG_VALUES for n in _config_long(body)):
                k += 1                          # its value, whatever it says
        else:
            at = min((t.find(c, 1) for c in 'ft' if c in t[1:]), default=-1)
            if at == len(t) - 1:
                k += 1                          # -f <file>, -t <type>: the value is the next word
        k += 1
    return False


def git_targets(gc):
    """[Word]: the folders and files git `gc` writes besides the checkout it runs in (worktree add,
    clone, archive's -o/--output file: archive_outputs)."""
    args = gc.rest
    if gc.sub == 'archive':
        return archive_outputs(gc)[0]
    if gc.sub == 'worktree' and args and args[0].text == 'add':
        rest, skip = args[1:], {'-b', '-B', '--reason', '--orphan'}
    elif gc.sub == 'clone':
        rest, skip = args, {'-b', '--branch', '-o', '--origin', '--depth', '--reference', '-c', '--config',
                            '--template', '-u', '--upload-pack', '--separate-git-dir', '--filter', '-j', '--jobs',
                            '--shallow-since', '--shallow-exclude', '--reference-if-able', '--server-option'}
    else:
        return []
    pos, k = [], 0
    while k < len(rest):
        t = rest[k].text
        if t in skip:
            k += 2
            continue
        if not t.startswith('-'):
            pos.append(rest[k])
        k += 1
    if gc.sub == 'worktree':
        return pos[:1]
    return pos[1:2]


LANDER = 'lander'


LANDER_VERBS = ('fetch', 'merge')
# the tests lander_check runs, in order; a refusal names the first that failed
LANDER_NO_CONFIG = 'the main checkout\'s .kit.json does not load'
LANDER_NO_ROLE = ('the main checkout\'s .kit.json does not set roles.lander.edit_main_checkout to true, so no lander '
                  'may write it; the owner sets it (docs/guard.md "Go-live", step 3)')
LANDER_NOT_ALONE = ('git is not alone on the line (at most one literal cd, Set-Location or pushd to that checkout '
                    'may come before it, joined by && or ;)')
LANDER_WRAPPED = ('a wrapper, an environment assignment, a git option other than -C <checkout>, or a word that is '
                  'not literal comes with it')
LANDER_ARGS = 'its arguments are not exactly one of the landing forms'


def lander_forms(cfg):
    """The landing verbs a lander may run in a main checkout, as git argument lists."""
    remote, main = cfg.remote, cfg.main_branch
    return {'fetch': ([remote], [remote, main]), 'merge': (['--ff-only', '%s/%s' % (remote, main)],)}


def lander_alone(event, cmd, gc, home=None):
    """True when `cmd` (git) is the whole command line of `event`, or follows one literal cd,
    Set-Location or pushd into the checkout git runs in, joined by && or ;. The line is read again
    from the event, `~` as `home` (ctx.home); an unclean parse, a pipe, a group, a nested shell or
    any third command is not alone."""
    shell = SHELLS.get(event.shell) if event is not None and event.kind == 'shell' else None
    text, cwd = (event.command, event.workdir or event.cwd) if event is not None else (None, None)
    if shell is None or not isinstance(text, str) or cmd.depth or cmd.nested_in is not None:
        return False
    parsed = cm.read(text, shell, cwd, home=home)
    cmds = parsed.commands
    if not parsed.clean or not cmds or len(cmds) > 2 or cmds[-1].start != cmd.start or cmds[-1].end != cmd.end:
        return False
    if any(c.depth or c.groups or c.piped_in or c.piped_out or c.nested_in is not None for c in cmds):
        return False
    if len(cmds) == 1:
        return True
    is_cd, where = sh.cd_target(cmds[0], cwd, home)
    if not is_cd or where is None or cmds[1].prev_op not in ('&&', ';') or cmds[0].redirects:
        return False
    return jail.real(where) == jail.real(gc.dir)


def lander_check(event, ctx, cmd, inv, gc):
    """(allowed, why) for a lander subagent's `git fetch` or `git merge` in a main checkout:
    (True, None) for one of the landing forms (lander_forms: `fetch <remote>`, `fetch <remote>
    <main>`, `merge --ff-only <remote>/<main>`, remote and main from that checkout's .kit.json repo
    section), alone on its line (lander_alone), with at most `-C <main checkout>` before the verb
    (git, git.exe or PowerShell's `& git`), in a checkout whose .kit.json sets
    roles.lander.edit_main_checkout: the one main-checkout write the jail leaves a lander that runs
    from a worktree. (False, <the first test that failed>) when one fails. (False, None) for any
    other session, verb or place: the other rules judge it as before."""
    if not subagent(event) or event.agent_role != LANDER:
        return False, None
    if gc.unknown or gc.sub not in LANDER_VERBS or not gc.dir or ctx.place(gc.dir).kind != repo.MAIN:
        return False, None
    # the repository's own settings first: while they refuse every landing form, a refusal that
    # named the command would offer an Instead that is refused too
    cfg = ctx.config(gc.dir)
    if cfg is None:
        return False, LANDER_NO_CONFIG
    table = kconfig.roles(cfg)
    if kconfig.role_name(event, table) != LANDER or not table[LANDER].get('edit_main_checkout'):
        return False, LANDER_NO_ROLE
    if not lander_alone(event, cmd, gc, getattr(ctx, 'home', None)):
        return False, LANDER_NOT_ALONE
    pre = gc.before()
    if inv.via is not None or inv.dyn or [w.text for w in cmd.words[:inv.index]] not in ([], ['&']) or \
            [w.text for w in pre[:1]] not in ([], ['-C']) or len(pre) not in (0, 2) or \
            any(w.dyn for w in pre) or any(w.dyn for w in gc.rest):
        return False, LANDER_WRAPPED
    if [w.text for w in gc.rest] not in lander_forms(cfg)[gc.sub]:
        return False, LANDER_ARGS
    return True, None


def lander_refusal(ctx, cmd, gc, rule, role, why):
    """The refusal for a lander's landing step that failed lander_check's test `why`: it names the
    test, the forms a lander may run, and the command to use (the config read when the config is
    the cause)."""
    top = ctx.place(gc.dir).top or gc.dir
    kit = os.path.join(top, KIT_JSON)
    cfg = ctx.config(gc.dir)
    if cfg is None or why == LANDER_NO_ROLE:
        forms = 'the landing forms'
        instead, end = 'python -m json.tool %s' % quote(kit), 'Ask the coordinator to take it to the owner. To read the file:'
    else:
        remote, main = cfg.remote, cfg.main_branch
        forms = 'git fetch %s, git fetch %s %s or git merge --ff-only %s/%s' % (remote, remote, main, remote, main)
        want = '%s %s' % (remote, main) if gc.sub == 'fetch' else '--ff-only %s/%s' % (remote, main)
        instead, end = 'git -C %s %s %s' % (quote(top), gc.sub, want), 'Run it alone, as:'
    return refuse(rule, role, '`%s` is refused as a lander\'s landing step in the main checkout %s: %s. A lander may '
                  'run only %s there, each alone on its line. %s' % (bare(cmd), fwd(top), why, forms, end), instead)


def git_jail_unknown(ctx, cmd, gc, cwd, role):
    """An agent session's git that writes a checkout the text cannot place (GIT_DIR and the rest
    of commands.GIT_ENV_NAMES set, --git-dir, --work-tree, -c core.worktree, --config-env, a
    computed -C; a -C after any of them does not place it), by any role: it may be a main
    checkout, and the main-checkout git rules judge only history writes there, so a symbolic-ref
    or branch -m that re-points main's HEAD would pass them. Only a plain read passes
    (git_reads strict; a computed word only as strict_dyn_read allows). The hint names what hides the place (git_unplaced): drop it, or name a
    computed -C literally. A computed -C is refused under UNPLACED (warn by default); the
    re-pointing forms (gc.blind) stay write-jail."""
    if git_reads(gc, strict=True):
        return None
    if gc.blind:                                # a deliberate re-pointing: denied, not unplaced
        how, instead = git_unplaced(ctx, cmd, gc, cwd, True)
        return refuse('write-jail', role, '`%s` writes a checkout the guard cannot place: %s can point git at a '
                      'git directory, work tree, index or config the text does not show, and a -C does not undo '
                      'it. %s' % (bare(cmd), gc.blind, how), instead)
    return refuse(UNPLACED, role, '`%s` writes a checkout the guard cannot place (a computed -C, or a '
                  'folder it cannot tell). Name the checkout literally:' % bare(cmd),
                  ('git -C "<checkout>" %s %s' % (gc.sub, ' '.join(w.text for w in gc.rest))).rstrip())


def git_jail(event, ctx, cmd, inv, cwd, role):
    """An agent session's git that writes a checkout (or makes a folder) outside its roots
    (kitlib.guard.jail): git -C <elsewhere> commit, a cd chain into another checkout, worktree
    add or clone to a folder outside them. Its repository's other worktrees (jail.sibling) are
    not elsewhere."""
    gc = git_call_judged(inv)
    if gc is None or not agent_session(event, ctx):
        return None
    if not gc.dir:                              # commands.git_call: no place the text can tell
        return git_jail_unknown(ctx, cmd, gc, cwd, role)
    if gc.unknown:
        return None                             # a computed subcommand: the unreadable rule's
    view = None
    for w in git_targets(gc):
        if w.dyn:
            continue
        target = sh.resolve_path(w.text, gc.dir, home=getattr(ctx, 'home', None))
        if not target:
            continue
        view = view or jail_view(event, ctx, cwd)
        rs, dev, dropped = view
        verdict, root = jail.judge(target, rs)
        what = '`%s` makes %s' % (bare(cmd), w.text)
        allowed = verdict is None or (verdict == jail.OUTSIDE and (jail.sibling(ctx, target, cwd) or
                                                                   in_dropped(target, dropped, dev)))
        if allowed and (not dev or jail.on_drive(target, dev)):
            continue
        # a word an inline alias gave has no span in the line to aim (inline_alias)
        rewrite = None if w.start is None else git_retarget(ctx, cmd, inv, gc, w, cwd)
        if allowed:
            d = dev_drive_bound(event, ctx, cwd, rs, dev, target, role, what, rewrite)
            if d:
                return d
            continue
        return jail_refusal(role, rs, verdict, root, what, target, rewrite=rewrite)
    if git_reads(gc):
        return None
    rs, dev, dropped = view or jail_view(event, ctx, cwd)
    changes = '`%s` changes the checkout %s' % (bare(cmd), fwd(gc.dir))
    landed, why = lander_check(event, ctx, cmd, inv, gc)
    if landed:
        return dev_drive_bound(event, ctx, cwd, rs, dev, gc.dir, role, changes)
    d = pm_data_unknown(ctx, gc.dir, cwd, role, '`%s`' % bare(cmd))
    if d:
        return d
    verdict, root = jail.judge(gc.dir, rs)
    if verdict is None or (verdict == jail.OUTSIDE and (jail.sibling(ctx, gc.dir, cwd) or in_dropped(gc.dir, dropped, dev))):
        return dev_drive_bound(event, ctx, cwd, rs, dev, gc.dir, role, changes)
    if why is not None:
        return lander_refusal(ctx, cmd, gc, 'write-jail', role, why)
    what = '`%s` changes the checkout %s' % (bare(cmd), fwd(gc.dir))
    dest = jail.destination(rs)
    if dest is None:
        return jail_refusal(role, rs, verdict, root, what, gc.dir)
    if ctx.place(dest.path).kind not in (repo.MAIN, repo.LINKED):
        # the folder it may write is no checkout (the temp folder, a plain folder): git there has no
        # repository to act on, so no same command is offered
        return refuse('write-jail', role, jail_why(rs, verdict, root, what, gc.dir) +
                      ' To see where this session runs:', 'pwd')
    if verdict == jail.OUTSIDE:
        why = '%s: it is outside the folders an agent session may write: %s. %s' % (what, jail.describe(rs), JAIL_ASK)
    else:
        why = jail_why(rs, verdict, root, what, gc.dir)
    note, instead = git_elsewhere(ctx, cmd, gc, dest.path, True)
    return refuse('write-jail', role, why + (' In %s, %s' % (fwd(dest.path), note) if note else
                                             ' The same command in %s:' % fwd(dest.path)), instead)


def git_elsewhere(ctx, cmd, gc, where, agent):
    """(note, command): git `gc` run again in the checkout `where`, `git -C <where> <sub> <rest>`
    and no note. In a linked worktree an agent session's commit (commit_alternative) or shared
    stash entry (stash_shared) is refused there too (git_plain_commit, git_stash_shared): the kit
    tool's form, the note saying why."""
    plain = ('', ('git -C %s %s %s' % (quote(where), gc.sub, git_rest_text(cmd, gc))).rstrip())
    if not agent:
        return plain
    place = ctx.place(where)
    if place.kind != repo.LINKED or not place.top:
        return plain
    alt = commit_alternative(ctx, gc, place.top)
    if alt is not None:
        return ('plain git writes no Co-Authored-By trailer, which the gate\'s trailer check fails. ' + alt[0],
                alt[1])
    if gc.sub == 'stash' and stash_shared(gc):
        how, instead = stash_alternative(ctx, place.top)
        return 'the stash stack is shared by every worktree of the repository. ' + how, instead
    return plain


def git_retarget(ctx, cmd, inv, gc, w, cwd):
    """rewrite(path) for the refused output word `w` of git `gc`: the same command with `w` aimed
    at `path`. A git archive with no -C of its own also names the checkout it archives: an agent's
    shell keeps no cd between calls, so the bare command run again from a main checkout archives
    main, not the branch. That is the session's own worktree of the main checkout git runs in
    (`<worktree>` when it has none, its output aimed there too), else the folder git runs in."""
    def rewrite(path):
        if gc.sub != 'archive' or inv.via is not None or inv.word is None or inv.word.start is None or \
                any(x.text.startswith('-C') for x in gc.before()):
            return retarget(cmd, w, path)
        place = ctx.place(gc.dir)
        where = gc.dir
        if place.kind == repo.MAIN and place.top:
            where = own_worktree(ctx, cwd, place.top) or '<worktree>'
            top = paths.norm(place.top)
            if jail.inside(paths.norm(path), top):
                path = fwd(where) + fwd(paths.norm(path, fold=False)[len(top):])
        line = retarget(cmd, w, path)
        at = inv.word.end - min(cmd.words[0].start, w.start)
        return line[:at] + ' -C ' + quote(fwd(where)) + line[at:]
    return rewrite


def git_in_worktree(ctx, cmd, gc, main_top, cwd, agent=False):
    """(sentence, command): the same git command in the session's own worktree of this main
    checkout, when it has one (git_elsewhere: an agent session's commit there is the kit tool's),
    else the worktree to make; git_unplaced when what hides the place is in the text."""
    if gc.blind:
        return git_unplaced(ctx, cmd, gc, cwd, agent)
    mine = own_worktree(ctx, cwd, main_top)
    if mine:
        note, instead = git_elsewhere(ctx, cmd, gc, mine, agent)
        return 'Run it in your worktree:' + (' ' + note if note else ''), instead
    no_wt, instead = new_worktree(ctx, main_top)
    if no_wt:
        return no_wt, instead
    return 'Work in a worktree and run git %s there; the lander brings it to main:' % gc.sub, instead


def ref_delete_hint(ctx, gc, top, cwd, short, longs, pos, cleanup=False):
    """(sentence, command) for `git update-ref -d <ref>`, or None. Every worktree of a repository
    shares its refs, so neither a worktree nor the lander carries a ref delete to main: a branch
    goes with git branch -d, which refuses an unmerged one; a stray ref (stray_ref) is shown, and
    a coordinator deletes it in its own repository (`cleanup`: the session is one); any other ref
    is shown, and the PM deletes it; a guarded main is never offered to branch -d. Both run in
    the session's own worktree of this main checkout when it has one. None too for a ref
    git_shared_ref lets any session delete (scratch_ref): only where it runs is wrong, and
    git_in_worktree says so."""
    if gc.sub != 'update-ref' or 'd' not in short or 'stdin' in longs or gc.blind:
        return None
    ref = pos[0] if pos else None
    name = ref.text if ref is not None and not ref.dyn else None
    if name and scratch_ref(name):
        return None
    at = quote(own_worktree(ctx, cwd, top) or top)
    shared = 'Every worktree shares this repository\'s refs, so no worktree or landing carries a ref delete to main.'
    if name and name.startswith('refs/heads/') and len(name) > len('refs/heads/'):
        branch = name[len('refs/heads/'):]
        if branch.lower() in main_names(ctx, top):
            return ('%s %s is the main branch: only a landing moves it, and no session deletes it. To show it:' %
                    (shared, name), 'git -C %s show-ref %s' % (at, name))
        return ('%s Delete a branch with git branch -d, which refuses an unmerged one; unmerged work goes to '
                'the PM, never deleted:' % shared, 'git -C %s branch -d %s' % (at, branch))
    if name and stray_ref(name):
        who = 'you delete it in your own repository, naming the ref literally' if cleanup else \
            'your coordinator deletes it from its own session in its own repository, so send it the ref'
        return ('%s A ref outside refs/heads, refs/tags, refs/stash and refs/backlog is a coordinator\'s '
                'to delete: %s. To show it:' %
                (shared, who), 'git -C %s show-ref %s' % (at, name))
    return ('%s A ref other than a branch is the PM\'s to delete; to show it:' % shared,
            'git -C %s show-ref %s' % (at, name or '<ref>'))


# git_shared_ref: the update-ref options it reads (GIT_LONGS['update-ref'] spells the long ones),
# the refs any agent session may write (scratch_ref), and the names git reads as one loose ref file
UPDATE_REF_SHORTS = set('mdz')
PER_WORKTREE_REFS = ('refs/bisect/', 'refs/worktree/', 'refs/rewritten/')
PSEUDOREF_RE = re.compile(r'^[A-Z][A-Z0-9_]*_HEAD$')
REF_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]*$')
SCRATCH_WALK_MAX = 4096     # entries of a temp repository's git directory read for a link, at most
REPARSE_POINT = 0x400       # FILE_ATTRIBUTE_REPARSE_POINT: a symlink, a junction or another link


def ref_name_ok(name):
    """True for a ref name the guard reads as one file under the repository's git directory: plain
    characters only, no `..`, empty or dot-led component, and no component ending in .lock or a dot."""
    if not name or not REF_NAME_RE.match(name):
        return False
    parts = name.split('/')
    return all(p and not p.startswith('.') and not p.endswith('.') and not p.endswith('.lock') for p in parts)


def per_worktree_ref(name):
    """True for a ref git keeps in each worktree's own git directory: HEAD, a <NAME>_HEAD
    pseudoref, refs/bisect/, refs/worktree/ and refs/rewritten/."""
    return name == 'HEAD' or bool(PSEUDOREF_RE.match(name)) or name.startswith(PER_WORKTREE_REFS)


def scratch_ref(name):
    """True for a ref any agent session may write or delete from any checkout: refs/tmp/<name>
    and the per-worktree refs but HEAD."""
    return ref_name_ok(name) and name != 'HEAD' and (name.startswith('refs/tmp/') or per_worktree_ref(name))


# the main sessions that may delete a stray ref in their own repository (the coordinator only),
# and the refs that are no stray ref, compared without case: NTFS reads refs/HEADS/main as the
# loose file of refs/heads/main
REF_CLEANUP_MARKS = ('coordinator',)
NOT_STRAY = ('refs/heads', 'refs/tags', 'refs/stash', 'refs/backlog')


def stray_ref(name):
    """True for a ref a coordinator may delete in its own repository: a ref_name_ok name under
    refs/, outside refs/heads, refs/tags, refs/stash (every worktree's stash entries) and
    refs/backlog (the backlog store, in any case)."""
    low = (name or '').lower()
    return ref_name_ok(name) and low.startswith('refs/') and not low.startswith(NOT_STRAY)


def cleanup_session(event, ctx):
    """True for a main session whose mark (session_mark: None for every subagent, a call a host
    cannot tell apart included) is one of REF_CLEANUP_MARKS, case ignored."""
    return str(session_mark(event, ctx) or '').lower() in REF_CLEANUP_MARKS


def _deref_unsure(place, name):
    """Why `git update-ref -d <name>` without --no-deref may delete another ref (git follows a
    symbolic ref and deletes where it points), or None: a reftable repository, where the guard
    reads no ref file, a loose ref file that does not read, or one that is a symbolic ref."""
    if os.path.isdir(os.path.join(place.common, 'reftable')):
        return 'its refs are in a reftable, where the guard cannot tell a symbolic ref'
    text, why = _read_ref(os.path.join(place.common, *name.split('/')))
    if text is None:
        return why
    if text.startswith('ref:'):
        return 'it is a symbolic ref to %s, and git deletes that ref instead' % text[4:].strip()
    return None


def main_names(ctx, where):
    """The guarded mains of the repository at `where`, lower-cased, main and master always among
    them (a missing or unreadable .kit.json guards those two)."""
    names = {'main', 'master'}
    cfg = ctx.config(where) if where and os.path.isabs(where) else None
    if cfg is not None:
        names.update(str(b).lower() for b in _guarded_mains(cfg) if b)
    return names


def shared_ref_held(event, ctx):
    """True for a session git_shared_ref holds: an agent session (agent_session) but the PM's."""
    return agent_session(event, ctx) and str(session_mark(event, ctx) or '').lower() != 'pm'


def _in_temp(ctx, path):
    """True when `path` lies in a temp root (jail.temp_roots). It lifts checks, so it compares with
    the jail's strict fold (jail.real strict: on Linux a mount in doubt does not fold)."""
    strict = paths.posix()
    real = jail.real(path, strict)
    return any(jail.inside(real, jail.real(r, strict)) for r in jail.temp_roots(ctx))


def _scratch_repo(ctx, place):
    """True when `place` is a scratch repository: its git directories lie in a temp root
    (jail.temp_roots) and no link inside them may carry a ref write out. Every entry but what
    objects/ holds is read, at most SCRATCH_WALK_MAX; any symlink, junction or other reparse
    point, an entry that does not read, or a longer walk answers False, and the rule judges the
    repository as any other. A hard link is not seen: git writes a ref file and packed-refs to a
    lock file it renames over the old one, so a write does not go through a hard link."""
    if place is None or not place.common:
        return False
    roots = [place.common]
    if place.git_dir and not jail.inside(jail.real(place.git_dir), jail.real(place.common)):
        roots.append(place.git_dir)
    if not all(_in_temp(ctx, r) for r in roots):
        return False
    seen, stack = 0, list(roots)
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
            for e in entries:
                seen += 1
                if seen > SCRATCH_WALK_MAX or e.is_symlink() or \
                        getattr(e.stat(follow_symlinks=False), 'st_file_attributes', 0) & REPARSE_POINT:
                    return False
                if e.is_dir(follow_symlinks=False) and not (d in roots and e.name.lower() == 'objects'):
                    stack.append(e.path)
        except OSError:
            return False
    return True


def _read_ref(path):
    """(text of the loose ref file, None); ('', None) when there is none; (None, why) when it
    cannot be read."""
    try:
        with open(path, 'rb') as f:
            return f.read(4096).decode('utf-8', 'replace').strip(), None
    except FileNotFoundError:
        return '', None
    except OSError as e:
        return None, 'its file cannot be read (%s)' % e


def _head_ref(place):
    """The ref the HEAD file of `place`'s git directory names, '' when HEAD is detached, None when
    there is no such file or it cannot be read."""
    if not place.git_dir:
        return None
    text, _ = _read_ref(os.path.join(place.git_dir, 'HEAD'))
    if not text:
        return None
    return text[4:].strip() if text.startswith('ref:') else ''


def _own_branch(ctx, place, at):
    """The branch checked out in the linked worktree `place` (refs/heads/<name>), or None: a
    detached or unreadable HEAD, a guarded main, the PM data worktree or one the guard cannot tell."""
    if place.kind != repo.LINKED or not place.git_dir or ctx.is_pm_data(at) or ctx.pm_data_error(at):
        return None
    own = _head_ref(place)
    if not own or not own.startswith('refs/heads/') or not ref_name_ok(own):
        return None
    if own[len('refs/heads/'):].lower() in main_names(ctx, at):
        return None
    return own


def _ref_ok(name, delete, linked, own):
    if scratch_ref(name):
        return True
    if delete or not linked:
        return False
    return name == 'HEAD' or (own is not None and name == own)


def _update_ref_unread(gc, short, longs, pos):
    """Why the guard cannot tell which ref `git update-ref` writes, or None."""
    if gc.inv is not None and gc.inv.via == 'xargs':
        return 'xargs adds words the text does not show'
    if 'stdin' in longs:
        return 'it reads the refs to write from its input'
    unknown = sorted(longs - set(GIT_LONGS['update-ref']))
    if unknown:
        return 'it gives an option the guard does not read (--%s)' % unknown[0]
    odd = sorted(short - UPDATE_REF_SHORTS)
    if odd:
        return 'it gives an option the guard does not read (-%s)' % odd[0]
    if not pos:
        return 'it names no ref'
    for w in gc.rest:
        if w.dyn or w.alt is not None or w.synth:
            return 'a word before the ref holds something only the shell knows (%s)' % w.text
        if w is pos[0]:
            break
    if not ref_name_ok(pos[0].text):
        return '%r is not a ref name the guard reads' % pos[0].text
    return None


def _stray_delete(ctx, gc, cwd, place, name, longs, text, top, role):
    """A coordinator's `git update-ref -d <stray_ref>`: None (allowed) in its own repository (the
    main checkout `top` of the place git runs in is the one of its session's folder `cwd`) when
    git deletes that ref itself (--no-deref, or _deref_unsure finds no other ref it may reach);
    else the refusal."""
    mine = ctx.main_top(cwd) if cwd else None
    at = quote(top or gc.dir)
    if not top or not mine or paths.norm(mine) != paths.norm(top):
        return refuse('git-shared-ref', role, '`%s` deletes %s in a repository other than the one your session '
                      'runs in (%s): a coordinator deletes a stray ref only in its own repository, and that '
                      'repository\'s coordinator deletes this one. To show it:' % (text, name, fwd(mine or cwd or '?')),
                      'git -C %s show-ref %s' % (at, name))
    why = None if 'no-deref' in longs else _deref_unsure(place, name)
    if why is None:
        return None
    return refuse('git-shared-ref', role, '`%s` may delete another ref than %s: %s. Delete the ref itself with '
                  '--no-deref:' % (text, name, why), 'git -C %s update-ref --no-deref -d %s' % (at, name))


SHARED_REFS = 'every worktree of this repository shares its refs, so a change made from any of them is made for all'
SHARED_REF_ALLOWED = 'An agent session writes refs/tmp/<name> (its scratch space), the per-worktree refs ' \
                     '(refs/bisect/, refs/worktree/, refs/rewritten/, <NAME>_HEAD) and, in its own worktree, HEAD ' \
                     'and the branch checked out there; a coordinator deletes a ref outside refs/heads, refs/tags, ' \
                     'refs/stash and refs/backlog in its own repository; any other ref is the PM\'s.'


def _shared_scene(ctx, gc, cwd):
    """(place, top, at, why) of a ref write by git `gc`: the Place git runs in (None when the text
    does not name it), its main checkout, the checkout a refusal's Instead names (the session's own
    worktree of it when it has one), and why the guard cannot judge that place (None when it can)."""
    why = None
    if gc.blind or gc.unknown or not gc.dir:
        why = 'the repository it runs in is unknown (%s)' % (gc.blind or 'a directory the text does not name')
    place = ctx.place(gc.dir) if gc.dir and not gc.unknown and not gc.blind else None
    if not why and place.kind not in (repo.MAIN, repo.LINKED):
        why = 'git cannot place %s (%s)' % (fwd(gc.dir), place.detail or place.kind)
    top = (ctx.main_top(gc.dir) if gc.dir and not gc.unknown else None) or (place.top if place else None)
    at = own_worktree(ctx, cwd, top) if top else own_worktree(ctx, cwd)
    at = at or top or (ctx.main_top(cwd) if cwd else None) or cwd
    return place, top, at, why


def git_shared_ref(event, ctx, cmd, gc, cwd, role):
    """`git update-ref` by a held session (shared_ref_held), from any checkout: every worktree of a
    repository shares its refs, so a worktree reaches main as surely as the main checkout. It may
    write scratch_ref names, and in a linked worktree its HEAD and the branch checked out there
    (never a guarded main); it deletes only scratch_ref names, and a coordinator's main session
    (cleanup_session) a stray_ref in its own repository too (_stray_delete). A ref is judged by its name; HEAD
    (unless --no-deref) by the branch it is on too, so a worktree on a guarded main does not move
    it. A scratch repository (_scratch_repo: in a temp root, no
    link out of its git directory) is not judged. Neither the PM data worktree nor, while the
    guard cannot tell it (ctx.pm_data_error), any linked worktree gets the HEAD allowance."""
    if not shared_ref_held(event, ctx):
        return None
    text = bare(cmd)
    short, longs, pos = _opts('update-ref', gc.rest)
    delete = 'd' in short
    unread = _update_ref_unread(gc, short, longs, pos)
    after = False
    for w in gc.rest:
        if after and (w.dyn or w.alt is not None or w.synth):
            delete = True                       # a word only the shell knows may be -d
        after = after or (bool(pos) and w is pos[0])
    place, top, at, where = _shared_scene(ctx, gc, cwd)
    if _scratch_repo(ctx, place):
        return None
    unread = unread or where
    shared, allowed = SHARED_REFS, SHARED_REF_ALLOWED
    cleanup = cleanup_session(event, ctx)
    if unread:
        hint = ref_delete_hint(ctx, gc, top or at, cwd, short, longs, pos, cleanup) if 'd' in short else None
        if hint:
            return refuse('git-shared-ref', role, '`%s`: %s, and %s. %s' % (text, unread, shared, hint[0]), hint[1])
        return refuse('git-shared-ref', role, '`%s`: the guard cannot tell which ref it writes: %s; %s. Name one '
                      'ref literally. %s To keep a commit in your scratch space:' % (text, unread, shared, allowed),
                      'git -C %s update-ref refs/tmp/<name> <new-oid>' % quote(at))
    name = pos[0].text
    # the PM data worktree answers as MAIN; while the guard cannot tell it, any linked worktree may be it
    linked = place.kind == repo.LINKED and not ctx.pm_data_error(gc.dir)
    own = _own_branch(ctx, place, gc.dir)
    chain, why = [name], None
    if name == 'HEAD' and 'no-deref' not in longs:
        head = _head_ref(place)
        if head is None:
            chain, why = None, 'the HEAD file of %s cannot be read' % fwd(gc.dir)
        elif head:
            chain.append(head)
    if chain is not None:
        bad = [r for r in chain if not _ref_ok(r, delete, linked, own)]
        if not bad:
            return None
        if cleanup and 'd' in short and stray_ref(name):
            return _stray_delete(ctx, gc, cwd, place, name, longs, text, top, role)
        why = '%s is not one it may %s' % (bad[0] if bad[0] == name else '%s, where %s points,' % (bad[0], name),
                                           'delete' if delete else 'write')
    hint = ref_delete_hint(ctx, gc, top or at, cwd, short, longs, pos, cleanup) if 'd' in short else None
    if hint:
        return refuse('git-shared-ref', role, '`%s` deletes %s: %s. %s' % (text, name, why, hint[0]), hint[1])
    return refuse('git-shared-ref', role, '`%s` %s %s: %s; %s. %s To show it:' % (
        text, 'deletes' if delete else 'moves', name, why, shared, allowed), 'git -C %s show-ref %s' % (quote(at), name))


# ---------------------------------------------------------------- other git forms that write refs
# git-shared-ref judges these as it judges update-ref (_judge_refs): a push into the repository
# itself (git_shared_push), a fetch or pull refspec's destination (a forced `tag <name>` too),
# symbolic-ref with two names or -d, replace, a notes write, a forced or moving branch, a forced
# or deleted tag, checkout -B and switch -C, --ignore-other-worktrees, worktree add -B or -f,
# rebase --update-refs, a remote's remove, rename, set-head or mirror add, fast-import and
# filter-branch (git_shared_ref_forms; branch after git_branch_delete, worktree after
# git_worktree_remove). Known limits (docs/guard.md "What git-shared-ref does not read"): refspecs, remotes and
# rebase.updateRefs git takes from its config files, a named remote whose URL is this repository,
# a fetch or pull refspec the shell computes whole, a push into another local repository, rebase
# <upstream> <branch> (it moves that branch), git replay and filter-repo.
SHARED_REF_FORMS = ('fetch', 'pull', 'symbolic-ref', 'replace', 'notes', 'tag', 'checkout', 'switch', 'rebase',
                    'remote', 'fast-import', 'filter-branch')
# per form: (long flags, long options that take a value, short flags, short options that take a
# value, short options whose value may only be attached)
FETCH_FLAGS = ('all', 'append', 'atomic', 'dry-run', 'write-fetch-head', 'force', 'keep', 'multiple',
               'auto-maintenance', 'auto-gc', 'write-commit-graph', 'prefetch', 'prune', 'prune-tags', 'tags',
               'recurse-submodules', 'quiet', 'verbose', 'progress', 'update-head-ok', 'update-shallow',
               'unshallow', 'ipv4', 'ipv6', 'show-forced-updates', 'set-upstream', 'stdin', 'porcelain',
               'negotiate-only')
FETCH_VALUES = ('upload-pack', 'jobs', 'depth', 'deepen', 'shallow-since', 'shallow-exclude', 'refmap',
                'server-option', 'negotiation-tip', 'filter', 'submodule-prefix', 'recurse-submodules-default')
PULL_FLAGS = FETCH_FLAGS + ('commit', 'edit', 'ff', 'ff-only', 'squash', 'stat', 'summary', 'compact-summary',
                            'verify', 'verify-signatures', 'autostash', 'allow-unrelated-histories', 'rebase',
                            'signoff', 'log', 'gpg-sign')
PULL_VALUES = FETCH_VALUES + ('strategy', 'strategy-option', 'cleanup')
BRANCH_FLAGS = ('delete', 'move', 'copy', 'force', 'list', 'all', 'remotes', 'verbose', 'quiet', 'track',
                'unset-upstream', 'color', 'abbrev', 'column', 'omit-empty', 'show-current', 'create-reflog',
                'edit-description', 'ignore-case', 'recurse-submodules')
BRANCH_VALUES = ('set-upstream-to', 'contains', 'no-contains', 'merged', 'no-merged', 'sort', 'points-at', 'format')
TAG_FLAGS = ('list', 'delete', 'verify', 'annotate', 'sign', 'edit', 'force', 'create-reflog', 'color', 'column',
             'omit-empty', 'ignore-case')
TAG_VALUES = ('message', 'file', 'trailer', 'cleanup', 'local-user', 'contains', 'no-contains', 'merged',
              'no-merged', 'sort', 'points-at', 'format')
CHECKOUT_FLAGS = ('quiet', 'progress', 'merge', 'detach', 'force', 'ours', 'theirs', 'patch', 'guess', 'overlay',
                  'overwrite-ignore', 'ignore-other-worktrees', 'ignore-skip-worktree-bits', 'pathspec-file-nul',
                  'recurse-submodules', 'track', 'auto-advance')
CHECKOUT_VALUES = ('conflict', 'orphan', 'unified', 'inter-hunk-context', 'pathspec-from-file')
SWITCH_FLAGS = ('guess', 'discard-changes', 'quiet', 'recurse-submodules', 'progress', 'merge', 'detach', 'force',
                'overwrite-ignore', 'ignore-other-worktrees', 'track', 'auto-advance')
SWITCH_VALUES = ('create', 'force-create', 'conflict', 'orphan')
REBASE_FLAGS = ('keep-base', 'verify', 'quiet', 'verbose', 'stat', 'signoff', 'committer-date-is-author-date',
                'reset-author-date', 'ignore-whitespace', 'force-rebase', 'ff', 'continue', 'skip', 'abort', 'quit',
                'edit-todo', 'show-current-patch', 'apply', 'merge', 'interactive', 'rerere-autoupdate',
                'autosquash', 'update-refs', 'gpg-sign', 'autostash', 'rebase-merges', 'fork-point', 'root',
                'reschedule-failed-exec', 'reapply-cherry-picks', 'keep-empty')
REBASE_VALUES = ('onto', 'trailer', 'whitespace', 'empty', 'exec', 'strategy', 'strategy-option')
WORKTREE_ADD_FLAGS = ('force', 'detach', 'checkout', 'lock', 'orphan', 'quiet', 'track', 'guess-remote',
                      'relative-paths')
REF_FORM_OPTS = {'fetch': (FETCH_FLAGS, FETCH_VALUES, 'afkmnPpqtuv46', 'jo', ''),
                 'pull': (PULL_FLAGS, PULL_VALUES, 'aefknpqrtuv46', 'joXs', 'S'),
                 'symbolic-ref': (('quiet', 'delete', 'short', 'recurse', 'no-recurse'), (), 'qd', 'm', ''),
                 'branch': (BRANCH_FLAGS, BRANCH_VALUES, 'vqrladDmMcCfi', 'u', 't'),
                 'tag': (TAG_FLAGS, TAG_VALUES, 'ldvasefi', 'mFu', 'n'),
                 'checkout': (CHECKOUT_FLAGS, CHECKOUT_VALUES, 'qmdf23plt', 'bBU', ''),
                 'switch': (SWITCH_FLAGS, SWITCH_VALUES, 'qmdft', 'cC', ''),
                 'rebase': (REBASE_FLAGS, REBASE_VALUES, 'qvnfmik', 'xsXC', 'Sr'),
                 'worktree add': (WORKTREE_ADD_FLAGS, ('reason',), 'fdq', 'bB', ''),
                 'remote add': (('fetch', 'tags', 'mirror'), ('track', 'master'), 'f', 'tm', '')}
# git replace that only lists: these options (and --format=<f>), with a pattern only after -l
REPLACE_LIST_OPTS = ('-l', '--list')
NOTES_READ_VERBS = (None, 'list', 'show', 'get-ref')
REMOTE_FETCH_KEY_RE = re.compile(r'^remote\.(.+)\.fetch$')
PRUNE_TAGS_KEY_RE = re.compile(r'^(?:fetch|remote\..+)\.prunetags$')
FALSE_WORDS = ('false', 'no', 'off', '0')


def _computed(w):
    """True for a Word whose text is not what git gets: computed, an alias's, or one bash changes."""
    return bool(w.dyn or w.alt is not None or w.synth)


def _long_hit(known, body):
    """(name, negated) of the long option `body` (the text after `--`, before any `=`) among the
    names `known`: its exact name, a `no-` one, or the one name it abbreviates; None when it names
    none or more than one."""
    neg = body.startswith('no-') and body not in known
    stem = body[3:] if neg else body
    hits = [stem] if stem in known else [n for n in known if n.startswith(stem)] if stem else []
    return (hits[0], neg) if len(hits) == 1 else None


def _form_words(sub, args):
    """(short flags, {long name: [(value text, computed)]}, positional Words, unread option texts)
    of git `sub`'s arguments by REF_FORM_OPTS: a long option is its exact name, a `no-` one, or the
    one name it abbreviates; one the table does not name, or an ambiguous prefix, is unread."""
    return _form_scan(sub, args)[:4]


def _form_scan(sub, args):
    """_form_words, and the option names in the order given (a long one by name, a negated one as
    no-<name>, a short flag as -<letter>) and how many positional words come before a `--`."""
    flags, values, s_flags, s_values, s_attached = REF_FORM_OPTS[sub]
    known = set(flags) | set(values)
    shorts, longs, pos, unread, seq = set(), {}, [], [], []
    k = 0
    npre = None
    while k < len(args):
        w = args[k]
        t = w.text
        k += 1
        if t == '--':
            npre = len(pos)
            pos.extend(args[k:])
            break
        if t.startswith('--'):
            body, eq, v = t[2:].partition('=')
            hit = _long_hit(known, body)
            if hit is None:
                unread.append(t)
                continue
            name, neg = hit
            seq.append('no-' + name if neg else name)
            if name in values and not neg:
                if not eq and k < len(args):
                    v, computed = args[k].text, _computed(args[k])
                    k += 1
                else:
                    computed = _computed(w)
                longs.setdefault(name, []).append((v, computed))
            else:
                longs.setdefault('no-' + name if neg else name, []).append((v, _computed(w)))
            continue
        if t.startswith('-') and len(t) > 1:
            body = t[1:]
            for j, c in enumerate(body):
                if c in s_values:
                    if not body[j + 1:] and k < len(args):
                        longs.setdefault('-' + c, []).append((args[k].text, _computed(args[k])))
                        k += 1
                    else:
                        longs.setdefault('-' + c, []).append((body[j + 1:], _computed(w)))
                    break
                if c in s_attached:
                    break
                if c in s_flags:
                    shorts.add(c)
                    seq.append('-' + c)
                else:
                    unread.append('-' + c)
            continue
        pos.append(w)
    return shorts, longs, pos, unread, seq, len(pos) if npre is None else npre


def fetch_dst_ref(dst):
    """The local ref a fetch refspec's destination names (git's get_local_ref): a refs/ name as
    it is, heads/, tags/ and remotes/ under refs/, any other name a branch."""
    if dst.startswith('refs/'):
        return dst
    if dst.startswith(('heads/', 'tags/', 'remotes/')):
        return 'refs/' + dst
    return 'refs/heads/' + dst


def _tracking_ref(src, dst, remote):
    """True for the remote-tracking map a fetch makes: `dst` refs/remotes/<remote>/<x> (remote and
    folder compared without case) from `src` <x> or refs/heads/<x>, one `*` in both allowed; <x>
    never HEAD."""
    if not remote or not ref_name_ok(remote):
        return False
    prefix = 'refs/remotes/%s/' % remote
    if dst[:len(prefix)].lower() != prefix.lower():
        return False
    x = dst[len(prefix):]
    if not x or x == 'HEAD' or x.count('*') > 1 or not ref_name_ok(x.replace('*', 'x')):
        return False
    return src in (x, 'refs/heads/' + x)


def fetch_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None): the refs git fetch or pull `gc` writes
    beyond FETCH_HEAD and the remote-tracking map (_tracking_ref): each refspec destination after
    the remote (fetch_dst_ref; a `^` exclusion and an empty destination write nothing), each
    --refmap and inline -c remote.<name>.fetch mapping, local tags under --prune-tags (or an inline
    *.pruneTags), a forced --tags and a forced `tag <name>`. Unread: --stdin, a refspec the shell
    computes with a literal `:` or `+`, a forced `tag` whose name it computes, an option the guard
    does not read on a line with a `:` word."""
    shorts, longs, pos, unknown = _form_words(gc.sub, gc.rest)
    remote = pos[0] if pos else None
    rname = remote.text if remote is not None and not _computed(remote) else None
    # with --multiple every word names a remote (and --all takes none)
    many = 'm' in shorts or 'multiple' in longs
    specs, tagged = [], []          # tagged: (name text, computed) of each `tag <name>` (refs/tags/<name>)
    words = [] if many else pos[1:]
    j = 0
    while j < len(words):
        w = words[j]
        if w.text == 'tag' and not _computed(w) and j + 1 < len(words):
            tagged.append((words[j + 1].text, _computed(words[j + 1])))
            j += 2
            continue
        specs.append((w.text, _computed(w), rname))
        j += 1
    specs += [(v, c, rname) for v, c in longs.get('refmap', ())]
    if gc.inv is not None:
        for key, val, first, last in git_prefix_configs(gc):
            # --config-env names a variable that holds the value: the text is not the value
            computed = _computed(last) or first.text.startswith('--config-env')
            m = REMOTE_FETCH_KEY_RE.match(key)
            if m:
                specs.append((val, computed, m.group(1)))
            elif PRUNE_TAGS_KEY_RE.match(key) and (computed or val.strip().lower() not in FALSE_WORDS):
                longs.setdefault('prune-tags', []).append((val, computed))
    if 'stdin' in longs:
        return [], 'it reads refspecs from its input'
    if unknown and (any(':' in w.text for w in pos) or any(':' in t for t in unknown)):
        return [], 'it gives an option the guard does not read (%s)' % unknown[0]
    instead = 'Fetch into a remote-tracking ref (refs/remotes/<remote>/<branch>) or your scratch space:'
    out = []
    for text, computed, rem in specs:
        spec = text.strip('"\'') if computed else text
        if computed and (':' in spec or spec.startswith('+')):
            return [], 'a refspec holds something only the shell knows (%s)' % text
        if computed:
            continue                            # a refspec computed whole: a known limit
        spec = spec[1:] if spec.startswith('+') else spec
        src, colon, dst = spec.partition(':')
        if spec.startswith('^') or not colon or not dst:
            continue
        ref = fetch_dst_ref(dst)
        if _tracking_ref(src, ref, rem):
            continue
        out.append((ref, False, None, (instead, 'git -C <at> fetch %s %s:refs/tmp/<name>' % (rem or '<remote>',
                                                                                             src or 'HEAD'))))
    forced = 'f' in shorts or 'force' in longs
    for name, computed in tagged if forced else ():
        # unforced, git refuses to move a tag that is there; forced, it moves it
        if computed:
            return [], 'a tag name holds something only the shell knows (%s)' % name
        out.append(('refs/tags/' + name, False, 'moves the tag %s to the remote\'s' % name,
                    ('Fetch it into your scratch space:', 'git -C <at> fetch %s %s' % (
                        rname or '<remote>', spelt('refs/tags/%s:refs/tmp/%s' % (name, name))))))
    tags = ('without --prune-tags or a forced --tags (a fetch never moves a tag you have):',
            'git -C <at> fetch %s' % (rname or '<remote>'))
    if 'P' in shorts or 'prune-tags' in longs:
        out.append(('refs/tags/*', True, 'deletes every tag the remote does not have', ('Fetch ' + tags[0], tags[1])))
    if ('t' in shorts or 'tags' in longs) and ('f' in shorts or 'force' in longs):
        out.append(('refs/tags/*', False, 'moves every tag to the remote\'s', ('Fetch ' + tags[0], tags[1])))
    return out, None


def symbolic_ref_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git symbolic-ref `gc`: none for a
    read (one name, no -d); with -d the name, deleted; with two names the name, written (HEAD
    itself: symbolic-ref does not follow it), and the ref it then points at, since later writes go
    through it. A computed word or an option the guard does not read is unread."""
    shorts, longs, pos, unknown = _form_words('symbolic-ref', gc.rest)
    delete = 'd' in shorts or 'delete' in longs
    if any(_computed(w) for w in gc.rest):
        return [], 'a word holds something only the shell knows (%s)' % next(w.text for w in gc.rest if _computed(w))
    if unknown:
        return [], 'it gives an option the guard does not read (%s)' % unknown[0]
    if not pos or (len(pos) < 2 and not delete):
        return [], None
    name = pos[0].text
    switch = ('To put your worktree on another branch:', 'git -C <at> switch <branch>') if name == 'HEAD' else None
    if delete:
        return [(name, True, None, switch)], None
    target = pos[1].text
    return [(name, False, None, switch), (target, False, 'points %s at %s, so later writes through it move it' % (
        name, target), switch)], None


def replace_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git replace `gc`: none when it only
    lists (no word, or REPLACE_LIST_OPTS and --format=<f> with a pattern only after -l); else the
    refs/replace/<object> ref it writes or deletes (-d)."""
    words = [w.text for w in gc.rest]
    if any(_computed(w) for w in gc.rest):
        return [], 'a word holds something only the shell knows (%s)' % next(w.text for w in gc.rest if _computed(w))
    opts = [t for t in words if t.startswith('-')]
    objs = [t for t in words if not t.startswith('-')]
    if all(t in REPLACE_LIST_OPTS or t.startswith('--format=') for t in opts) and \
            (not objs or any(t in REPLACE_LIST_OPTS for t in opts)):
        return [], None
    delete = '-d' in opts or any(len(t) > 2 and '--delete'.startswith(t) for t in opts)
    ref = 'refs/replace/%s' % (objs[0] if objs else '<object>')
    return [(ref, delete, None, ('To list the replace refs:', 'git -C <at> replace -l'))], None


def notes_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git notes `gc`: none for a verb in
    NOTES_READ_VERBS; any other writes the notes ref (--ref <ref> under refs/notes/, else
    refs/notes/commits). A computed verb, or an option before it other than --ref, is unread."""
    args, k, ref = gc.rest, 0, 'refs/notes/commits'
    while k < len(args) and args[k].text.startswith('-'):
        w = args[k]
        body, eq, v = w.text[2:].partition('=')
        if w.text == '--' or not w.text.startswith('--') or not body or not 'ref'.startswith(body):
            return [], 'it gives an option the guard does not read (%s)' % w.text
        if not eq:
            k += 1
            v = args[k].text if k < len(args) else ''
        ref = v if v.startswith('refs/notes/') else 'refs/' + v if v.startswith('notes/') else 'refs/notes/' + v
        k += 1
    verb = args[k] if k < len(args) else None
    if verb is not None and _computed(verb):
        return [], 'its verb holds something only the shell knows (%s)' % verb.text
    if (verb.text if verb is not None else None) in NOTES_READ_VERBS:
        return [], None
    return [(ref, verb.text == 'remove', None, ('To show the notes:', 'git -C <at> notes list'))], None


def _first_computed(words):
    """'a word holds something only the shell knows (<text>)' for the first computed Word of
    `words`, or None."""
    for w in words:
        if _computed(w):
            return 'a word holds something only the shell knows (%s)' % w.text
    return None


def _last_of(seq, names):
    """The last of `names` in `seq` (_form_scan's option names in order), or None."""
    for n in reversed(seq):
        if n in names:
            return n
    return None


def branch_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git branch `gc`: a move (-m, -M,
    --move) deletes the old name (with one name, the branch HEAD names where git runs: none when it
    is detached, unread when the guard cannot read it) and writes the new; a forced copy (-C, or
    -c with -f) writes the new name; -f with a name writes it. A
    plain create, an unforced copy, -d and -D (git_branch_delete judges -D), a listing and the
    other modes write no ref here. A computed word, or an option the guard does not read, in a
    form that writes is unread."""
    shorts, longs, pos, unknown = _form_words('branch', gc.rest)
    move = bool(shorts & set('mM')) or 'move' in longs
    copy = bool(shorts & set('cC')) or 'copy' in longs
    force = bool(shorts & set('fMC')) or 'force' in longs
    if not pos or not (move or force):
        return [], None
    others = {'delete', 'list', 'edit-description', 'show-current', 'set-upstream-to', 'unset-upstream',
              'contains', 'no-contains', 'merged', 'no-merged', 'points-at'}
    if shorts & set('dDlu') or others & set(longs) or '-u' in longs:
        return [], None
    why = _first_computed(gc.rest)
    if why or unknown:
        return [], why or 'it gives an option the guard does not read (%s)' % unknown[0]
    names = [w.text for w in pos]
    if move or copy:
        old, new = (names[0], names[1]) if len(names) > 1 else (None, names[0])
        new_ref = 'refs/heads/' + new
        if move:
            if old is None:
                # one name: git renames the branch checked out where it runs (HEAD's), and a
                # detached HEAD has none to rename
                cur = _head_ref(place) if place is not None else None
                if cur == '':
                    return [], None
                if not cur or not cur.startswith('refs/heads/') or not ref_name_ok(cur):
                    return [], 'it renames the branch checked out where git runs, and the guard cannot read its HEAD'
                old = cur[len('refs/heads/'):]
            make = ('To make a branch of the new name and leave %s as it is:' % old,
                    'git -C <at> branch %s %s' % (spelt(new), spelt(old)))
            return [('refs/heads/' + old, True, 'renames refs/heads/%s to %s, deleting the old name' % (old, new_ref),
                     make),
                    (new_ref, False, None, make)], None
        copy_to = ('To copy it to a new name:', 'git -C <at> branch -c %s<new-name>' % (spelt(old) + ' ' if old else ''))
        return [(new_ref, False, 'writes over %s with a copy' % new_ref, copy_to)], None
    start = ' ' + spelt(names[1]) if len(names) > 1 else ''
    return [('refs/heads/' + names[0], False, 'moves refs/heads/%s' % names[0],
             ('To make a new branch there:', 'git -C <at> branch <new-name>%s' % start))], None


def tag_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git tag `gc`: -d deletes each tag
    named; -f with a name writes it. A plain create, a listing and -v write no ref here."""
    shorts, longs, pos, unknown = _form_words('tag', gc.rest)
    delete = 'd' in shorts or 'delete' in longs
    force = 'f' in shorts or 'force' in longs
    if not pos or not (delete or force) or 'l' in shorts or 'list' in longs or 'v' in shorts or 'verify' in longs:
        return [], None
    why = _first_computed(gc.rest)
    if why or unknown:
        return [], why or 'it gives an option the guard does not read (%s)' % unknown[0]
    if delete:
        return [('refs/tags/' + w.text, True, None, None) for w in pos], None
    start = ' ' + spelt(pos[1].text) if len(pos) > 1 else ''
    return [('refs/tags/' + pos[0].text, False, 'moves refs/tags/%s' % pos[0].text,
             ('To make a new tag there:', 'git -C <at> tag <new-name>%s' % start))], None


def _branch_seen(place, name):
    """True when the repository of `place` (the Place git runs in) has branch `name`: a loose
    refs/heads/<name>, a packed-refs line, or a worktree's HEAD naming it; False when it has none
    of them; None when the guard cannot tell (no place or common dir, or a reftable store)."""
    common = place.common if place is not None else None
    if not common or os.path.isdir(os.path.join(common, 'reftable')):
        return None
    ref = 'refs/heads/' + name
    if os.path.isfile(os.path.join(common, *ref.split('/'))) or ref in (gitdefaults._packed(common) or ()):
        return True
    heads = [os.path.join(common, 'HEAD')]
    try:
        heads += [os.path.join(common, 'worktrees', n, 'HEAD') for n in os.listdir(os.path.join(common, 'worktrees'))]
    except OSError:
        pass
    return any((gitdefaults._read(h, 4096) or '').strip() == 'ref: ' + ref for h in heads)


def _checked_out(word, place):
    """([(ref, ...)], why) for the branch `word` that --ignore-other-worktrees (or worktree add
    -f) checks out though another worktree has it, git running in `place`: refs/heads/<name> when the
    repository has that branch (_branch_seen; one the guard cannot tell counts); none for a word
    that names no branch there (a tag, a commit, a new branch git makes from a remote's), HEAD or a
    commit expression; `-` and @{...} name a branch the text does not show."""
    t = word.text
    if _computed(word):
        return None, 'a word holds something only the shell knows (%s)' % t
    detach = ('To check it out detached, so no branch moves with your commits:', 'git -C <at> switch --detach %s'
              % spelt(t))
    if t == '-' or t.startswith('@{'):
        return ('the branch %s names' % t, detach), None
    if t == 'HEAD' or not ref_name_ok(t) or _branch_seen(place, t) is False:
        return None, None
    return ('refs/heads/' + t, detach), None


def checkout_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git checkout or switch `gc`: -B
    (checkout) or -C/--force-create (switch) writes the branch it names; the last of
    --ignore-other-worktrees and its no- form, with no branch option or --detach, checks out the
    branch named (_checked_out) though another worktree has it, so commits here move it."""
    shorts, longs, pos, unknown, seq, npre = _form_scan(gc.sub, gc.rest)
    if gc.sub == 'checkout':
        forced = longs.get('-B') or []
    else:
        forced = (longs.get('-C') or []) + (longs.get('force-create') or [])
    start = ' ' + spelt(pos[0].text) if pos and not _computed(pos[0]) else ''
    if forced:
        v, computed = forced[-1]
        if computed:
            return [], 'a branch name holds something only the shell knows (%s)' % v
        return [('refs/heads/' + v, False, 'resets refs/heads/%s' % v,
                 ('To make a new branch and switch to it:', 'git -C <at> switch -c <new-name>%s' % start))], None
    if _last_of(seq, ('ignore-other-worktrees', 'no-ignore-other-worktrees')) != 'ignore-other-worktrees':
        return [], None
    if set(longs) & {'-b', '-c', 'create', 'orphan', 'detach'} or 'd' in shorts or not npre:
        return [], None
    hit, why = _checked_out(pos[0], place)
    if why or not hit:
        return [], why
    return [(hit[0], False, 'checks out %s though another worktree may have it, so commits here move it' % hit[0],
             hit[1])], None


def worktree_add_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git worktree add `gc`: -B writes
    the branch it names; -f with no -b, -B, --detach or --orphan checks out the branch named (the
    commit-ish, else the path's last part) though another worktree may have it. Any other worktree
    verb, and a plain add (-b <new>, or an existing branch git refuses when it is out elsewhere),
    write no ref here."""
    args = gc.rest
    if not args or args[0].text != 'add' or _computed(args[0]):
        return [], None
    shorts, longs, pos, unknown, seq, npre = _form_scan('worktree add', args[1:])
    force = 'f' in shorts or 'force' in longs
    path = spelt(pos[0].text) if pos and not _computed(pos[0]) else '<path>'
    if '-B' in longs:
        v, computed = longs['-B'][-1]
        if computed:
            return [], 'a branch name holds something only the shell knows (%s)' % v
        start = ' ' + spelt(pos[1].text) if len(pos) > 1 and not _computed(pos[1]) else ''
        return [('refs/heads/' + v, False, 'resets refs/heads/%s' % v,
                 ('To make a new branch for it:', 'git -C <at> worktree add -b <new-name> %s%s' % (path, start)))], None
    if not force or not pos or '-b' in longs or 'd' in shorts or set(longs) & {'detach', 'orphan'}:
        return [], None
    if unknown:
        return [], 'it gives an option the guard does not read (%s)' % unknown[0]
    if len(pos) > 1:
        hit, why = _checked_out(pos[1], place)
    elif _computed(pos[0]):
        hit, why = None, 'a word holds something only the shell knows (%s)' % pos[0].text
    else:
        base = re.split(r'[\\/]', pos[0].text.rstrip('\\/'))[-1]
        named = ref_name_ok(base) and base != 'HEAD' and _branch_seen(place, base) is not False
        hit, why = (('refs/heads/' + base, None), None) if named else (None, None)
    if why or not hit:
        return [], why
    commit = spelt(pos[1].text) if len(pos) > 1 else '<commit-ish>'
    return [(hit[0], False, 'checks out %s though another worktree may have it, so commits there move it' % hit[0],
             ('To check the commit out detached in the new worktree:',
              'git -C <at> worktree add --detach %s %s' % (path, commit)))], None


def _inline_on(gc, name):
    """(on, why unread or None) of the last inline -c `name` (a boolean setting) before git's
    subcommand: on unless its value is false (a bare key is true); None when the line sets none.
    A value the shell computes, or one --config-env names, is unread."""
    on = None
    if gc.inv is not None:
        for key, val, first, final in git_prefix_configs(gc):
            if key == name.lower():
                if _computed(final) or first.text.startswith('--config-env'):
                    return None, 'its -c %s holds something only the shell knows' % name
                on = val.strip().lower() not in FALSE_WORDS
    return on, None


def rebase_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git rebase `gc`: with
    --update-refs (the last of it and its no- form, else an inline -c rebase.updateRefs not
    false) it moves every branch that points into the commits it rebases."""
    shorts, longs, pos, unknown, seq, npre = _form_scan('rebase', gc.rest)
    last = _last_of(seq, ('update-refs', 'no-update-refs'))
    on = last == 'update-refs'
    if last is None:
        on, why = _inline_on(gc, 'rebase.updateRefs')
        if why:
            return [], why
    if not on:
        return [], None
    keep = []
    for w in gc.rest:
        body = w.text[2:].partition('=')[0] if w.text.startswith('--') else None
        hit = _long_hit(set(REBASE_FLAGS) | set(REBASE_VALUES), body) if body else None
        if not (hit and hit[0] == 'update-refs'):
            keep.append(spelt(w.text))
    return [('refs/heads/*', False, 'moves every branch that points into the commits it rebases',
             ('Rebase without moving other branches:', ('git -C <at> rebase --no-update-refs %s' % ' '.join(keep)).rstrip()))], None


def pull_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git pull `gc`: what its fetch
    writes (fetch_refs), and, when an inline -c rebase.updateRefs is on and the pull may rebase,
    every branch that points into the commits it rebases. It may rebase unless the line says it
    merges: the last of --rebase[=<v>], -r and --no-rebase, else an inline -c pull.rebase (the
    guard does not read the repository's config). git pull takes no --update-refs of its own."""
    refs, why = fetch_refs(gc, place)
    on, cwhy = _inline_on(gc, 'rebase.updateRefs')
    if not on or why:
        return refs, why or cwhy
    shorts, longs, pos, unknown, seq, npre = _form_scan('pull', gc.rest)
    last = _last_of(seq, ('rebase', 'no-rebase', '-r'))
    if last == 'no-rebase':
        return refs, None
    if last == 'rebase':
        v, computed = longs['rebase'][-1]
        if computed:
            return refs, 'its --rebase holds something only the shell knows'
        if v.strip().lower() in FALSE_WORDS:
            return refs, None
    elif last is None:
        rebases, cwhy = _inline_on(gc, 'pull.rebase')
        if cwhy:
            return refs, cwhy
        if rebases is False:
            return refs, None
    rest = ' '.join(spelt(w.text) for w in gc.rest)
    return refs + [('refs/heads/*', False, 'may rebase with rebase.updateRefs on, which moves every branch that '
                    'points into the commits it rebases',
                    ('Pull without moving other branches:',
                     ('git -C <at> -c rebase.updateRefs=false pull %s' % rest).rstrip()))], None


def remote_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git remote `gc`: remove (rm) and
    rename delete the remote's remote-tracking refs, set-head writes or deletes <remote>/HEAD, and
    add --mirror (=fetch) makes later fetches write every ref. Its other verbs (prune and update
    as fetch --prune, show, get-url and the like) write no ref here."""
    args, k = gc.rest, 0
    while k < len(args) and args[k].text.startswith('-'):
        t = args[k].text
        if t == '-h':
            return [], None
        if _computed(args[k]) or not (t == '-v' or (len(t) > 2 and '--verbose'.startswith(t))):
            return [], 'it gives an option the guard does not read (%s)' % t
        k += 1
    if k >= len(args):
        return [], None
    verb, rest = args[k], args[k + 1:]
    if _computed(verb):
        return [], 'its verb holds something only the shell knows (%s)' % verb.text
    names = [w for w in rest if not w.text.startswith('-')]
    listing = ('To list the remotes:', 'git -C <at> remote -v')
    if verb.text in ('remove', 'rm', 'rename', 'set-head'):
        why = _first_computed(rest)
        if why:
            return [], why
        if not names:
            return [], None
        n = names[0].text
        if verb.text == 'set-head':
            delete = any(w.text in ('-d', '--delete') for w in rest)
            return [('refs/remotes/%s/HEAD' % n, delete, None,
                     ('To see which branch the remote\'s HEAD names:', 'git -C <at> remote show %s' % spelt(n)))], None
        if verb.text == 'rename':
            new = names[1].text if len(names) > 1 else '<new-name>'
            return [('refs/remotes/%s/*' % n, True, 'renames every remote-tracking ref of %s to %s' % (n, new),
                     ('To add the same URL under the new name and leave %s as it is:' % n,
                      'git -C <at> remote add %s <url>' % spelt(new)))], None
        return [('refs/remotes/%s/*' % n, True, 'deletes every remote-tracking ref of %s' % n, listing)], None
    if verb.text != 'add':
        return [], None
    shorts, longs, pos, unknown, seq, npre = _form_scan('remote add', rest)
    if _last_of(seq, ('mirror', 'no-mirror')) != 'mirror':
        return [], None
    v, computed = longs['mirror'][-1]
    if computed:
        return [], 'its --mirror holds something only the shell knows'
    if v not in ('', 'fetch'):
        return [], None
    plain = ' '.join(spelt(w.text) for w in pos[:2]) or '<name> <url>'
    return [('refs/*', False, 'sets the remote up as a mirror, so a fetch from it writes over every ref',
             ('To add it with its own remote-tracking refs (refs/remotes/<name>/):', 'git -C <at> remote add %s' % plain))], None


HISTORY_TOOLS = {'fast-import': 'writes the refs its input names',
                 'filter-branch': 'rewrites the branches it names and keeps their old tips under refs/original/'}


def history_tool_refs(gc, place=None):
    """([(ref, delete, what, instead)], None) of git fast-import or filter-branch `gc`: every ref
    (refs/*), unless it only asks for help (-h)."""
    if [w.text for w in gc.rest] in (['-h'], ['--help']):
        return [], None
    return [('refs/*', False, HISTORY_TOOLS[gc.sub],
             ('Run it in a clone under the temp folder, where the guard does not judge refs:',
              'git clone <at> "<temp>/<name>"'))], None


REF_FORM_READERS = {'fetch': fetch_refs, 'pull': pull_refs, 'symbolic-ref': symbolic_ref_refs,
                    'replace': replace_refs, 'notes': notes_refs, 'branch': branch_refs, 'tag': tag_refs,
                    'checkout': checkout_refs, 'switch': checkout_refs, 'rebase': rebase_refs,
                    'remote': remote_refs, 'worktree': worktree_add_refs, 'fast-import': history_tool_refs,
                    'filter-branch': history_tool_refs}


def _judge_refs(event, ctx, cmd, gc, cwd, role, refs, unread):
    """git-shared-ref for a git form other than update-ref: `refs` [(ref, delete, what, instead)]
    the refs it writes or deletes (`what` a phrase for the refusal, None for "writes/deletes
    <ref>"; `instead` (sentence, command with <at> and maybe <temp>, the temp folder), None for show-ref), `unread` why the guard
    cannot tell them (None when it can). As git_shared_ref: only a held session
    (shared_ref_held), not in a scratch repository (_scratch_repo), each ref by _ref_ok; no form
    here writes through HEAD."""
    if (not refs and not unread) or not shared_ref_held(event, ctx):
        return None
    place, _top, at, where = _shared_scene(ctx, gc, cwd)
    if _scratch_repo(ctx, place):
        return None
    text = bare(cmd)
    unread = unread or where
    if unread:
        return refuse('git-shared-ref', role, '`%s`: the guard cannot tell which refs it writes: %s; %s. Name each '
                      'ref literally. %s To keep a commit in your scratch space:' % (
                          text, unread, SHARED_REFS, SHARED_REF_ALLOWED),
                      'git -C %s update-ref refs/tmp/<name> <new-oid>' % quote(at))
    linked = place.kind == repo.LINKED and not ctx.pm_data_error(gc.dir)
    own = _own_branch(ctx, place, gc.dir)
    for name, delete, what, instead in refs:
        if _ref_ok(name, delete, linked, own):
            continue
        what = what or '%s %s' % ('deletes' if delete else 'writes', name)
        how, command = instead or ('To show it:', 'git -C <at> show-ref %s' % name)
        temp = jail.temp_roots(ctx)
        return refuse('git-shared-ref', role, '`%s` %s: %s is not one it may %s; %s. %s %s' % (
            text, what, name, 'delete' if delete else 'write', SHARED_REFS, SHARED_REF_ALLOWED, how),
            command.replace('<at>', quote(at)).replace('<temp>', fwd(temp[0]) if temp else '<temp folder>'))
    return None


def git_shared_ref_forms(event, ctx, cmd, gc, cwd, role):
    """git-shared-ref for the forms REF_FORM_READERS reads (SHARED_REF_FORMS, branch and worktree),
    each given the Place git runs in (None when the text does not name it)."""
    if not shared_ref_held(event, ctx):
        return None
    place = ctx.place(gc.dir) if gc.dir and not gc.unknown and not gc.blind else None
    refs, unread = REF_FORM_READERS[gc.sub](gc, place)
    return _judge_refs(event, ctx, cmd, gc, cwd, role, refs, unread)


def _repo_common(path):
    """The git common dir of the repository git opens at `path` (a work tree, its .git, a git dir,
    or <path>.git, as git's enter_repo tries them), or None."""
    for p in (path, path + '.git'):
        common = gitdefaults.common_dir(p)
        if common:
            return common
        if os.path.isfile(os.path.join(p, 'HEAD')):
            named = gitdefaults._read(os.path.join(p, 'commondir'), 4096)
            return paths.norm(os.path.join(p, named.strip()), fold=False) if named and named.strip() else p
    return None


def _push_self(gc, place, t, computed, home=None):
    """True when git push's repository `word` is the repository git runs in: `.`, or a local path
    or file:// URL (resolved against where git runs and its work tree's top) that opens this
    repository's common dir; False for a URL, scp-like host:path, or a name that opens no such
    repository; None when the guard cannot tell (a computed word, or a path with no place to
    compare). A leading ~ is the home folder `home`, as git reads it. A named remote is taken as
    a remote, whatever its URL (a known limit)."""
    if computed:
        return None
    if t == '.':
        return True
    if t == '~' or t.startswith(('~/', '~\\')):
        if not home:
            return None
        t = os.path.join(home, t[2:]) if len(t) > 2 else home
    elif t.startswith('~'):
        return None                             # ~user: another user's home
    if t.lower().startswith('file://'):
        t = t[len('file://'):]
        t = t[1:] if re.match(r'^/[A-Za-z]:', t) else t
    elif '://' in t or (not re.match(r'^[A-Za-z]:', t) and re.match(r'^[^/\\]*:', t)):
        return False
    absolute = os.path.isabs(t) or bool(re.match(r'^[A-Za-z]:[\\/]', t))
    if place is None or not place.common:
        return None if absolute or '/' in t or '\\' in t or t.startswith('.') else False
    bases = [t] if absolute else [os.path.join(b, t) for b in (gc.dir, place.top) if b]
    mine = paths.norm(jail.real(place.common))
    for p in bases:
        common = _repo_common(paths.norm(p, fold=False))
        if common and paths.norm(jail.real(common)) == mine:
            return True
    return False


def _long_value(sub, args, name):
    """(value text, computed) of git `sub`'s long option `name` (or an abbreviation of it), the
    last one given, or None."""
    out, k = None, 0
    while k < len(args):
        t = args[k].text
        if t == '--':
            break
        if t.startswith('--'):
            body, eq, v = t[2:].partition('=')
            if _canon_long(sub, body) == [name]:
                if eq:
                    out = (v, _computed(args[k]))
                elif k + 1 < len(args):
                    out = (args[k + 1].text, _computed(args[k + 1]))
                    k += 1
        k += 1
    return out


def git_shared_push(event, ctx, cmd, gc, cwd, role):
    """git-shared-ref for a push into the repository itself (_push_self): it writes this
    repository's refs, so each refspec destination is judged as update-ref's ref. A destination
    must be a full refs/ name (push matches a short one against the refs there); `:dst` and -d
    delete; a refspec with no `:` is a no-op. --all, --mirror, --branches, --tags, --prune, no
    refspec, a computed refspec, or a repository the guard cannot tell with a refspec that writes,
    are unread."""
    if not shared_ref_held(event, ctx):
        return None
    short, longs, pos = _opts('push', gc.rest)
    remote = (pos[0].text, _computed(pos[0])) if pos else _long_value('push', gc.rest, 'repo')
    if remote is None:
        return None
    specs = pos[1:]
    delete = 'd' in short or 'delete' in longs
    every = sorted({'all', 'mirror', 'branches', 'tags', 'prune'} & longs)
    place = ctx.place(gc.dir) if gc.dir and not gc.unknown and not gc.blind else None
    self_ = _push_self(gc, place, *remote, home=getattr(ctx, 'home', None))
    if self_ is False:
        return None
    writes = delete or every or any(':' in w.text or _computed(w) for w in specs)
    if self_ is None:
        why = 'the guard cannot tell whether %s is this repository' % remote[0]
        return _judge_refs(event, ctx, cmd, gc, cwd, role, [], why) if writes else None
    refs, unread = [], None
    if every:
        unread = 'with --%s it pushes refs it does not name into this repository' % every[0]
    elif not specs:
        unread = 'it names no refspec, so git takes them from its config'
    for w in specs:
        if unread:
            break
        if _computed(w):
            unread = 'a refspec holds something only the shell knows (%s)' % w.text
            break
        spec = w.text[1:] if w.text.startswith('+') else w.text
        src, colon, dst = spec.partition(':')
        if spec.startswith('^') or (not colon and not delete):
            continue                            # an exclusion, or a ref pushed onto itself
        dst = dst if colon else src
        if not dst or not dst.startswith('refs/'):
            unread = '%r names no full ref (refs/...); push into this repository names it in full' % spec
            break
        refs.append((dst, delete or not src, None, None))
    return _judge_refs(event, ctx, cmd, gc, cwd, role, refs, unread)


SAFE_WORD_RE = re.compile(r'^[\w@%+=:,./-]+$')


def spelt(text):
    """`text` as one shell word: bare when it needs no quoting, else single-quoted (double-quoted,
    with bash's escapes, when it holds a single quote)."""
    if SAFE_WORD_RE.match(text):
        return text
    if "'" not in text:
        return "'%s'" % text
    return '"%s"' % re.sub(r'(["\\$`])', r'\\\1', text)


def git_rest_text(cmd, gc):
    """The text of `gc.rest` for a hint: the words an alias the line defines gave (gc.synth, no
    span in the line) spelt as git split them, then the line's own words as written."""
    words = [spelt(w.text) for w in gc.rest[:gc.synth]]
    own = gc.rest[gc.synth:]
    if own and own[0].start is not None and own[-1].end is not None:
        words.append(cmd.source[own[0].start:own[-1].end])
    elif own:
        words.extend(spelt(w.text) for w in own)
    return ' '.join(words)


def git_unplaced(ctx, cmd, gc, cwd, agent=False):
    """(sentence, command) for git whose place `gc.blind` hides (GIT_DIR and the like, --git-dir,
    --work-tree, core.worktree, --config-env, a computed -c): drop it and run the same git
    command in the session's own worktree (git_elsewhere), or where it runs when it is in none."""
    mine = own_worktree(ctx, cwd)
    if mine:
        note, instead = git_elsewhere(ctx, cmd, gc, mine, agent)
        return 'Drop %s and run it in your worktree:%s' % (gc.blind, ' ' + note if note else ''), instead
    rest = git_rest_text(cmd, gc)
    return 'Drop %s and run it in your worktree:' % gc.blind, ('git %s %s' % (gc.sub, rest)).rstrip()


def git_branch_delete(ctx, gc, role):
    short, longs, pos = _opts('branch', gc.rest, wide=True)
    force = 'D' in short or (('delete' in longs or 'd' in short) and ('force' in longs or 'f' in short))
    if not force:
        return None
    names = ' '.join(w.text for w in pos) or '<branch>'
    from . import backlogrole
    store = backlogrole.store_kind(ctx, gc.dir) if gc.dir and not gc.unknown else None
    held = [w.text for w in pos if not w.dyn and '*' not in w.text and
            backlogrole.is_store_ref(w.text, store, short=True)]
    if held:
        # -d of it is a raw write of the store's ref (backlog-raw-write): no delete form passes
        return refuse('git-branch-force-delete', role, '`git branch %s` deletes a branch whether or not it is '
                      'merged, and %s is the backlog store\'s branch, which only tools/backlog.py writes; no '
                      'session deletes it. To see it:' % (' '.join(w.text for w in gc.rest), held[0]),
                      'git log --oneline -1 %s' % held[0])
    return refuse('git-branch-force-delete', role, '`git branch %s` deletes a branch whether or not it is merged. '
                  'Delete it with -d, which refuses an unmerged branch; unmerged work goes to the PM, never '
                  'deleted:' % ' '.join(w.text for w in gc.rest), 'git branch -d %s' % names)


def git_worktree_remove(ctx, gc, role, text):
    args = gc.rest
    if not args or args[0].text != 'remove':
        return None
    if ctx.is_pm_data(gc.dir):
        ctx, why = kit_judged(ctx, gc.dir)
        if ctx is None:
            return refuse('git-worktree-remove', role, '`%s` from the PM data worktree, where the kit\'s rules for the '
                          'shared repository cannot be read: %s. To see the worktrees:' % (text, why),
                          'git -C %s worktree list' % quote(gc.dir))
    pos = _pos(args[1:])
    if not pos or pos[0].dyn or gc.dir is None:
        target = None
    else:
        target = sh.resolve_path(pos[0].text, gc.dir, home=getattr(ctx, 'home', None))
    main_top =(ctx.main_top(gc.dir) if gc.dir else None) or gc.dir
    if target is None:
        # a worktree the guard cannot place (warn by default); git still refuses a dirty one without --force
        return refuse(UNPLACED, role, '`%s`: the guard cannot tell which worktree it removes. Name it '
                      'by a literal path:' % text,
                      'git -C %s worktree remove "<worktree path>"' % (quote(main_top) if main_top else '"<main checkout>"'))
    if _in_temp(ctx, target):
        return None                             # a scratch worktree under a temp root
    cfg = ctx.config(gc.dir)
    main = cfg.main_branch if cfg is not None else 'main'
    clean, why = ctx.git.is_clean(target)
    if clean is not True:
        return refuse('git-worktree-remove', role, '`%s` removes a worktree that is not clean (%s). Commit or drop '
                      'what status lists, then remove it with git -C %s worktree remove %s. To see it:' %
                      (text, why or 'uncommitted or untracked files', quote(main_top), quote(target)),
                      'git -C %s status' % quote(target))
    head, why = ctx.git.resolve('HEAD', target)
    merged, why2 = ctx.git.is_ancestor(head, main, gc.dir) if head else (None, why)
    if merged is not True:
        word, mark_why = stale_mark_word(ctx, target, head) if merged is False and head else (False, None)
        if word:
            return None                         # a stale mark that holds: the PM's word
        how = ('Merge it first; unmerged work goes to the PM, who decides (python %s -C %s %s --abandon "<why>" '
               'only on the PM\'s word).' % (kit_tool(ctx, 'worktree_remove'),
                                             quote(main_top) if main_top else '"<main checkout>"',
                                             os.path.basename(target)))
        if mark_why:
            how += ' A stale mark does not count: %s.' % mark_why
        return refuse('git-worktree-remove', role, '`%s` removes a worktree whose commit is not merged into %s (%s). '
                      '%s Its unmerged commits:' % (text, main, why2 or 'not an ancestor', how),
                      'git -C %s log --oneline %s..HEAD' % (quote(target), main))
    return None


def stale_mark_word(ctx, target, head):
    """`(True, None)` when the clean worktree at `target`, HEAD `head`, carries a stale mark that
    holds (kitlib.worktree.marks: a mark recorded by `worktree_remove.py --mark-stale`, read from
    `<git common dir>/kit-stale-marks.json`): the PM's word to remove it unmerged. It holds on the
    nightly sweep's void checks (sweep.sweep_skip): the mark names this path and this tip, and
    nothing in the tree, its git dir (but index, index.lock and the folder's own time), its
    branch's reflog or the tip's commit date is newer than the mark. The sweep's other checks are
    not made here: the idle bar, `git worktree lock`, exemption, worktree-folder placement, a branch
    checked out elsewhere, holders, open claims, machine lock and landing hold. marks.mark makes
    them all when it marks (with classify.live_reason) but the DELETE-access holder probe
    (holders.blockers): of holders it checks only processes' current folders, through the L
    rule. So what is open is that probe and a change after the mark: the mark is the decision that
    the tree is idle and later activity voids it; a later `git worktree lock` writes `locked` into
    the walked git dir and voids it while it is held (no `--force` is read); a mark naming another
    path is void. Known limits: a file held open in the
    tree other than as a current folder (neither the mark nor this runs holders.blockers), and a
    later exemption, second checkout, holder (no process probe runs here), claim, machine-lock
    run or landing that writes nothing there, are not seen (docs/guard.md, the
    git-worktree-remove row). Else `(False, why)`: why is None when the tree's branch has no
    mark (detached included), else why no mark counts. Fails closed: a marks file, branch, git
    common dir or last activity that cannot be read is no word.
    The tree walk (sweep.last_activity) runs only here, on an unmerged remove that found a mark."""
    from kitlib.worktree import marks, sweep  # the walk and its git reads: only on this path
    place = ctx.place(target)
    if not place.common:
        return False, 'git cannot place its repository (%s)' % (place.detail or 'no git common dir')
    branch, why = ctx.git.current_branch(target)
    if branch is None:
        return False, 'its branch cannot be read (%s)' % why
    if not branch:
        return False, None                    # a detached HEAD: a mark names a branch
    got, why = marks.load(os.path.join(place.common, marks.FILE_NAME))
    if got is None:
        return False, 'the stale marks cannot be read (%s)' % why
    mark = marks.find(got, branch)
    if mark is None:
        return False, None
    void = marks.void_reason(mark, target, head)
    if void is None:
        seen, why = sweep.last_activity(ctx.main_top(target) or target, target, branch)
        if seen is None:
            return False, 'its stale mark cannot be checked: its last activity cannot be read (%s)' % why
        void = marks.void_reason(mark, target, head, seen)
    if void:
        return False, 'its stale mark is void: %s' % void
    return True, None


def pmdata_possible(ctx, path):
    """pmdata_recorded, except that a directory the text cannot tell (None) counts only when the
    machine config names pm_data (or cannot be read): with none named, an unknown directory is
    no ground to deny a command that is not a push."""
    return pmdata_recorded(ctx, path) if path else ctx.pm_data_named()


def call_subexpr_pmdata(event, ctx, cmd, inv, cwd, role):
    """PowerShell's call operator on a ( ) subexpression (`& (Get-Command git) push`,
    commands.call_subexpr), in every session but the owner's own: the program is what the ( )
    computes, so the rules that need a literal program (heavy, the write jail, the git rules)
    cannot read it. In a repository with a PM data record it is denied as git-push-pmdata (it may
    be git push --no-verify); elsewhere an agent session (agent_session) is refused as unplaced
    (it may be git writing a checkout the jail cannot place; warn by default). Only the owner and
    a session the jail does not hold (`plain`) read the line as any other."""
    if not cm.call_subexpr(inv) or is_owner_session(event, ctx):
        return None
    instead = ('& <program> %s' % ' '.join(w.text for w in inv.args)).rstrip()
    where = cmd.cwd
    if pmdata_possible(ctx, where):
        return refuse('git-push-pmdata', role, '`& %s` runs the program a PowerShell subexpression computes, and this '
                      'repository has %s (or the guard cannot tell): it may be git push --no-verify, which skips the '
                      'pre-push hook that keeps PM data off public remotes. Name the program literally:'
                      % (inv.path, pmdata.FILE_NAME), instead)
    if not agent_session(event, ctx):
        return None
    return refuse(UNPLACED, role, '`& %s` runs the program a PowerShell subexpression computes, which the guard cannot '
                  'name: it may be git writing a checkout the write jail cannot place, or a heavy program run '
                  'without the machine lock. Name the program literally:' % inv.path, instead)


def hookless_sub(sub):
    """True for a git subcommand that talks to a remote without running the pre-push hook:
    send-pack, http-push, and a remote helper run by hand (remote-https, remote-ext, remote-fd,
    any remote-<transport>)."""
    return sub in ('send-pack', 'http-push') or (sub.startswith('remote-') and len(sub) > len('remote-'))


def git_send_pack_pmdata(event, ctx, inv, role):
    """`git send-pack`, `git http-push` or a `git remote-<helper>`, or their dashed programs
    (git-send-pack, git-http-push, git-remote-https, ...), in a repository with a PM data record
    (or where the text cannot place it), in every session but the owner's own: each talks to a
    remote without running the pre-push hook (rule git-push-pmdata). In a repository without a
    record, data_copy judges it."""
    if inv is None or inv.dyn:
        return None
    if inv.name.startswith('git-') and hookless_sub(inv.name[len('git-'):]):
        sub, where, args = inv.name[len('git-'):], cm.run_dir(inv), [w.text for w in inv.args]
    else:
        gc = git_call_judged(inv)
        if gc is None or not hookless_sub(gc.sub):
            return None
        sub, where, args = gc.sub, (None if gc.unknown else gc.dir), gc.argv
    return hookless_refusal(event, ctx, sub, where, args, role)


def hookless_refusal(event, ctx, sub, where, args, role):
    """git_send_pack_pmdata's judgement of `git <sub> <args>` run in `where`."""
    if is_owner_session(event, ctx):
        return None
    text = ('git %s %s' % (sub, ' '.join(args))).rstrip()
    if not pmdata_recorded(ctx, where):
        return data_copy(event, ctx, where, role, '`%s`' % text)
    return refuse('git-push-pmdata', role, '`%s` talks to a remote without running the pre-push hook, which '
                  'keeps PM data off public remotes in this repository (it has %s, or the guard cannot tell). '
                  'Push with git push, which runs the hook:' % (text, pmdata.FILE_NAME),
                  'git -C %s push <remote> <branch>' % (quote(where) if where else '"<checkout>"'))


# ---------------------------------------------------------------- remotes repointed

REMOTE_REWRITE_VERBS = ('set-url', 'add', 'rename', 'remove', 'rm')


def remote_rewrite_key(name):
    """True for a config key or section that can point a remote somewhere else: remote.* (url,
    pushurl, ...), url.* (insteadOf, pushInsteadOf), include.* and includeIf.* (they read
    another file), case ignored as git ignores it in section and key names."""
    n = name.lower()
    return n in ('remote', 'url', 'include', 'includeif') or n.startswith(('remote.', 'url.', 'include.', 'includeif.'))


def config_rewrite(gc):
    """(what, files, scope_all) when a `git config` call writes a key or section
    remote_rewrite_key names, opens a config in an editor, or has a positional word computed at
    run time (git config $K, PowerShell's git config @a: it may be any write), else None.
    Options are read as git reads them (config_args: --glo, --fil <path>, -f<path>, --ed).
    `files`: the Words of its -f/--file paths; `scope_all`: --global or --system."""
    names, sure, pos, files = config_args(gc)
    scope_all = bool({'global', 'system'} & names)
    if 'edit' in names or (pos and not pos[0].dyn and pos[0].text == 'edit'):
        return 'opens a config file in an editor, where any remote can be changed', files, scope_all
    computed = next((w for w in pos if w.dyn), None)
    if computed is not None:
        return 'has a word computed at run time (%s), which may write any key' % computed.text, files, scope_all
    if sure & CONFIG_READS or not pos:
        return None
    verb = pos[0].text
    if verb in ('get', 'list'):
        return None
    if verb in ('set', 'unset'):
        keys = pos[1:2]
    elif verb in ('rename-section', 'remove-section'):
        keys = pos[1:3]
    elif names & CONFIG_SECTIONS:
        keys = pos[:2]
    elif names & CONFIG_WRITES or len(pos) > 1:
        keys = pos[:1]
    else:
        return None
    for w in keys:
        if remote_rewrite_key(w.text):
            return 'writes %s' % w.text, files, scope_all
    return None


def _config_file_recorded(ctx, path, where):
    """True when a config file git reads belongs to a repository with a PM data record (its
    folder, or the git common dir two folders up from a worktree's config, holds the record), or
    is a user or system config (scope_all, judged by machine_pm_data); a path that cannot be
    resolved counts (fail closed)."""
    p = sh.resolve_path(path, where, home=getattr(ctx, 'home', None)) if path else None
    if not p:
        return True
    parts = [x.lower() for x in fwd(posixpath.normpath(fwd(p))).split('/') if x]
    if parts and (parts[-1] in ('.gitconfig', 'gitconfig') or parts[-3:-1] == ['.config', 'git']):
        return machine_pm_data(ctx)
    d = os.path.dirname(p)
    for _ in range(3):
        if os.path.exists(os.path.join(d, pmdata.FILE_NAME)):
            return True
        d = os.path.dirname(d)
    return False


def machine_pm_data(ctx):
    """True when the machine config names pm_data, or cannot be read (Ctx.pm_data_named)."""
    return ctx.pm_data_named()


def _remote_verb(gc):
    """The git remote verb that repoints a remote (REMOTE_REWRITE_VERBS), a computed one too (a
    PowerShell splat @a among them), or None."""
    ps = gc.inv is not None and gc.inv.cmd.shell == sh.PS
    for w in gc.rest:
        if w.dyn or (ps and w.start is not None and cm.PS_SPLAT_RE.match(w.text) and not cm.quoted(gc.inv.cmd, w)):
            return w.text
        if w.text.startswith('-'):
            continue
        return w.text if w.text in REMOTE_REWRITE_VERBS else None
    return None


def git_remote_rewrite(event, ctx, gc, role):
    """git-remote-rewrite: `git remote set-url|add|rename|remove`, a `git config` write to
    remote.*, url.*, include.* or includeIf.* (any spelling: --add, --unset, set, unset, the
    section verbs) and `git config --edit`, in a repository with a PM data record (or one git
    cannot place), in every session but the owner's own. A directory the text cannot tell counts
    when _unknown_dir_counts says so (the machine config names pm_data, or the command's own
    directory has a record). A --global or --system write, or one to
    a user or system config file, counts when the machine config names pm_data; a --file git
    does not read is left alone. The pre-push leak stage tells the private remote by its
    configured URLs, so a remote pointed elsewhere would make a public one look private
    (docs/pm-data-branch.md "Pre-push guard")."""
    if gc.sub not in ('remote', 'config') or is_owner_session(event, ctx):
        return None
    where = None if gc.unknown else gc.dir
    def here_recorded():
        return pmdata_recorded(ctx, where) if where is not None else _unknown_dir_counts(ctx, gc)
    if gc.sub == 'remote':
        verb = _remote_verb(gc)
        if verb is None:
            return None
        what = 'repoints a remote (git remote %s)' % verb
        recorded = here_recorded()
    else:
        call = config_rewrite(gc)
        if call is None:
            return None
        what, files, scope_all = call
        if any(not f.dyn and not repo_config_file(f.text) for f in files):
            return None                         # a file git does not read
        if files:
            recorded = any(f.dyn or _config_file_recorded(ctx, f.text, where) for f in files)
        else:
            recorded = here_recorded() or (scope_all and machine_pm_data(ctx))
    if not recorded:
        return None
    return refuse('git-remote-rewrite', role, '`git %s %s` %s, and this repository has %s (or the guard cannot tell): '
                  'the pre-push hook tells the private remote by its configured URLs, so a remote pointed elsewhere '
                  'could carry PM data to a public one. Ask the owner (through the PM) when a remote must change. '
                  'To see them:' % (gc.sub, ' '.join(w.text for w in gc.rest), what, pmdata.FILE_NAME),
                  'git -C %s remote -v' % (quote(where) if where else '"<checkout>"'))


# ---------------------------------------------------------------- Windows paths in heredocs
