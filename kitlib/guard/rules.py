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
                   to-pm add|send, inbox read, approvals approve|deny|apply, todos add --owner; a word
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
import os
import posixpath

from kitlib import config as kconfig
from kitlib.git import repo
from kitlib.host import Decision

from . import commands as cm
from . import commands_git as cg
from . import commands_nested as cn
from . import context
from . import modes
from . import shell as sh
from .rules_base import (AGENT_MARKS, ALLOW, ASK, Asked, CITE_MAX, DENY, ELIDED, LEDGER_GLOB_CHARS, PSCmdlet,
                         PS_ALIASES, PS_CMDLETS, PS_COMMON_ALIASES, PS_COMMON_SWITCHES, PS_COMMON_VALUES,
                         PS_NAMED_DYN_RE, SAFE_WORD_RE, SHELLS, TAG, UNPLACED, WIN_OPT_RE, _COMMAND, _FILTERS, _PATHS,
                         advice_kit, agent_session, bare, cited, fwd, here, is_owner_session, kept_word, kit_tool,
                         literal_word, lock_tool, own_worktree, owner_host, ps_args, ps_bound, ps_native_unsure,
                         ps_param_word, ps_params, quote, refuse, reworded, same_dir, shell_word, spelt, split_commas,
                         subagent, tag, tag_role, tail_word, unowned_chat)
from .rules_owner import (COMPUTED_TAIL_RE, HUB_NAMES_LIMIT, HUB_OWNER_WHY, HUB_SCRIPT, HUB_SECRETS_WHY, HubPlaces,
                          HubShell, LOCK_SCRIPT, LOCK_VALUE_OPTS, NODE_EVAL, NODE_VALUE_OPTS, OPENERS, OWNER_LOCK_WHY,
                          OWNER_SETUP_WHY, PY_FLAG_RE, PY_RUN_RE, SETUP_APPLY, SETUP_SCRIPTS, UI_NAME_RE, _tool_run,
                          computed_script, glob_folder, host_mcp_prefix, host_mcp_separator, hub_ask_alternative,
                          hub_config, hub_folder_read, hub_line, hub_mcp, hub_owner_run, hub_path_texts,
                          hub_read_alternative, hub_real, hub_run_args, hub_run_verb, hub_secret_edit,
                          hub_secret_folders, hub_secret_names, hub_secret_read, hub_secret_shell, hub_secrets_refusal,
                          hub_session, hub_texts, inline_code, kit_run, lock_mcp, lock_owner_write, mcp_owner_tool,
                          mcp_prefix, owner_tool, owner_tool_run, script_run, tool_run, tool_words, unowned_name)
from .rules_run import (BASH_LEAD_SKIP, BOUND_RE, BUSY_MINUTES, BUSY_WHY, DUP_RE, GATE_NOTHING, GLOB_ANY, GLOB_HINT,
                        KILL_VALUE_LONG, KILL_VALUE_SHORT, PS_PROC_ALIASES, PS_PROC_SWITCHES, PS_PROC_VALUES,
                        PS_SPLIT_C_RE, PS_STOP, ROOT_VAR_RE, SIGNAL_OPT_RE, SLEEPERS, THREAD_SLEEP_RE, WILD,
                        _ROOT_NAMES, _ROOT_VAR, _backgrounded, _bash_loop_do, _bash_loops, _busy_loop, _extends, _first,
                        _in_outer, _kill_patterns, _lead, _match_bracket, _nested_under, _ps_loop_span, _ps_loops,
                        _segments, _sleeps, _under_timeout, bare_wrapper, busy_wait, find_paths, gate_runs_nothing,
                        heavy, is_root, kill_by_name, killed_name, kit_script, kit_tool_dir, kit_wrapper,
                        lock_alternative, lock_run_help, plain_run, ps_proc_params, root_search, run_from, search_paths,
                        split_wild, wrapped_gate)
from .rules_gitcore import (ALIAS_BLANKS, ALIAS_DEPTH, CONFIG_LONGS, CONFIG_LONG_VALUES, CONFIG_READS, CONFIG_READ_OPTS,
                            CONFIG_SECTIONS, CONFIG_SHORTS, CONFIG_STRICT_READ_OPTS, CONFIG_WRITES, GIT_BUILTINS,
                            GIT_LONGS, GIT_LONG_VALUES, GIT_SHORT_VALUES, HOOKS_ENV, HOOKS_KEY, HOOKS_WHY, IDENTITY_ENV,
                            IDENTITY_KEYS, _canon_long, _config_files, _config_long, _config_scope_all, _is_kit_hooks,
                            _names_email, _one_letter, _opts, _owner_identity_use, _pos, _under_temp, _without,
                            clean_dry_run, clone_configs, computed_word, config_args, env_assignments, git_call_judged,
                            git_config_call, git_hooks_path, git_prefix_configs, hooks_config_write, inline_alias,
                            kit_githooks, owner_identity, repo_config_file, restore_index_only, split_alias,
                            stash_reads)
from .rules_push import (DataBranch, Judged, LAND_HOW, _branch_of, _guarded_mains, _no_verify_word,
                         _push_names_branches, _unknown_dir_counts, call_subexpr_pmdata, data_copy, git_push,
                         git_push_pm_data, git_push_pmdata, git_send_pack_pmdata, home_config, hookless_refusal,
                         hookless_sub, kit_judged, landing_command, pmdata_possible, pmdata_recorded)
from .rules_push import pmdata      # kitlib.config.pmdata, read here and as rules.pmdata (fallback)
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
from .rules_net import (IO_FILE_RE, NET_ABS_RE, NET_DEVICE_RE, NET_PROCESS_DIR, NET_QUOTES, NET_TYPE_RE, NET_UNPLACED,
                        NEW_OBJECT_PARAMS, NetWrite, PIPE_UNPLACED, PIPE_WORD, PS_EXPORT_NO_PATH, _net_args, _net_ctor,
                        _net_list, _net_literal, _net_lost, _net_new_object, _net_word, _net_written, net_mentioned,
                        net_placed, net_process_dir, net_relative, net_retarget, net_stand_in, net_string_end,
                        net_writes, pipe_stand_in, pipe_stand_in_word, ps_export_targets)
from .rules_tools import (AWK_ASSIGN_RE, AWK_FLAG_LONGS, AWK_LONGS, AWK_OPTIONAL_LONGS, AWK_OPTIONAL_SHORTS,
                          AWK_VALUE_SHORTS, EDITOR_VALUE_SHORTS, EVALS, PY_LAUNCHER_VER_RE, PY_LONG_VALUE, PY_PS_WHY,
                          PY_SHORT_VALUE, PY_SP_WHY, PY_UNREAD_WHY, TAR_FLAGS, TAR_LONG_FLAGS, TAR_VALUES, Under,
                          _TarDirs, _awk_inplace_lib, _awk_inplace_text, _awk_long, _cluster_values, _tar_old,
                          _tar_stdout, _tar_word, _under, awk_writes, copier_writes, curl_writes, editor_writes,
                          eval_writes, patch_writes, perl_writes, py_program, python_unread, python_writes,
                          rsync_writes, ruby_writes, sed_writes, tar_writes, unix_opts, unzip_writes, wget_writes)
from .rules_write import (ATTACHED_LEAD_RE, BASH_DEVICE_RE, BASH_TMP_RE, BASH_WRITERS, CMD_WRITERS, COPIERS,
                          COPY_INTO_LONGS, COPY_INTO_SHORTS, COPY_LONGS, COPY_VALUE_LONGS, COPY_VALUE_SHORTS, CopyArgs,
                          DOTDOT_RE, DUPS_RE, EDITORS, GLOB_CHARS, NULL_TARGETS, OUTER, PS_BRACED_VAR_RE,
                          PS_CMDLET_ARG_RE, PS_DOTNET_RE, PS_DRIVE_REMAP_RE, PS_ENV_READ_WORD_RE, PS_PLAIN_ARG_RE,
                          PS_PLAIN_GIT, PS_PROVIDER_ALIASES, PS_PROVIDER_CMDLETS, PS_PROVIDER_ITEM_RE, PS_PWD_WORD_RE,
                          PS_READ_CMDLETS, PS_VALUE_PARAMS, _PS_BARE, _PS_CALL_ARG, _PS_CHAIN, _PS_DQ, _PS_DQ_PLAIN,
                          _PS_END, _PS_HERE_CLOSE, _PS_SQ, _PS_VALUE_ATOM, _PS_VALUE_END, _PS_VAR, _ps_group_end,
                          _ps_value_end, _write_from, attached_lead, copy_into, copy_writes, execdir_swaps,
                          find_exec_writes, find_runs, into_folder, ps_drive_jail, ps_line_plain, ps_provider_item,
                          ps_provider_word, ps_pwd_path, ps_unread, py_unread, retarget, unread_write, word_rewrite,
                          write_place, write_targets)
from .rules_gitread import (APPLY_CHECK_OPTS, APPLY_READ_ONLY, APPLY_REPORTS, APPLY_STRIP_RE, ARCHIVE_FLAGS,
                            ARCHIVE_LONGS, ARCHIVE_SAFE_KEY_RE, ARCHIVE_VALUES, DYN_PS_VAR_RE, DYN_SAFE_BARE,
                            DYN_VAR_RE, END_OF_OPTIONS, GIT_DYN_ANY_READS, GIT_DYN_PINNED_READS, GIT_DYN_VALUE_OPTS,
                            GIT_LISTING_OPTS, GIT_LIST_FLAGS, GIT_READS, GIT_READ_VERBS, GIT_REF_WRITE_FLAGS,
                            GIT_STRICT_READ_OPTS, HISTORY_SUBS, NO_VALUE_NUM_RE, NO_VALUE_OPTS, SYMREF_READ_OPTS,
                            apply_checks, archive_filter_set, archive_long, archive_opened, archive_outputs,
                            archive_words, cmd_native_unsure, config_reads, dyn_head, git_reads, git_targets,
                            native_unsure, ps_splat, respelt, strict_dyn_read, strict_read_opts, writes_main,
                            writes_output_file)
from .rules_gitcommit import (COMMIT_HOLDS, COMMIT_KEPT_LONG, COMMIT_LONG_FLAGS, COMMIT_LONG_VALUES,
                              COMMIT_SHORT_ATTACHED, COMMIT_SHORT_FLAGS, COMMIT_SHORT_VALUES, COMMIT_STEPS,
                              COMMIT_STOPS, COMMIT_UNDOES, COMMIT_VERBS, INTERPRETER_COMMITS, INTERPRETER_GIT_RE,
                              INTERPRETER_LAUNCH_RE, INTERPRETER_VERB_RE, STASH_NAMED, STASH_READS, TYPED_HASH_RE,
                              commit_alternative, commit_scan, git_plain_commit, git_stash_shared, interpreter_git,
                              stash_alternative, stash_shared, typed_hash)
from .rules_gitjail import (LANDER, LANDER_ARGS, LANDER_NOT_ALONE, LANDER_NO_CONFIG, LANDER_NO_ROLE, LANDER_VERBS,
                            LANDER_WRAPPED, git_elsewhere, git_in_worktree, git_jail, git_jail_unknown, git_rest_text,
                            git_retarget, git_unplaced, lander_alone, lander_check, lander_forms, lander_refusal)
from .rules_gitref import (BRANCH_FLAGS, BRANCH_VALUES, CHECKOUT_FLAGS, CHECKOUT_VALUES, FALSE_WORDS, FETCH_FLAGS,
                           FETCH_VALUES, HISTORY_TOOLS, NOTES_READ_VERBS, NOT_STRAY, PER_WORKTREE_REFS,
                           PRUNE_TAGS_KEY_RE, PSEUDOREF_RE, PULL_FLAGS, PULL_VALUES, REBASE_FLAGS, REBASE_VALUES,
                           REF_CLEANUP_MARKS, REF_FORM_OPTS, REF_FORM_READERS, REF_NAME_RE, REMOTE_FETCH_KEY_RE,
                           REPARSE_POINT, REPLACE_LIST_OPTS, SCRATCH_WALK_MAX, SHARED_REFS, SHARED_REF_ALLOWED,
                           SHARED_REF_FORMS, SWITCH_FLAGS, SWITCH_VALUES, TAG_FLAGS, TAG_VALUES, UPDATE_REF_SHORTS,
                           WORKTREE_ADD_FLAGS, _branch_seen, _checked_out, _deref_unsure, _first_computed, _form_scan,
                           _form_words, _head_ref, _in_temp, _inline_on, _judge_refs, _last_of, _long_hit, _long_value,
                           _own_branch, _push_self, _read_ref, _ref_ok, _repo_common, _scratch_repo, _shared_scene,
                           _stray_delete, _tracking_ref, _update_ref_unread, branch_refs, checkout_refs,
                           cleanup_session, fetch_dst_ref, fetch_refs, git_branch_delete, git_shared_push,
                           git_shared_ref, git_shared_ref_forms, git_worktree_remove, history_tool_refs, main_names,
                           notes_refs, per_worktree_ref, pull_refs, rebase_refs, ref_delete_hint, ref_name_ok,
                           remote_refs, replace_refs, scratch_ref, shared_ref_held, stale_mark_word, stray_ref,
                           symbolic_ref_refs, tag_refs, worktree_add_refs)


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
    parsed = cn.read(text, shell, cwd, home=ctx.home)
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
    # git-* program) gives none: the git rules judge its writes, from the workdir (commands_git.git_call).
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
        parsed = cn.read(text, cmd.shell, where, home=getattr(ctx, 'home', None))
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
    gc = cg.git_call(inv)
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
