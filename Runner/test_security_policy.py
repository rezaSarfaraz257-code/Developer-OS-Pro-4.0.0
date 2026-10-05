import pytest

from fastapi import HTTPException

import main


@pytest.mark.parametrize("command", [
    "rm -rf /",
    "curl https://example.com",
    "wget https://example.com",
    "docker ps",
    "nsenter --target 1",
    "mount /dev/sda /mnt",
    "cat /etc/shadow",
    "kill -9 1",
])
def test_blocked_commands(command):
    with pytest.raises(HTTPException) as exc:
        main._validate_execution_policy(command)
    assert exc.value.status_code == 400


def test_network_is_denied_by_default():
    with pytest.raises(HTTPException) as exc:
        main._validate_execution_policy("python -c 'print(1)'", allow_network=True)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("path", [
    "../secret.txt",
    "/etc/passwd",
    ".git/config",
    "src/../../secret.py",
    "C:/Windows/System32",
    "",
])
def test_workspace_path_traversal_is_rejected(path):
    with pytest.raises(ValueError):
        main.safe_rel(path)


def test_resource_limits_are_declared():
    assert main.TIMEOUT >= 5
    assert main.MAX_COMMAND > 0
    assert main.MAX_WORKSPACE_BYTES > 0
    assert main.MAX_OUTPUT > 0
    assert main.MAX_CONCURRENT >= 1
