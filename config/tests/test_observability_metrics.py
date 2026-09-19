from config.observability import metrics


def test_set_build_info_records_the_version_label():
    metrics.set_build_info("abc123")

    assert metrics.build_info.labels(version="abc123")._value.get() == 1
