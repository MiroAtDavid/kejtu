"""Version-matched local help through existing exact skill delivery."""
from importlib.resources import files

from .skills import encode, sha


NAME = 'kejtu-app-help'
DESCRIPTION = ('Explain Kejtu capabilities, onboarding, settings, memory, documents, '
               'tools and limitations; troubleshoot unavailable app actions.')
PATHS = ('SKILL.md', '01-start.md', '02-capabilities.md', '03-memory.md',
         '04-web-files-skills.md', '05-projects-documents.md',
         '06-tasks-background.md', '07-settings-troubleshooting.md', '08-privacy-limits.md')


def packages():
    root = files('kejtu_next').joinpath('help')
    bodies = {path: root.joinpath(path).read_bytes() for path in PATHS}
    manifest = dict(name=NAME, metadata=dict(name=NAME, description=DESCRIPTION),
                    origin=dict(kind='builtin'),
                    files={path: dict(sha256=sha(body), bytes=len(body)) for path, body in bodies.items()})
    return {NAME: dict(manifest=manifest, revision=sha(encode(manifest).encode()), files=bodies)}

