"""The scripts a Linux job runs around its command (kitlib.platform.linux.contain, whose docstring
says what each one does): STUB, the sh stub that joins the job's cgroup and waits on its go pipe;
WSTUB, W2's stub; WATCH, W2's watchdog; NSTART, W1's starter. Text only: nothing here runs."""



# $1 job cgroup name, $2 the job cgroup to join ('' : the scope systemd put it in), $3 ready fd,
# $4 go fd, $5 watch fd, $6 armed (1/0), $7 variables to unset, $8 1: systemd puts it in $2/main
# (a carried job: it waits for `in` on the go pipe first), then the command.
STUB = r'''n=$1 j=$2 r=$3 g=$4 k=$5 arm=$6 un=$7 put=$8; shift 8
own() {
  cg=; while IFS= read -r l; do case $l in 0::*) cg=${l#0::};; esac; done </proc/self/cgroup
}
if [ "$put" = 1 ]; then
  IFS= read -r m <&$g
  [ "$m" = in ] || exit 125
fi
own
if [ -z "$j" ]; then
  case $cg in */"$n") j=$cg;; *) echo "x not in its scope: $cg" >&$r; exit 125;; esac
  mkdir "/sys/fs/cgroup$j/main" "/sys/fs/cgroup$j/w" ||
    { echo "x cannot make its leaves" >&$r; exit 125; }
fi
echo 0 >"/sys/fs/cgroup$j/main/cgroup.procs" || { echo "x cannot join $j/main" >&$r; exit 125; }
own
[ "$cg" = "$j/main" ] || { echo "x not in its leaf: $cg" >&$r; exit 125; }
( ( trap '' INT HUP TERM QUIT PIPE
    echo 0 >"/sys/fs/cgroup$j/w/cgroup.procs" || exit 125
    eval "exec $r>&- $g>&- 0<&$k $k<&-"
    exec >/dev/null 2>&1
    while IFS= read -r c; do
      case $c in arm) arm=1;; disarm) arm=0;; release) exit 0;; esac
    done
    [ "$arm" = 1 ] && echo 1 >"/sys/fs/cgroup$j/cgroup.kill"
  ) & echo "w $!" >&$r )
eval "exec $k<&-"
echo "s $$ $j" >&$r
IFS= read -r go <&$g
[ "$go" = go ] || exit 125
eval "exec $r>&- $g>&-"
for v in $un; do unset "$v"; done
exec "$@"
'''

# W2's stub: $1 ready fd, $2 go fd, then the command.
WSTUB = r'''r=$1 g=$2; shift 2
echo "s $$" >&$r
IFS= read -r go <&$g
[ "$go" = go ] || exit 125
eval "exec $r>&- $g>&-"
exec "$@"
'''

# W2's watchdog (python -I -S -c): argv token, watch fd, armed (1/0), passes.
WATCH = r'''import os, signal, sys, time
tok, fd, arm, passes = sys.argv[1], int(sys.argv[2]), sys.argv[3] == '1', int(sys.argv[4])
for s in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM, signal.SIGQUIT):
    signal.signal(s, signal.SIG_IGN)
spared = set()
with os.fdopen(fd, 'rb', 0) as f:
    for line in f:
        c = line.strip()
        if c == b'arm':
            arm = True
        elif c == b'disarm':
            arm = False
        elif c == b'release':
            sys.exit(0)
        elif c.startswith(b'spare '):
            spared.update(int(x) for x in c.split()[1:])
if not arm:
    sys.exit(0)
def marked(pid):
    try:
        with open('/proc/%d/environ' % pid, 'rb') as e:
            env = e.read().split(b'\0')
    except OSError:
        return False
    return any(v.startswith(b'PMKIT_JOB=') and tok.encode() in v[10:].split() for v in env)
me = os.getpid()
for _ in range(passes):
    fds = []
    for n in os.listdir('/proc'):
        if not n.isdigit() or int(n) == me or int(n) in spared or not marked(int(n)):
            continue
        try:
            pfd = os.pidfd_open(int(n))
        except OSError:
            continue
        if marked(int(n)):
            fds.append(pfd)
        else:
            os.close(pfd)
    if not fds:
        break
    for sig in (signal.SIGSTOP, signal.SIGKILL):
        for pfd in fds:
            try:
                signal.pidfd_send_signal(pfd, sig)
            except OSError:
                pass
    for pfd in fds:
        os.close(pfd)
    time.sleep(0.05)
'''

# W1's starter (python -I -S -c; the module docstring): its ready, go and watch pipes at 3, 4 and
# 5 (FDS), argv armed (1/0), the stub's shell, the stub (WSTUB), then the command. It says
# `r <pid 1's pid>`, the reaper `x <why>` when it fails, the stub `s <its pid in the namespace>`.
# The uid and gid are read before the unshare: after it they read as the overflow id until the
# maps are written, and only the writer's own id may be mapped (from inside the namespace, root
# included: so root is never W1, W1_ROOT).
NSTART = r'''import os, select, signal, sys, time
R, G, K = 3, 4, 5
arm, sh, stub, cmd = sys.argv[1] == '1', sys.argv[2], sys.argv[3], sys.argv[4:]
def say(text):
    try:
        os.write(R, (text + '\n').encode('utf-8', 'replace'))
    except OSError:
        pass
def live(n):
    try:
        with open('/proc/%s/stat' % n, 'rb') as f:
            data = f.read()
        return data[data.rindex(b')') + 2:][:1] not in (b'Z', b'X')
    except (OSError, ValueError):
        return False
def kill_all(spared):
    for _ in range(8):
        fds = []
        for n in os.listdir('/proc'):
            if n.isdigit() and int(n) != 1 and int(n) not in spared:
                try:
                    fd = os.pidfd_open(int(n))
                except OSError:
                    continue
                if live(n):
                    fds.append(fd)
                else:
                    os.close(fd)
        if not fds:
            return
        for sig in (signal.SIGSTOP, signal.SIGKILL):
            for fd in fds:
                try:
                    signal.pidfd_send_signal(fd, sig)
                except OSError:
                    pass
        for fd in fds:
            os.close(fd)
        time.sleep(0.05)
def reaper(st, up):
    global arm
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        os.unshare(os.CLONE_NEWNS)
        for src, dst, kind, flags in ((b'none', b'/', None, 16384 | 262144),
                                      (b'proc', b'/proc', b'proc', 2 | 4 | 8)):
            if libc.mount(src, dst, kind, flags, None) != 0:
                e = ctypes.get_errno()
                raise OSError(e, 'mount %s: %s' % (dst.decode(), os.strerror(e)))
    except (ImportError, AttributeError, OSError) as e:
        say('x no fresh /proc in the job: %s' % e)
        os._exit(125)
    os.environ['PMKIT_W1_NS'] = str(os.stat('/proc/self/ns/pid').st_ino)
    wr, ww = os.pipe()
    os.set_blocking(wr, False)
    os.set_blocking(ww, False)
    signal.signal(signal.SIGCHLD, lambda *a: None)
    signal.set_wakeup_fd(ww)
    child = os.fork()
    if child == 0:
        try:
            os.close(K)
            signal.set_wakeup_fd(-1)
            for s in TERMINAL + (signal.SIGCHLD, signal.SIGPIPE, signal.SIGXFSZ):
                signal.signal(s, signal.SIG_DFL)
            os.execv(sh, [sh, '-c', stub, 'pmkit-job', str(R), str(G)] + cmd)
        except BaseException as e:
            say('x the stub could not start: %s' % e)
        os._exit(127)
    os.close(R)
    os.close(G)
    null = os.open('/dev/null', os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    os.close(null)
    poll = select.poll()
    for fd in (K, up, wr):
        poll.register(fd, select.POLLIN)
    status, told, released, closed, spared, buf = None, False, False, False, set(), b''
    while True:
        kids = True
        while True:
            try:
                pid, s = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                kids = False
                break
            if pid == 0:
                break
            if pid == child:
                status = s
        if status is not None and not told:
            told = True
            try:
                os.write(st, b'%d\n' % status)
            except OSError:
                pass
        if not kids and (released or closed):
            os._exit(0)
        try:
            ready = poll.poll()
        except InterruptedError:
            continue
        for fd, _ in ready:
            if fd == wr:
                try:
                    os.read(wr, 512)
                except OSError:
                    pass
                continue
            try:
                data = os.read(fd, 4096)
            except OSError:
                data = b''
            if fd == up:
                if not data:
                    poll.unregister(up)
                    if not told:
                        os._exit(0)
                continue
            if not data:
                poll.unregister(K)
                closed = True
                if arm and not released:
                    if not spared:
                        os._exit(0)
                    kill_all(spared)
                continue
            buf += data
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                words = line.split()
                if words == [b'arm']:
                    arm = True
                elif words == [b'disarm']:
                    arm = False
                elif words == [b'release']:
                    released = True
                elif words[:1] == [b'spare']:
                    spared.update(int(x) for x in words[1:] if x.isdigit())
TERMINAL = (signal.SIGINT, signal.SIGQUIT, signal.SIGHUP)
for s in TERMINAL:
    signal.signal(s, signal.SIG_IGN)
uid, gid = os.getuid(), os.getgid()
try:
    os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWPID)
    for name, text in (('setgroups', 'deny'), ('uid_map', '%d %d 1' % (uid, uid)),
                       ('gid_map', '%d %d 1' % (gid, gid))):
        with open('/proc/self/' + name, 'w') as f:
            f.write(text)
except (AttributeError, OSError) as e:
    say('x no user and PID namespace: %s' % e)
    sys.exit(125)
st_r, st_w = os.pipe()
up_r, up_w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(st_r)
    os.close(up_w)
    try:
        reaper(st_w, up_r)
    finally:
        os._exit(1)
os.close(st_w)
os.close(up_r)
say('r %d' % pid)
for fd in (R, G, K):
    os.close(fd)
null = os.open('/dev/null', os.O_RDWR)
for fd in (0, 1, 2):
    os.dup2(null, fd)
data = b''
while b'\n' not in data:
    chunk = os.read(st_r, 64)
    if not chunk:
        break
    data += chunk
try:
    status = int(data.split(b'\n')[0])
except ValueError:
    status = None
if status is not None and os.WIFEXITED(status):
    os._exit(os.WEXITSTATUS(status))
sig = os.WTERMSIG(status) if status is not None and os.WIFSIGNALED(status) else signal.SIGKILL
try:
    signal.signal(sig, signal.SIG_DFL)
except (OSError, ValueError):
    pass
os.kill(os.getpid(), sig)
os._exit(128 + sig)
'''
