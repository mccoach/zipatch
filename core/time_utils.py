# -*- coding: utf-8 -*-

from datetime import datetime


def current_timestamp_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def current_log_time():
    return datetime.now().strftime("%H:%M:%S")


def now_stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")
