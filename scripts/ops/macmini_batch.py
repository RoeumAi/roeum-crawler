"""Serialize Mac mini batch runs and explicit, reviewed-commit deployments."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import os
from pathlib import Path
import re
import subprocess
import sys


@contextmanager
def exclusive_lock(path):
    # Never unlink: all callers must lock the same inode, even after a crash.
    with open(path, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield handle


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE,
        timeout=120,
    ).strip()


def deploy(root, revision):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("Deploy requires a full reviewed commit SHA.")
    if git(root, "branch", "--show-current") != "main":
        raise RuntimeError("Deployment checkout must be on main.")
    # .env is machine-owned. Refuse all other tracked edits.
    dirty = git(root, "diff", "HEAD", "--name-only", "--", ".", ":(exclude).env")
    if dirty:
        raise RuntimeError("Tracked source changes exist; deployment refused.")
    git(root, "fetch", "origin", "main")
    git(root, "merge-base", "--is-ancestor", revision, "origin/main")
    git(root, "merge-base", "--is-ancestor", "HEAD", revision)
    changed = git(root, "diff", "--name-only", "HEAD", revision).splitlines()
    if any(p == ".env" or p.startswith("requirements") or
           p in {"pyproject.toml", "poetry.lock", "uv.lock"} for p in changed):
        raise RuntimeError("Environment/dependency changes require a maintenance deployment.")
    git(root, "merge", "--ff-only", revision)
    print(f"Deployed revision={git(root, 'rev-parse', 'HEAD')}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--lock", type=Path, default=Path("/tmp/roeum_crawler.lock"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--deploy", metavar="SHA")
    mode.add_argument("--days", choices=["1", "7"])
    mode.add_argument("--check-lock", action="store_true")
    args = parser.parse_args()
    try:
        with exclusive_lock(args.lock) as handle:
            if args.check_lock:
                print("Batch lock available", flush=True)
                return 0
            root = args.root.resolve()
            (root / "logs").mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            mode_name = "deploy" if args.deploy else ("daily" if args.days == "1" else "weekly")
            with (root / "logs" / f"{mode_name}_{stamp}_{os.getpid()}.log").open("a") as log:
                if args.deploy:
                    # Capture metadata, never credentials or git stderr.
                    try:
                        deploy(root, args.deploy)
                        log.write(f"deployed_revision={args.deploy}\n")
                    except Exception:
                        log.write(f"deployment_failed revision={args.deploy}\n")
                        raise
                    return 0
                revision = git(root, "rev-parse", "HEAD")
                dirty = bool(git(root, "diff", "HEAD", "--name-only", "--", ".", ":(exclude).env"))
                log.write(f"revision={revision} source_dirty={dirty} python={sys.version.split()[0]} days={args.days}\n")
                log.flush()
                if dirty:
                    raise RuntimeError("Tracked source changes exist; batch refused.")
                env = dict(os.environ, CRAWLER_DIR=str(root), CRAWLER_PYTHON=sys.executable,
                           CRAWLER_LOG_FILE=str(Path(log.name).resolve()))
                # The shell and its children inherit the lock, so coordinator
                # termination cannot allow deployment while crawling is still alive.
                result = subprocess.run(
                    ["/bin/bash", str(root / "scripts/ops/run_batch.sh"), args.days],
                    cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                    pass_fds=(handle.fileno(),),
                )
                log.write(f"batch_exit_code={result.returncode}\n")
                print(f"{mode_name}: revision={revision} exit={result.returncode}", flush=True)
                return result.returncode
    except BlockingIOError:
        print("Crawler/deployment already running; no work started.", file=sys.stderr)
        return 75
    except subprocess.SubprocessError:
        print("Git operation failed; no batch started. Check repository access/state.", file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
