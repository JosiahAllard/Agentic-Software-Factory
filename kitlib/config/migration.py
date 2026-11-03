"""The machine config folder's move from its retired name to kitlib.config.user.DIR_NAME
(docs/kit-json.md "Moving the machine config folder").

OLD_DIR_NAME is the one place the retired name is spelled. kitlib.config.user.locate reports a
config file left in the old folder, and nothing here moves, copies or deletes a file. The modules
that use OLD_DIR_NAME are listed in tests/scrub/test_old_config_name.py.
"""

# The machine config folder's retired name.
OLD_DIR_NAME = '.claude-pm-kit'
