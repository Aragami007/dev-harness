import subprocess


class FakeRun:
    """Stand-in for subprocess.run.

    responses: maps a command prefix tuple, e.g. ("git", "status"), to (returncode, stdout).
    fail_on: any command containing one of these exact tokens returns code 1.
    """

    def __init__(self, responses=None, fail_on=()):
        self.calls = []
        self.responses = responses or {}
        self.fail_on = set(fail_on)

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        for prefix, (code, out) in self.responses.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                return subprocess.CompletedProcess(cmd, code, out, "")
        code = 1 if self.fail_on.intersection(cmd) else 0
        return subprocess.CompletedProcess(cmd, code, "", "")
