import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/ops/macmini_batch.py'


def load_module():
    assert SCRIPT.exists(), 'Mac mini batch coordinator is missing'
    spec = importlib.util.spec_from_file_location('macmini_batch', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


@pytest.fixture
def repositories(tmp_path):
    origin = tmp_path / 'origin'
    origin.mkdir()
    git(origin, 'init', '-b', 'main')
    git(origin, 'config', 'user.name', 'Test')
    git(origin, 'config', 'user.email', 'test@example.com')
    (origin / '.env').write_text('local-config')
    (origin / 'source.py').write_text('v1')
    git(origin, 'add', '.')
    git(origin, 'commit', '-m', 'initial')
    checkout = tmp_path / 'checkout'
    subprocess.run(['git', 'clone', str(origin), str(checkout)], check=True, capture_output=True)
    (checkout / '.env').write_text('machine-config')
    (origin / 'source.py').write_text('v2')
    git(origin, 'commit', '-am', 'next')
    return origin, checkout


def test_deploy_exact_commit_preserves_local_env(repositories):
    mod = load_module()
    origin, checkout = repositories
    target = git(origin, 'rev-parse', 'HEAD')
    mod.deploy(checkout, target)
    assert git(checkout, 'rev-parse', 'HEAD') == target
    assert (checkout / '.env').read_text() == 'machine-config'


def test_deploy_rejects_dirty_code(repositories):
    mod = load_module()
    origin, checkout = repositories
    (checkout / 'source.py').write_text('local-change')
    with pytest.raises(RuntimeError):
        mod.deploy(checkout, git(origin, 'rev-parse', 'HEAD'))
    assert (checkout / 'source.py').read_text() == 'local-change'


def test_deploy_rejects_env_and_dependency_changes(repositories):
    mod = load_module()
    origin, checkout = repositories
    (origin / 'requirements.txt').write_text('new-dependency')
    git(origin, 'add', '.')
    git(origin, 'commit', '-m', 'deps')
    old = git(checkout, 'rev-parse', 'HEAD')
    with pytest.raises(RuntimeError):
        mod.deploy(checkout, git(origin, 'rev-parse', 'HEAD'))
    assert git(checkout, 'rev-parse', 'HEAD') == old


def test_lock_excludes_other_process_and_recovers_after_exit(tmp_path):
    mod = load_module()
    path = tmp_path / 'batch.lock'
    with mod.exclusive_lock(path):
        result = subprocess.run([sys.executable, str(SCRIPT), '--lock', str(path), '--check-lock'], capture_output=True)
        assert result.returncode == 75
    result = subprocess.run([sys.executable, str(SCRIPT), '--lock', str(path), '--check-lock'], capture_output=True)
    assert result.returncode == 0


def test_shell_returns_crawl_failure_after_successful_notification(tmp_path):
    load_module()
    body = SCRIPT.parent / 'run_batch.sh'
    fake_python = tmp_path / 'python'
    fake_python.write_text('#!/bin/sh\ncase "$*" in *crawl.py*) exit 1;; *) exit 0;; esac\n')
    fake_python.chmod(0o755)
    result = subprocess.run(['bash', str(body), '1'], env={'PATH':'/usr/bin:/bin', 'CRAWLER_DIR':str(tmp_path), 'CRAWLER_PYTHON':str(fake_python)})
    assert result.returncode == 1


def test_deploy_rejects_unmerged_revision(repositories):
    mod = load_module()
    origin, checkout = repositories
    git(origin, 'checkout', '-b', 'unreviewed')
    (origin / 'source.py').write_text('unmerged')
    git(origin, 'commit', '-am', 'unmerged')
    old = git(checkout, 'rev-parse', 'HEAD')
    with pytest.raises(subprocess.CalledProcessError):
        mod.deploy(checkout, git(origin, 'rev-parse', 'HEAD'))
    assert git(checkout, 'rev-parse', 'HEAD') == old


def test_deploy_rejects_rollback(repositories):
    mod = load_module()
    origin, checkout = repositories
    old = git(checkout, 'rev-parse', 'HEAD')
    mod.deploy(checkout, git(origin, 'rev-parse', 'HEAD'))
    with pytest.raises(subprocess.CalledProcessError):
        mod.deploy(checkout, old)


def test_refresh_failure_is_not_hidden(tmp_path):
    load_module()
    fake_python = tmp_path / 'python'
    fake_python.write_text('#!/bin/sh\ncase "$*" in *refresh_current*) exit 9;; *) exit 0;; esac\n')
    fake_python.chmod(0o755)
    result = subprocess.run(['bash', str(SCRIPT.parent / 'run_batch.sh'), '1'],
                            env={'PATH':'/usr/bin:/bin', 'CRAWLER_DIR':str(tmp_path),
                                 'CRAWLER_PYTHON':str(fake_python)})
    assert result.returncode == 9


def test_weekly_keeps_nodong_and_propagates_failure(tmp_path):
    load_module()
    fake_python = tmp_path / 'python'
    fake_python.write_text('#!/bin/sh\ncase "$*" in *nodong*) exit 7;; *) exit 0;; esac\n')
    fake_python.chmod(0o755)
    result = subprocess.run(['bash', str(SCRIPT.parent / 'run_batch.sh'), '7'],
                            env={'PATH':'/usr/bin:/bin', 'CRAWLER_DIR':str(tmp_path),
                                 'CRAWLER_PYTHON':str(fake_python)})
    assert result.returncode == 7
