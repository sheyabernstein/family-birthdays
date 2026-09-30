from config.context_processors import config


def test_config_exposes_build_version_and_build_sha(settings):
    settings.BUILD_VERSION = "v1.2.3"
    settings.BUILD_SHA = "abc123def456"
    settings.BUILD_IS_RELEASE = True

    result = config()

    assert result == {"BUILD_VERSION": "v1.2.3", "BUILD_SHA": "abc123def456", "BUILD_IS_RELEASE": True}
