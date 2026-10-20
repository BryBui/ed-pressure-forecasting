"""Stop processes using the demo ports. Run in WSL with .venv-wsl/bin/python.

Defaults to 8080 (Airflow) and 8050 (dashboard); pass other ports as arguments.
This stops port owners, not the complete demo. Stop the launcher with Ctrl+C first.
"""
import argparse
import shutil
import subprocess
import time


def owners(port):
    result = subprocess.run(
        ['fuser', '-n', 'tcp', str(port)], capture_output=True, text=True)
    if result.stderr and ('Permission denied' in result.stderr or result.returncode > 1):
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def release(port):
    if not owners(port):
        print(f'Port {port} is already free.')
        return True
    for signal in ('TERM', 'KILL'):
        subprocess.run(['fuser', '-k', f'-{signal}', '-n', 'tcp', str(port)],
                       capture_output=True, text=True)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not owners(port):
                print(f'Released port {port}.')
                return True
            time.sleep(0.1)
    print(f'Could not release port {port}; check process ownership/permissions.')
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ports', nargs='*', type=int, default=[8080, 8050])
    args = parser.parse_args()
    if any(not 1 <= port <= 65535 for port in args.ports):
        parser.error('Ports must be between 1 and 65535.')
    if not shutil.which('fuser'):
        parser.error('fuser is required. Install it in WSL with: sudo apt install psmisc')
    try:
        results = [release(port) for port in dict.fromkeys(args.ports)]
        return 0 if all(results) else 1
    except RuntimeError as error:
        print(f'Cannot inspect ports: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
