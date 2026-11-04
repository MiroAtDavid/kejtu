"""Prepare explicit fresh launch inputs; native startup owns archive creation."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from kejtu_next.bootstrap import native_profile, native_standing_profile
from kejtu_next.core.prompt import NATIVE_BEHAVIOR
from kejtu_next.core.records import Failure, Profile
from kejtu_next.core.providers import provider


def prepare(root: Path, profile_path: Path | None = None, standing_path: Path | None = None,
            *, setup_runtimes=False) -> Path:
    if profile_path is None:
        # Read the same provider defaults as SavedModels; saved profiles remain
        # authoritative after initialization. No separate window setting exists.
        spec = provider('deepseek')
        profile = Profile(spec.default_model, spec.endpoint, spec.default_context,
            spec.default_generation, thinking_level=spec.thinking.default, provider=spec.id)
    else:
        profile = native_profile(profile_path.resolve())
    if profile.model.startswith('YOUR_INSTALLED_'):
        raise ValueError('Replace the example model name with your installed model first.')
    standing = native_standing_profile(standing_path.resolve()) if standing_path else NATIVE_BEHAVIOR
    state = root / '.state'
    launch = state / 'launch'
    if (state / 'runtime' / 'core.sqlite3').exists() or (state / 'runtime.identity.json').exists():
        raise ValueError('This checkout already has an initialized archive; setup did not change it.')
    if launch.exists():
        raise ValueError('Launch inputs already exist; setup will not replace them.')
    if setup_runtimes:
        sys.path.insert(0, str(ROOT))
        from tools.setup_web import install as install_web
        from tools.setup_files import install as install_files
        install_web(root)
        install_files(root)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Exclusive creation protects concurrent setup. Partial inputs stay visible
    # for inspection; rerunning never overwrites a user's existing inputs.
    launch.mkdir(mode=0o700)
    for name, text in (
        ('profile.json', json.dumps(asdict(profile), indent=2) + '\n'),
        ('standing-profile.md', standing),
    ):
        fd = os.open(launch / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as target:
            target.write(text)
    return launch


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path,
        help='Validated profile JSON; defaults to the existing DeepSeek provider profile.')
    parser.add_argument('--standing-profile', type=Path)
    args = parser.parse_args(argv)
    try:
        launch = prepare(ROOT, args.profile, args.standing_profile, setup_runtimes=True)
    except (OSError, ValueError, Failure, subprocess.CalledProcessError) as error:
        parser.exit(1, (error.detail if isinstance(error, Failure) else str(error)) + '\n')
    print(f'Prepared new launch inputs in {launch}. The archive has not been initialized.')


if __name__ == '__main__':
    main()
