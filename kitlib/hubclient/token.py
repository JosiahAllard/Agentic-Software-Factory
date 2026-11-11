"""kitlib.hubclient.token: where the hub's token files live, and reading one.

  folder(home=None)           <machine config dir>/hub, where the token files live: the PM
                              repository's; ConfigError with no PM-repo pointer (NO_POINTER)
  read_token(folder, role)    a role's token as a client sends it, or None

OWNER and AGENT are the two roles (ROLES) a token file serves; ANY is a route either token may
call. Making the files and judging a request are the server's (kitlib/hubsite/auth.py).
"""
import os

from kitlib.config import user
from kitlib.config.types import ConfigError

OWNER = 'owner'
AGENT = 'agent'
ANY = 'any'
ROLES = (OWNER, AGENT)
FOLDER_NAME = 'hub'
# folder()'s refusal with no PM-repo pointer: the token files live in the PM repository's machine
# config folder, and only the pointer names that repository
NO_POINTER = ('no PM repo pointer is set, so the hub has no token folder: set it first, from an '
              'elevated shell: python tools/machine_setup.py --pm-repo <PM repository> (%s); '
              'nothing written' % user.POINTER_DOC)
OWNER_FILE = 'owner.token'
AGENT_FILE = 'agent.token'
TOKEN_FILES = {OWNER: OWNER_FILE, AGENT: AGENT_FILE}
MIN_TOKEN = 43               # characters, a generated token's length; a shorter file grants nothing
MAX_TOKEN = 512


def folder(home=None):
    """The credential folder: FOLDER_NAME in the PM repository's machine config folder
    (kitlib.config.user.locate OK; `home`, default the user's, is its leftover check only).
    ConfigError when the PM-repo pointer is not set (NO_POINTER) or the machine config cannot be
    read (locate's error): never a folder in the home folder, so hub.py setup writes no token
    file there."""
    loc = user.locate(home)
    if loc.state == user.NONE:
        raise ConfigError(NO_POINTER)
    if loc.state != user.OK:
        raise ConfigError(loc.error)
    return os.path.join(os.path.dirname(loc.new), FOLDER_NAME)


def read_token(folder, role):
    """The token in `role`'s file under `folder`, stripped, or None when the file is absent,
    unreadable, not ASCII, or holds fewer than MIN_TOKEN or more than MAX_TOKEN characters."""
    if role not in TOKEN_FILES:
        return None
    return read_secret(os.path.join(folder, TOKEN_FILES[role]))


def read_secret(path):
    """The secret in the file at `path` (read_token's rules), or None."""
    try:
        with open(path, 'rb') as f:
            raw = f.read(MAX_TOKEN + 64)
    except OSError:
        return None
    try:
        token = raw.decode('ascii').strip()
    except UnicodeDecodeError:
        return None
    if not MIN_TOKEN <= len(token) <= MAX_TOKEN or any(c.isspace() for c in token):
        return None
    return token
