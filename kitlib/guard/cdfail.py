"""The folders a cd that may fail leaves the shell in (docs/guard-core.md "Parsing"; cdtrack.track).

After a failed cd, bash runs what follows `;`, `||` or a newline in the folder it was in, and so
does PowerShell after `;`. guarded() names the commands after a cd that run only when it
succeeded; Held keeps the folders before the cd until those have passed, then they are the line's
other folders (Command.cwd_alt). Reads Command attributes only; imports nothing from shell.
"""
BASH, PS = 'bash', 'ps'                         # shell.BASH, shell.PS


def guarded(cmds, i):
    """range of the indices of the commands after the cd `cmds[i]` that run only if it succeeded:
    (a) the run of && (and the pipes in it) from the cd at its group level, every command of a group
    that run enters included; (b) everything after `cd x || exit` to the end of the cd's subshell
    (bash's innermost ( ) group) or of the line (`|| return` outside a function only prints an
    error); (c) PowerShell only, `cd x; if ($?) { ... }`: the commands of the { } group opened right
    after the condition, which is `$?` exactly (an else or elseif block runs when the cd failed)."""
    return range(i + 1, max(_and_run(cmds, i), _or_exit(cmds, i), _if_ok(cmds, i)) + 1)


def group_end(cmds, j, n, gid):
    """The last index from j on of the commands inside the group `gid` at depth n."""
    while j + 1 < len(cmds) and len(cmds[j + 1].groups) > n and cmds[j + 1].groups[n] == gid:
        j += 1
    return j


def _and_run(cmds, i):
    level = cmds[i].groups
    n, end, op = len(level), i, cmds[i].next_op
    while end + 1 < len(cmds) and op in ('&&', '|') and cmds[end + 1].groups[:n] == level:
        c = cmds[end + 1]
        end = group_end(cmds, end + 1, n, c.groups[n]) if len(c.groups) > n else end + 1
        op = cmds[end].next_op if len(c.groups) == n else (cmds[end + 1].prev_op if end + 1 < len(cmds) else None)
    return end


def _or_exit(cmds, i):
    c, e = cmds[i], cmds[i + 1] if i + 1 < len(cmds) else None
    if c.next_op != '||' or e is None or e.groups != c.groups or not e.words or e.words[0].dyn \
            or e.words[0].text.lower() != 'exit':
        return i
    keep = max([k + 1 for k, g in enumerate(c.groups) if g[1] == '(' and c.shell == BASH] or [0])
    end = i + 1                                 # exit leaves the line, or a bash ( ) subshell
    while end + 1 < len(cmds) and cmds[end + 1].groups[:keep] == c.groups[:keep]:
        end += 1
    return end


def _if_ok(cmds, i):
    c, n = cmds[i], len(cmds[i].groups)
    if c.shell != PS or c.next_op not in (';', '\n') or i + 3 >= len(cmds):
        return i
    kw, cond, first = cmds[i + 1], cmds[i + 2], cmds[i + 3]
    if kw.groups != c.groups or [w.text.lower() for w in kw.words] != ['if'] or kw.next_op != '(' or \
            [w.text for w in cond.words] != ['$?'] or len(cond.groups) != n + 1 or cond.next_op != ')' or \
            len(first.groups) != n + 1 or first.groups[n][1] != '{' or first.prev_op != '{':
        return i
    return group_end(cmds, i + 3, n, first.groups[n])


class Held(object):
    """The other folders the shell may be in (`alts`), and per cd that may fail the folders before
    it, held until the commands guarded() names, or the rest of the body or group that alone may keep
    a cd from running, have passed (at())."""

    def __init__(self, alts=()):
        self.alts, self.wait, self.seq = list(alts), [], 0     # wait: (sequence, last guarded index, folders)

    def hold(self, folders, last):
        """Hold `folders` until the command after index `last` (the last guarded())."""
        self.seq += 1
        self.wait.append((self.seq, last, list(folders)))

    def at(self, k):
        """The other folders at command k: a hold whose guarded commands are behind joins them for good."""
        for w in [w for w in self.wait if w[1] < k]:
            self.wait.remove(w)
            self.alts.extend(w[2])
        return self.alts

    def save(self):
        return list(self.alts), self.seq

    def restore(self, saved):
        """Back to save()'s state: a bash ( ) group closed, and no cd inside it moved the shell outside."""
        self.alts, self.wait = list(saved[0]), [w for w in self.wait if w[0] <= saved[1]]
