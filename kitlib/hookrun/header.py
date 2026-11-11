"""The kit header: what every session and subagent is told at its start (docs/content.md "Kit
header"). Names no host; each host's adapter renders it into its own hook output (on Claude Code,
SessionStart and SubagentStart additionalContext).

  text(kit)   the header for the kit folder `kit` (absolute; the root kitboot bound), then the
              kit's rules for every agent (RULES), with the Dev Drive warning (NO_DEV_DRIVE) when
              the machine config sets no dev_drive

Skills and role files name the kit's folder `<kit>` (docs/content.md "Skills"), and a role or skill
body is not substituted on every host, so the header says once, at the start, what `<kit>` is. A
repository holds no copies of the kit's tools (docs/install.md "Ported repo tools"):
every call is `python <kit>/tools/<x>.py -C <checkout>`.
"""

# Said at every start while the guard's outer bound on agent writes (docs/guard-jail-roles.md "Write jail",
# "Outer bound") is off for want of a usable dev_drive: unset, relative, not an existing folder, or
# a machine config that does not read.
NO_DEV_DRIVE = ('Warning: the machine config sets no usable dev_drive (the Dev Drive\'s root folder: unset, '
                'relative, not an existing folder, or the config cannot be read), so the guard does not hold '
                'agent writes to the Dev Drive. The owner sets dev_drive (docs/kit-json.md "Machine config keys").')

# The kit's rules for every session and agent, on every host: their one home (docs/content.md "Kit
# header"). Role files and skills cite them, never restate them.
RULES = (
    'Kit rules for every session and agent:',
    '- Never send the owner\'s personal details (email address, name, account names) off this machine; '
    'web fetches use a neutral User-Agent.',
    '- Search inside a repository, worktree or known folder, with a timeout; never from a drive root. '
    'Docs: `python <kit>/tools/doc_index.py -C <worktree> --find <topic>`, then the one section it names '
    '(`--section "<heading>"`), never docs/INDEX.md whole; code: `python <kit>/tools/codemap.py`. Use your '
    'search and read tools, not shell grep, cat or sed. Scripts and scratch files go in your scratch '
    'folder, never a repository.',
    '- One write per shell call: never chain a commit, merge, reset, backlog write or landing command with '
    'other commands; a kit tool call is bare (no `cd` prefix, pipe or wrapper).',
    '- End only a process you started, by its pid while its start time matches; never by name or command '
    'line.',
    '- A classifier refusal, a permission denial or a tool\'s `REFUSED:` on substance is final: send your '
    'caller the exact command, folder and message and go on with other work; never retry, reword or route '
    'it round. A kit-guard refusal: fix the form its `Instead:` line gives, then retry (skill backlog-ops, '
    '"Refusals").',
    '- A subagent reports in its reply text, about 40 lines, never a report file.',
)

_READ = object()


def dev_drive_note(user=_READ, kit=None):
    """NO_DEV_DRIVE when the guard's outer bound has no Dev Drive to hold writes to, else None. It
    asks what the guard asks (kitlib.guard.jail.dev_root: the guard's tolerant read of the machine
    config, Ctx.user; an absolute dev_drive that is an existing folder), so the warning and the
    bound always agree. `user` is a machine config as Ctx.user returns it (default: this
    machine's, read here). A process under the kit's home fence (kitlib.config.user.fenced: the
    kit's tests and their children) reads no machine config here and says nothing."""
    from kitlib.config import user as kuser
    from kitlib.guard import jail
    if user is not _READ:
        return None if jail.dev_root_of(user) else NO_DEV_DRIVE
    if kuser.fenced():
        return None
    try:
        from kitlib.guard import context
        dev = jail.dev_root(context.Ctx(kit=kit))
    except Exception:  # noqa: BLE001 - a config that does not read leaves the bound off: say so
        dev = None
    return None if dev else NO_DEV_DRIVE


def text(kit, user=_READ):
    k = kit.replace('\\', '/').rstrip('/')
    head = ('PM kit header. The kit folder is %s. In the kit\'s skills and role files, `<kit>` means '
            'that folder: `python <kit>/tools/machine_lock.py status` is '
            '`python "%s/tools/machine_lock.py" status`. The kit\'s tools run from that folder, never '
            'from a copy in the repository: a tool that acts on a checkout is named with -C, as in '
            '`python <kit>/tools/preflight.py -C <worktree>`, and runs the same from any directory. '
            'Every other name in angle brackets (`<worktree>`, `<repo>`, `<slug>`, `<branch>` and the '
            'like) is a placeholder you fill in from your brief, never a folder to look for.'
            % (k, k))
    note = dev_drive_note(user, kit)
    return head + '\n' + '\n'.join(RULES) + ('\n' + note if note else '')
