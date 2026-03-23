from datetime import datetime


def local_now() -> datetime:
    return datetime.now().astimezone().replace(tzinfo=None)


def local_today():
    return local_now().date()
