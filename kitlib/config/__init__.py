"""kitlib.config (ARCHITECTURE.md): a repository's `.kit.json` read and checked whole (load,
schema, types), the owner-only rules (owner), kit policy read from it - heavy programs, roles, the
kinds table, the repo hook module and its gate checks (policy) - and the machine's own settings, the ledger path
(user). docs/kit-json.md lists every key.
"""
from .types import ConfigError
from .load import Config, config_path, load, load_at, overlay_staging, retired_text, retired_warnings
from .owner import Rules as OwnerRules, merge as merge_owner_rules
from .policy import (HEAVY_DEFAULTS, ROLE_DEFAULTS, commit_settings, gate_checks, heavy_match, heavy_programs,
                     kinds_table, load_gate_checks, load_hook_module, permissions, role_name, roles)
from .user import ledger_dir, ledger_files, ledger_path, user_config

__all__ = ['ConfigError', 'Config', 'config_path', 'load', 'load_at', 'overlay_staging', 'retired_text',
           'retired_warnings', 'work_base', 'staging_on',
           'OwnerRules',
           'merge_owner_rules', 'HEAVY_DEFAULTS', 'ROLE_DEFAULTS', 'heavy_match', 'heavy_programs',
           'kinds_table', 'gate_checks', 'load_gate_checks', 'load_hook_module', 'permissions', 'role_name', 'roles', 'ledger_path',
           'ledger_dir', 'ledger_files', 'user_config']


def work_base(cfg):
    """The branch work merges onto and starts from (Config.work_base): the staging branch when
    `"staging".branch` is set, else the main branch. The one read of it outside kitlib.config;
    a test's stand-in config without the property gives its main_branch (docs/gate-landing.md
    "Staging branch")."""
    base = getattr(cfg, 'work_base', None)
    return base if isinstance(base, str) and base else cfg.main_branch


def staging_on(cfg):
    """True when work_base(cfg) is a staging branch, not the main branch."""
    return work_base(cfg) != cfg.main_branch
