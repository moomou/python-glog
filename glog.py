"""A simple Google-style logging wrapper."""
import sys
import hashlib
import inspect
import logging
import os
import random
import time
import traceback
from collections import defaultdict

import gflags as flags

FLAGS = flags.FLAGS


def _noop(*args, **kwargs):
    pass


class NoOp:
    def __getattr__(self, name):
        return _noop

noop = NoOp()


def format_message(record):
    try:
        record_message = "%s" % (record.msg % record.args)
    except TypeError:
        record_message = record.msg
    return record_message


class GlogFormatter(logging.Formatter):
    LEVEL_MAP = {
        logging.FATAL: "F",  # FATAL is alias of CRITICAL
        logging.ERROR: "E",
        logging.WARN: "W",
        logging.INFO: "I",
        logging.DEBUG: "D",
    }

    def __init__(self):
        logging.Formatter.__init__(self)

    def format(self, record):
        try:
            level = GlogFormatter.LEVEL_MAP[record.levelno]
        except KeyError:
            level = "?"

        # 2 represents line at caller
        callerframerecord = inspect.stack()[-1]  # 0 represents this line
        frame = callerframerecord[0]
        frame_info = inspect.getframeinfo(frame)

        date = time.localtime(record.created)
        date_usec = (record.created - int(record.created)) * 1e6
        record_message = "%c%02d%02d %02d:%02d:%02d.%06d %s %s:%d] %s" % (
            level,
            date.tm_mon,
            date.tm_mday,
            date.tm_hour,
            date.tm_min,
            date.tm_sec,
            date_usec,
            record.process if record.process is not None else "?????",
            frame_info.filename,
            frame_info.lineno,
            format_message(record),
        )
        record.getMessage = lambda: record_message
        return logging.Formatter.format(self, record)


logger = logging.getLogger()
handler = logging.StreamHandler()


def setLevel(newlevel):
    logger.setLevel(newlevel)
    logger.debug("Log level set to %s", newlevel)


def init():
    setLevel(FLAGS.verbosity)


def log_wrapper(name):
    counter = defaultdict(int)

    def conditional_log(*args, **kwargs):
        sampling = kwargs.pop("sampling", 100)
        first_n = kwargs.pop("first_n", -1)

        if sampling < 100:
            if sampling < random.random() * 100:
                return
        if first_n > 0:
            key = hashlib.md5(args[0].encode("utf-8")).hexdigest()
            if counter[key] > first_n:
                return
            counter[key] += 1

        return getattr(logging, name)(*args)

    return conditional_log


debug = log_wrapper("debug")
info = log_wrapper("info")
warning = log_wrapper("warning")
warn = log_wrapper("warning")
error = log_wrapper("error")
exception = log_wrapper("exception")
fatal = log_wrapper("fatal")
log = log_wrapper("log")

DEBUG = log_wrapper("DEBUG")
INFO = log_wrapper("INFO")
WARNING = log_wrapper("WARNING")
WARN = log_wrapper("WARN")
ERROR = log_wrapper("ERROR")
FATAL = log_wrapper("FATAL")

_level_names = {
    DEBUG: "DEBUG",
    INFO: "INFO",
    WARN: "WARN",
    ERROR: "ERROR",
    FATAL: "FATAL",
}

_level_letters = [name[0] for name in _level_names.values()]

GLOG_PREFIX_REGEX = (
    (
        r"""
    (?x) ^
    (?P<severity>[%s])
    (?P<month>\d\d)(?P<day>\d\d)\s
    (?P<hour>\d\d):(?P<minute>\d\d):(?P<second>\d\d)
    \.(?P<microsecond>\d{6})\s+
    (?P<process_id>-?\d+)\s
    (?P<filename>[a-zA-Z<_][\w._<>-]+):(?P<line>\d+)
    \]\s
    """
    )
    % "".join(_level_letters)
)
"""Regex you can use to parse glog line prefixes."""

handler.setFormatter(GlogFormatter())
logger.addHandler(handler)


class CaptureWarningsFlag(flags.BooleanFlag):
    def __init__(self):
        flags.BooleanFlag.__init__(
            self,
            "glog_capture_warnings",
            True,
            "Redirect warnings to log.warn messages",
        )

    def Parse(self, arg):
        flags.BooleanFlag.Parse(self, arg)
        logging.captureWarnings(self.value)


flags.DEFINE_flag(CaptureWarningsFlag())


class VerbosityParser(flags.ArgumentParser):
    """Sneakily use gflags parsing to get a simple callback."""

    def Parse(self, arg):
        try:
            intarg = int(arg)
            # Look up the name for this level (DEBUG, INFO, etc) if it exists
            try:
                level = logging._levelNames.get(intarg, intarg)
            except AttributeError:  # This was renamed somewhere b/w 2.7 and 3.4
                level = logging._levelToName.get(intarg, intarg)
        except ValueError:
            level = arg
        setLevel(level)
        return level


flags.DEFINE(
    parser=VerbosityParser(),
    serializer=flags.ArgumentSerializer(),
    name="verbosity",
    default=logging.INFO,
    help="Logging verbosity",
)

# Define functions emulating C++ glog check-macros
# https://htmlpreview.github.io/?https://github.com/google/glog/master/doc/glog.html#check


def format_stacktrace(stack):
    """Print a stack trace that is easier to read.

    * Reduce paths to basename component
    * Truncates the part of the stack after the check failure
    """
    lines = []
    for _, f in enumerate(stack):
        fname = os.path.basename(f[0])
        line = "\t%s:%d\t%s" % (fname + "::" + f[2], f[1], f[3])
        lines.append(line)
    return lines


class FailedCheckException(AssertionError):
    """Exception with message indicating check-failure location and values."""


def check_failed(message):
    stack = traceback.extract_stack()
    stack = stack[0:-2]
    stacktrace_lines = format_stacktrace(stack)
    filename, line_num, _, _ = stack[-1]

    try:
        raise FailedCheckException(message)
    except FailedCheckException:
        log_record = logger.makeRecord(
            "CRITICAL", 50, filename, line_num, message, None, None
        )
        handler.handle(log_record)

        log_record = logger.makeRecord(
            "DEBUG", 10, filename, line_num, "Check failed here:", None, None
        )
        handler.handle(log_record)
        for line in stacktrace_lines:
            log_record = logger.makeRecord(
                "DEBUG", 10, filename, line_num, line, None, None
            )
            handler.handle(log_record)
        raise
    return


def check(condition, message=None):
    """Raise exception with message if condition is False."""
    if not condition:
        if message is None:
            message = "Check failed."
        check_failed(message)


def check_eq(obj1, obj2, message=None):
    """Raise exception with message if obj1 != obj2."""
    if obj1 != obj2:
        if message is None:
            message = "Check failed: %s != %s" % (str(obj1), str(obj2))
        check_failed(message)


def check_ne(obj1, obj2, message=None):
    """Raise exception with message if obj1 == obj2."""
    if obj1 == obj2:
        if message is None:
            message = "Check failed: %s == %s" % (str(obj1), str(obj2))
        check_failed(message)


def check_le(obj1, obj2, message=None):
    """Raise exception with message if not (obj1 <= obj2)."""
    if obj1 > obj2:
        if message is None:
            message = "Check failed: %s > %s" % (str(obj1), str(obj2))
        check_failed(message)


def check_ge(obj1, obj2, message=None):
    """Raise exception with message unless (obj1 >= obj2)."""
    if obj1 < obj2:
        if message is None:
            message = "Check failed: %s < %s" % (str(obj1), str(obj2))
        check_failed(message)


def check_lt(obj1, obj2, message=None):
    """Raise exception with message unless (obj1 < obj2)."""
    if obj1 >= obj2:
        if message is None:
            message = "Check failed: %s >= %s" % (str(obj1), str(obj2))
        check_failed(message)


def check_gt(obj1, obj2, message=None):
    """Raise exception with message unless (obj1 > obj2)."""
    if obj1 <= obj2:
        if message is None:
            message = "Check failed: %s <= %s" % (str(obj1), str(obj2))
        check_failed(message)


def check_notnone(obj, message=None):
    """Raise exception with message if obj is None."""
    if obj is None:
        if message is None:
            message = "Check failed: Object is None."
        check_failed(message)


def lv(verbosity=0):
    if os.environ.get("DEBUG", "0") >= str(verbosity):
        return sys.modules[__name__]
    return noop

