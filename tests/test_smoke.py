from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_samples_exist():
    for name in ("20261109", "20261116"):
        assert (ROOT / "data" / f"{name}.hwpx").exists()
