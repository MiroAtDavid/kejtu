"""Ship a reviewed starter through the ordinary installed-skill owner."""
from importlib.resources import files

from .packages import prepare


NAME = 'topic-research'


def install_starter(store):
    """First desktop initialization or explicit setup; never replace a saved choice."""
    # An existing name may be disabled, updated or damaged. All require an
    # explicit library decision, never a reset to the shipped revision.
    if any(item['name'] == NAME for item in store.skills.status()['items']):
        return False
    source = files('kejtu_next').joinpath('starter_skills', NAME, 'SKILL.md')
    package = prepare({'SKILL.md': source.read_bytes()}, {'kind': 'builtin'}, NAME)
    store.skills.install(package, store.skills.epoch())
    store.skills.enable(NAME, package['revision'], True, store.skills.epoch())
    return True
