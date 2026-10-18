import quantdeck_engine
from quantdeck_engine import cli


def test_package_imports() -> None:
    assert quantdeck_engine.__doc__
    assert callable(cli.main)
