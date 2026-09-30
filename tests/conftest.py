import os
from pathlib import Path

import pytest

from src.pipeline import read_csv

os.chdir(Path(__file__).resolve().parents[1])  # tests use repo-relative paths; runs before the test modules load
# Training files are not in the repo (it ships only the inference example). The notebook-reproduction tests read them
# from the challenge package, by default next to this repo; set TRAINING_DATA to point elsewhere.
TRAINING = Path(os.environ.get("TRAINING_DATA", Path(__file__).resolve().parents[2] / "instructions/data"))


@pytest.fixture(scope="session")
def training():
    """(train_movies, train_consumption), or skip when the challenge package isn't available."""
    if not (TRAINING / "train_movies.csv").exists():
        pytest.skip(f"training data not found in {TRAINING} (set TRAINING_DATA)")
    return read_csv(TRAINING / "train_movies.csv"), read_csv(TRAINING / "train_consumption.csv")
