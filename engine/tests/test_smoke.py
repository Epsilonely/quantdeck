import quantdeck_engine


def test_package_imports() -> None:
    assert callable(quantdeck_engine.main)
