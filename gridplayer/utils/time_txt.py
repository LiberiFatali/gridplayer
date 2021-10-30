import time


def get_time_txt_short(seconds):
    if seconds > 3600:
        return time.strftime("%H:%M:%S", time.gmtime(seconds)).lstrip("0")
    elif seconds > 60:
        return time.strftime("%M:%S", time.gmtime(seconds)).lstrip("0")

    return time.strftime("0:%S", time.gmtime(seconds))


def get_time_txt(seconds, max_seconds=None):
    seconds_cnt = max_seconds or seconds

    if seconds_cnt > 3600:
        return time.strftime("%H:%M:%S", time.gmtime(seconds))
    elif seconds_cnt > 60:
        return time.strftime("%M:%S", time.gmtime(seconds))

    return time.strftime("0:%S", time.gmtime(seconds))