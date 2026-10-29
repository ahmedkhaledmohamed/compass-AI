"""Versioned prompt registry for Compass engine components.

Each prompt version is a dict with system and user prompt templates.
The registry maps version names to prompt dicts for each engine component.
"""

from __future__ import annotations

from compass.prompts.analyze_data_v1 import PROMPT as ANALYZE_DATA_V1_PROMPT
from compass.prompts.analyze_data_v1 import SYSTEM as ANALYZE_DATA_V1_SYSTEM
from compass.prompts.challenge_v1 import PROMPT as CHALLENGE_V1_PROMPT
from compass.prompts.challenge_v1 import SYSTEM as CHALLENGE_V1_SYSTEM
from compass.prompts.dashboard_v1 import PROMPT as DASHBOARD_V1_PROMPT
from compass.prompts.dashboard_v1 import SYSTEM as DASHBOARD_V1_SYSTEM
from compass.prompts.discover_v1 import PROMPT as DISCOVER_V1_PROMPT
from compass.prompts.discover_v1 import SYSTEM as DISCOVER_V1_SYSTEM
from compass.prompts.experiment_v1 import PROMPT as EXPERIMENT_V1_PROMPT
from compass.prompts.experiment_v1 import SYSTEM as EXPERIMENT_V1_SYSTEM
from compass.prompts.plan_week_v1 import PROMPT as PLAN_WEEK_V1_PROMPT
from compass.prompts.plan_week_v1 import SYSTEM as PLAN_WEEK_V1_SYSTEM
from compass.prompts.present_v1 import SYSTEM as PRESENT_V1_SYSTEM
from compass.prompts.present_v1 import USER as PRESENT_V1_PROMPT
from compass.prompts.prototype_v1 import SYSTEM as PROTOTYPE_V1_SYSTEM
from compass.prompts.prototype_v1 import USER as PROTOTYPE_V1_PROMPT
from compass.prompts.reconcile_v1 import PROMPT as RECONCILE_V1_PROMPT
from compass.prompts.reconcile_v1 import SYSTEM as RECONCILE_V1_SYSTEM
from compass.prompts.specify_v1 import PROMPT as SPECIFY_V1_PROMPT
from compass.prompts.specify_v1 import SYSTEM as SPECIFY_V1_SYSTEM
from compass.prompts.write_brief_v1 import PROMPT as WRITE_BRIEF_V1_PROMPT
from compass.prompts.write_brief_v1 import SYSTEM as WRITE_BRIEF_V1_SYSTEM
from compass.prompts.write_update_v1 import PROMPT as WRITE_UPDATE_V1_PROMPT
from compass.prompts.write_update_v1 import SYSTEM as WRITE_UPDATE_V1_SYSTEM

# Default prompt version
DEFAULT_VERSION = "v1"

# Registry: component → version → {system, prompt}
REGISTRY: dict[str, dict[str, dict[str, str]]] = {
    "reconcile": {
        "v1": {"system": RECONCILE_V1_SYSTEM, "prompt": RECONCILE_V1_PROMPT},
    },
    "discover": {
        "v1": {"system": DISCOVER_V1_SYSTEM, "prompt": DISCOVER_V1_PROMPT},
    },
    "specify": {
        "v1": {"system": SPECIFY_V1_SYSTEM, "prompt": SPECIFY_V1_PROMPT},
    },
    "write_brief": {
        "v1": {"system": WRITE_BRIEF_V1_SYSTEM, "prompt": WRITE_BRIEF_V1_PROMPT},
    },
    "write_update": {
        "v1": {"system": WRITE_UPDATE_V1_SYSTEM, "prompt": WRITE_UPDATE_V1_PROMPT},
    },
    "challenge": {
        "v1": {"system": CHALLENGE_V1_SYSTEM, "prompt": CHALLENGE_V1_PROMPT},
    },
    "experiment": {
        "v1": {"system": EXPERIMENT_V1_SYSTEM, "prompt": EXPERIMENT_V1_PROMPT},
    },
    "plan_week": {
        "v1": {"system": PLAN_WEEK_V1_SYSTEM, "prompt": PLAN_WEEK_V1_PROMPT},
    },
    "analyze_data": {
        "v1": {"system": ANALYZE_DATA_V1_SYSTEM, "prompt": ANALYZE_DATA_V1_PROMPT},
    },
    "dashboard": {
        "v1": {"system": DASHBOARD_V1_SYSTEM, "prompt": DASHBOARD_V1_PROMPT},
    },
    "present": {
        "v1": {"system": PRESENT_V1_SYSTEM, "prompt": PRESENT_V1_PROMPT},
    },
    "prototype": {
        "v1": {"system": PROTOTYPE_V1_SYSTEM, "prompt": PROTOTYPE_V1_PROMPT},
    },
}


def get_prompts(component: str, version: str = DEFAULT_VERSION) -> dict[str, str]:
    """Get system and prompt templates for a component at a given version.

    Args:
        component: "reconcile", "discover", or "specify"
        version: prompt version (e.g., "v1")

    Returns:
        Dict with "system" and "prompt" keys.

    Raises:
        KeyError: If component or version not found.
    """
    if component not in REGISTRY:
        raise KeyError(f"Unknown component '{component}'. Available: {list(REGISTRY.keys())}")
    versions = REGISTRY[component]
    if version not in versions:
        raise KeyError(f"Unknown version '{version}' for {component}. Available: {list(versions.keys())}")
    return versions[version]


def list_versions(component: str) -> list[str]:
    """List available prompt versions for a component."""
    if component not in REGISTRY:
        return []
    return list(REGISTRY[component].keys())
