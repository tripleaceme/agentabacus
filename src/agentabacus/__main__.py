"""Lets the package run as `python -m agentabacus`.

Schedulers (launchd, systemd, Task Scheduler) run with a minimal PATH, so the
`agentabacus` console script -- which may live in a virtualenv or a pipx shim
-- is often not resolvable. Invoking the interpreter that installed the
package always is.
"""

from .cli import main

if __name__ == "__main__":
    main()
