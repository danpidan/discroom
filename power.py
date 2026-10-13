"""Request an orderly system shutdown through the desktop user's permissions."""
import subprocess


def request_power(action):
    if action not in ("reboot", "poweroff"):
        raise ValueError("Unknown Pi power action.")
    label = "restart" if action == "reboot" else "shut down"
    try:
        result = subprocess.run(["systemctl", "--no-ask-password", action],
                                capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        raise RuntimeError("The Pi's system power command was not found.") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"The Pi did not respond to the {label} request. The app remains open.") from None
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1500:]
        raise RuntimeError(f"The Pi could not {label}. The app remains open.\n"
                           + (detail or "The system refused the request."))
    return "Restarting Pi…" if action == "reboot" else "Shutting down Pi…"
