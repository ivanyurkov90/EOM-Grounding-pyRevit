# -*- coding: utf-8 -*-
from __future__ import print_function
import os
import time
import tempfile

TRACE_PATH = os.path.join(tempfile.gettempdir(), 'EOM_Grounding_trace.log')
_t0 = None


def _safe_text(value):
    try:
        return u"{}".format(value if value is not None else u"")
    except Exception:
        try:
            return str(value)
        except Exception:
            return u"<unprintable>"


def reset(label='EOM Grounding'):
    global _t0
    _t0 = time.time()
    try:
        with open(TRACE_PATH, 'w') as f:
            f.write('0.000 | START | {}\n'.format(_safe_text(label)))
    except Exception:
        pass
    return TRACE_PATH


def mark(stage, detail=''):
    global _t0
    if _t0 is None:
        _t0 = time.time()
    dt = time.time() - _t0
    try:
        with open(TRACE_PATH, 'a') as f:
            f.write('{:.3f} | {} | {}\n'.format(dt, _safe_text(stage), _safe_text(detail or '')))
    except Exception:
        pass


def exception_text(ex):
    """Compact exception text safe for trace files and IronPython/.NET exceptions."""
    name = u"Exception"
    try:
        name = ex.GetType().FullName
    except Exception:
        try:
            name = ex.__class__.__name__
        except Exception:
            pass
    message = _safe_text(ex)
    return u"{}: {}".format(name, message)


def mark_exception(stage, ex, detail=''):
    text = exception_text(ex)
    if detail:
        text = u"{} | {}".format(_safe_text(detail), text)
    mark(stage, text)
    return text


def path():
    return TRACE_PATH
