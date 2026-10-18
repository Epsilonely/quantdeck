"""`python -m quantdeck_engine`: the same commands as `quantdeck-engine`.

The GUI starts the engine this way with the virtualenv's `pythonw` (D-34), which has no
console, so Windows opens no console window for it.
"""

from quantdeck_engine.cli import main

main()
