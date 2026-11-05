"""The ledger writer's refusals; tools/ledger.py maps them to exit codes."""


class Usage(Exception):
    """The request is malformed (an empty field, an unknown repo or provenance): exit 2."""


class Refused(Exception):
    """The request is well formed but cannot be carried out (an invalid ledger, an entry that
    would break it, git refused): exit 1."""


class Failed(Exception):
    """A read or write that could not be done (an unreadable file, a missing digest): the tool
    prints it as `ledger.py: <why>` and exits 1."""
