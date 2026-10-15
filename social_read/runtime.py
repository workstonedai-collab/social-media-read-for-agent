"""Project runtimes work even when an Agent starts with a minimal PATH."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def command_env(environment=None):
    env = dict(os.environ if environment is None else environment)
    bins = [ROOT / '.state/runtime/node/bin', ROOT / '.state/opencli/node_modules/.bin']
    env['PATH'] = os.pathsep.join([str(p) for p in bins if p.is_dir()] + [env.get('PATH', '/usr/bin:/bin')])
    return env


def opencli_executable():
    import shutil
    local = ROOT / '.state/opencli/node_modules/.bin/opencli'
    return str(local) if local.is_file() else shutil.which('opencli', path=command_env()['PATH'])
