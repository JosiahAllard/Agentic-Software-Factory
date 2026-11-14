"""kitlib.config (ARCHITECTURE.md): a repository's `.kit.json` read and checked whole (load,
schema, types), the owner-only rules (owner), kit policy read from it - heavy programs, roles, the
kinds table, the repo hook module and its gate checks (policy) - and the machine's own settings, the ledger path
(user). docs/kit-json.md lists every key.
"""
from .types import ConfigError
from .load import (Config, config_path, load, load_at, load_at_fetched_staging, overlay_fetched_staging,
                   overlay_staging, retired_text, retired_warnings, staging_on, work_base,
                   work_base_ref)
from .owner import Rules as OwnerRules, merge as merge_owner_rules
from .policy import (HEAVY_DEFAULTS, ROLE_DEFAULTS, commit_settings, gate_checks, heavy_match, heavy_programs,
                     kinds_table, load_gate_checks, load_hook_module, permissions, role_name, roles)
from .user import ledger_dir, ledger_files, ledger_path, user_config

__all__ = ['ConfigError', 'Config', 'config_path', 'load', 'load_at', 'load_at_fetched_staging', 'overlay_staging',
           'overlay_fetched_staging', 'retired_text', 'retired_warnings', 'work_base', 'staging_on',
           'work_base_ref', 'OwnerRules',
           'merge_owner_rules', 'HEAVY_DEFAULTS', 'ROLE_DEFAULTS', 'heavy_match', 'heavy_programs',
           'kinds_table', 'gate_checks', 'load_gate_checks', 'load_hook_module', 'permissions', 'role_name', 'roles', 'ledger_path',
           'ledger_dir', 'ledger_files', 'user_config']
