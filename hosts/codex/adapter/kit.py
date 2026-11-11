"""The kit as Codex's runtime half reads it: the folder it booted from and its roles. This replaces
the Codex branch's profile.py and release_binding.py (boot/kitboot.py binds and checks the kit
before any entry imports this) and roles.py/roles.json (content/roles is the one roster).

  discover()          Kit(kit_root, plugin_root): the booted kit's folder and this host's shell
                      folder
  load_kit(kit)       nothing: the kit was bound by the entry's kitboot.boot(); kept so the runtime
                      modules read as they did
  catalog()           {role: {'mode', 'effort', 'sandbox'}} from content/roles (every role, the
                      effort its tier gives when its file names none)
  instructions(role)  the role's developer instructions on Codex (the rendered agent file's)
  native_role(name)   the kit role a native agent name (kit-<role>) stands for; ValueError for any
                      other name
  current_role(environ)   PM_KIT_ROLE's role ('analyst' when unset); ValueError for one no role file names
  child_environment(environ, additions, preserve_lock)
                      kitlib.host.sessionmark.child_environment for a Codex child
  trailer(model)      the Co-Authored-By line for `model` on Codex (an explicit model id)
  host_prefixes(own)  every other registered host's environment prefixes (hosts.json env_prefixes),
                      and Codex's own too when `own`: what a native check scrubs from a fixture
"""
from dataclasses import dataclass
import os
from pathlib import Path

from kitlib.host import sessionmark

HOST = 'codex'
ROLE_ENV = sessionmark.ENV
DEFAULT_ROLE = 'analyst'


@dataclass(frozen=True)
class Kit:
    kit_root: Path
    plugin_root: Path


def discover():
    from kitlib import kitroot
    from hosts.codex.adapter import content
    root = Path(kitroot.root())
    return Kit(root, root.joinpath(*content.SHELL_REL.split('/')))


def load_kit(kit=None):
    return None


def _content():
    from kitlib import content, kitroot
    return content.load(kitroot.root())


def catalog():
    from hosts.codex.adapter import content
    return {r.name: {'mode': r.mode, 'effort': r.effort or content.TIER_EFFORT[r.tier],
                     'sandbox': content.sandbox_mode(r)} for r in _content().roles}


def instructions(role):
    from hosts.codex.adapter import content
    found = _content()
    return content.substitute_body(found.role(role), found.role_names()).replace("\r\n", "\n")


def native_role(name):
    from hosts.codex.adapter import content
    for role in catalog():
        if name == content.agent_name(role):
            return role
    raise ValueError('native agent %r is not a kit role' % (name,))


def current_role(environ=None):
    env = os.environ if environ is None else environ
    value = sessionmark.mark(env) or DEFAULT_ROLE
    if value not in catalog():
        raise ValueError('unknown kit role: %s' % value)
    return value


def child_environment(environ, additions=None, preserve_lock=False):
    return sessionmark.child_environment(environ, HOST, additions, preserve_lock)


def host_prefixes(own=False):
    from kitlib.host import registry
    return tuple(p for h in registry.load().values() if own or h.name != HOST for p in h.profile.env_prefixes)


def trailer(model):
    from kitlib import attribution
    return attribution.coauthor(model, provider=HOST)
