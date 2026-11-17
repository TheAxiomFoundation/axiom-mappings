"""Former name of ``axiom_mappings.export.oracles_microcosm``, kept for one release."""

import warnings

from .oracles_microcosm import *  # noqa: F401,F403
from .oracles_microcosm import main, microcosm_table, oracles_rule, render  # noqa: F401

warnings.warn(  # compat: populace alias, remove after 0.2.0
    "axiom_mappings.export.oracles_populace is now axiom_mappings.export.oracles_microcosm; "  # compat: populace alias, remove after 0.2.0
    "the old name goes in the next release",
    DeprecationWarning,
    stacklevel=2,
)
