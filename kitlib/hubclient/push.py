"""kitlib.hubclient.push: the words of a hub push notification, shared with whoever delivers one.

The hub's push worker (kitlib/hubsite/push.py) decides what to send and when; a host's delivery
object (deliver(recipient, text) -> Sent) sends it. Both read these.

  Sent(state, name, session, ambiguous)   what one delivery did; state is WRITTEN, NOT_WRITTEN,
                                          UNCERTAIN or NO_SESSION
  PM_KEY                                  the recipient key that stands for the PM
  field(value)                            one header field of a push line
"""
import collections
import re

PM_KEY = 'pm'                   # the PM's recipient key: a to-do's `from` when an agent gives none
WRITTEN, NOT_WRITTEN, UNCERTAIN, NO_SESSION = 'written', 'not-written', 'uncertain', 'no-live-session'
_FIELD = re.compile(r'[^A-Za-z0-9._:-]')

Sent = collections.namedtuple('Sent', 'state name session ambiguous')


def field(value):
    """A header field: only [A-Za-z0-9._:-], at most 64 characters, '-' for nothing."""
    text = _FIELD.sub('_', value if isinstance(value, str) else str(value or ''))[:64]
    return text or '-'
