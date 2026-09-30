from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "src/app.py")


def test_app_runs_on_sample_data():
    app = AppTest.from_file(APP, default_timeout=60).run()
    assert not app.exception and app.button(key="run").disabled  # landing: nothing uploaded yet
    assert len(app.dataframe) == 2  # the expected format of both files

    app.button(key="sample").click().run()
    assert not app.exception and not app.error
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Title × market × platform"] == "321"
    assert metrics["Forecast month"] == "Jun 2026" and metrics["Data checks"] == "Passed"
    assert metrics["Checks passed"] == "15 of 15"
    assert any("Holdout evaluation" in str(df.value.to_dict()) for df in app.dataframe)  # accuracy history shown

    app.button(key="new").click().run()
    assert "result" not in app.session_state and app.button(key="run")  # back to the input stage
