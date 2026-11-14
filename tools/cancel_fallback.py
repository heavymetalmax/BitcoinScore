"""Cancel active or queued runs of the GitHub Actions fallback workflow (update-data.yml).

Uses `gh` CLI if installed and authenticated, or GitHub REST API via GITHUB_TOKEN.
"""
import json
import os
import subprocess
import sys


def cancel_active_fallback_runs() -> int:
    cancelled_count = 0

    # 1. Try gh CLI
    try:
        proc = subprocess.run(
            [
                'gh', 'run', 'list',
                '--workflow=update-data.yml',
                '--json', 'databaseId,status',
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            runs = json.loads(proc.stdout)
            active_ids = [
                r['databaseId']
                for r in runs
                if r.get('status') in ('in_progress', 'queued') and r.get('databaseId')
            ]
            if active_ids:
                for run_id in active_ids:
                    print(f"Cancelling GitHub fallback run {run_id} via gh CLI...")
                    res = subprocess.run(
                        ['gh', 'run', 'cancel', str(run_id)],
                        capture_output=True,
                        text=True,
                        timeout=15,
                        check=False,
                    )
                    if res.returncode == 0:
                        print(f"Successfully cancelled run {run_id}.")
                        cancelled_count += 1
                    else:
                        print(f"Failed to cancel run {run_id}: {res.stderr.strip()}")
            else:
                print("No active or queued fallback runs found on GitHub Actions.")
            return cancelled_count
    except Exception as e:
        print(f"Notice: gh CLI check failed ({e}). Trying fallback method...")

    # 2. Try REST API if GITHUB_TOKEN is available
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    repo = os.environ.get('GITHUB_REPOSITORY', 'heavymetalmax/BitcoinScore')
    if token:
        try:
            import urllib.request
            url = f"https://api.github.com/repos/{repo}/actions/workflows/update-data.yml/runs?status=in_progress"
            req = urllib.request.Request(
                url,
                headers={
                    'Authorization': f'Bearer {token}',
                    'Accept': 'application/vnd.github+json',
                    'User-Agent': 'BitcoinScore-CancelFallback',
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                for run in data.get('workflow_runs', []):
                    run_id = run['id']
                    cancel_url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/cancel"
                    cancel_req = urllib.request.Request(
                        cancel_url,
                        method='POST',
                        headers={
                            'Authorization': f'Bearer {token}',
                            'Accept': 'application/vnd.github+json',
                            'User-Agent': 'BitcoinScore-CancelFallback',
                        },
                    )
                    try:
                        with urllib.request.urlopen(cancel_req, timeout=10) as c_resp:
                            if c_resp.status in (200, 202):
                                print(f"Successfully cancelled run {run_id} via API.")
                                cancelled_count += 1
                    except Exception as ce:
                        print(f"API cancel error for {run_id}: {ce}")
        except Exception as e:
            print(f"Notice: GitHub API fallback check failed: {e}")

    return cancelled_count


if __name__ == '__main__':
    cancel_active_fallback_runs()
