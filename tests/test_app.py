from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "src/app.py")


def test_app_runs_on_sample_data():
    app = AppTest.from_file(APP, default_timeout=60).run()
    assert not app.exception and not app.error
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Combinations predicted"] == "321" and metrics["Data checks"] == "passed"
    assert any("notebook holdout" in str(df.value.to_dict()) for df in app.dataframe)  # accuracy history shown
