from scripts.qmllint import check


def test_qml_passes_qmllint() -> None:
    """Nothing beyond scripts/qmllint_exemptions.toml, and no exemption left unused."""
    unexpected, stale = check()
    assert [str(finding) for finding in unexpected] == []
    assert stale == []
