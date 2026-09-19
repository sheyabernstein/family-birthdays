from django.conf import settings


def config(*args, **kwargs) -> dict:
    return {"BUILD_VERSION": settings.BUILD_VERSION}
