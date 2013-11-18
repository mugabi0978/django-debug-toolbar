from __future__ import unicode_literals

import inspect
import os.path
import re
import sys

import django
from django.core.exceptions import ImproperlyConfigured
from django.utils.encoding import force_text
from django.utils.html import escape
from django.utils.importlib import import_module
from django.utils.safestring import mark_safe
from django.utils import six
from django.views.debug import linebreak_iter

from .settings import CONFIG

# Figure out some paths
django_path = os.path.realpath(os.path.dirname(django.__file__))


def get_module_path(module_name):
    try:
        module = import_module(module_name)
    except ImportError as e:
        raise ImproperlyConfigured(
            'Error importing HIDDEN_STACKTRACE_MODULES: %s' % (e,))
    else:
        source_path = inspect.getsourcefile(module)
        if source_path.endswith('__init__.py'):
            source_path = os.path.dirname(source_path)
        return os.path.realpath(source_path)


hidden_paths = [
    get_module_path(module_name)
    for module_name in CONFIG['HIDDEN_STACKTRACE_MODULES']
]


def omit_path(path):
    return any(path.startswith(hidden_path) for hidden_path in hidden_paths)


def tidy_stacktrace(stack):
    """
    Clean up stacktrace and remove all entries that:
    1. Are part of Django (except contrib apps)
    2. Are part of socketserver (used by Django's dev server)
    3. Are the last entry (which is part of our stacktracing code)

    ``stack`` should be a list of frame tuples from ``inspect.stack()``
    """
    trace = []
    for frame, path, line_no, func_name, text in (f[:5] for f in stack):
        s_path = os.path.realpath(path)
        # Support hiding of frames -- used in various utilities that provide
        # inspection.
        if CONFIG['HIDE_DJANGO_SQL'] and django_path in s_path and not 'django/contrib' in s_path:
            continue
        if omit_path(s_path):
            continue
        text = (''.join(force_text(t) for t in text)).strip() if text else ''
        trace.append((path, line_no, func_name, text))
    return trace


def render_stacktrace(trace):
    stacktrace = []
    for frame in trace:
        params = map(escape, frame[0].rsplit(os.path.sep, 1) + list(frame[1:]))
        params_dict = dict((six.text_type(idx), v) for idx, v in enumerate(params))
        try:
            stacktrace.append('<span class="path">%(0)s/</span>'
                              '<span class="file">%(1)s</span>'
                              ' in <span class="func">%(3)s</span>'
                              '(<span class="lineno">%(2)s</span>)\n'
                              '  <span class="code">%(4)s</span>'
                              % params_dict)
        except KeyError:
            # This frame doesn't have the expected format, so skip it and move on to the next one
            continue
    return mark_safe('\n'.join(stacktrace))


def get_template_info(source, context_lines=3):
    line = 0
    upto = 0
    source_lines = []
    # before = during = after = ""

    origin, (start, end) = source
    template_source = origin.reload()

    for num, next in enumerate(linebreak_iter(template_source)):
        if start >= upto and end <= next:
            line = num
            # before = template_source[upto:start]
            # during = template_source[start:end]
            # after = template_source[end:next]
        source_lines.append((num, template_source[upto:next]))
        upto = next

    top = max(1, line - context_lines)
    bottom = min(len(source_lines), line + 1 + context_lines)

    context = []
    for num, content in source_lines[top:bottom]:
        context.append({
            'num': num,
            'content': content,
            'highlight': (num == line),
        })

    return {
        'name': origin.name,
        'context': context,
    }


def get_name_from_obj(obj):
    if hasattr(obj, '__name__'):
        name = obj.__name__
    elif hasattr(obj, '__class__') and hasattr(obj.__class__, '__name__'):
        name = obj.__class__.__name__
    else:
        name = '<unknown>'

    if hasattr(obj, '__module__'):
        module = obj.__module__
        name = '%s.%s' % (module, name)

    return name


def getframeinfo(frame, context=1):
    """
    Get information about a frame or traceback object.

    A tuple of five things is returned: the filename, the line number of
    the current line, the function name, a list of lines of context from
    the source code, and the index of the current line within that list.
    The optional second argument specifies the number of lines of context
    to return, which are centered around the current line.

    This originally comes from ``inspect`` but is modified to handle issues
    with ``findsource()``.
    """
    if inspect.istraceback(frame):
        lineno = frame.tb_lineno
        frame = frame.tb_frame
    else:
        lineno = frame.f_lineno
    if not inspect.isframe(frame):
        raise TypeError('arg is not a frame or traceback object')

    filename = inspect.getsourcefile(frame) or inspect.getfile(frame)
    if context > 0:
        start = lineno - 1 - context // 2
        try:
            lines, lnum = inspect.findsource(frame)
        except Exception:   # findsource raises platform-dependant exceptions
            first_lines = lines = index = None
        else:
            start = max(start, 1)
            start = max(0, min(start, len(lines) - context))
            first_lines = lines[:2]
            lines = lines[start:(start + context)]
            index = lineno - 1 - start
    else:
        first_lines = lines = index = None

    # Code taken from Django's ExceptionReporter._get_lines_from_file
    if first_lines and isinstance(first_lines[0], bytes):
        encoding = 'ascii'
        for line in first_lines[:2]:
            # File coding may be specified. Match pattern from PEP-263
            # (http://www.python.org/dev/peps/pep-0263/)
            match = re.search(br'coding[:=]\s*([-\w.]+)', line)
            if match:
                encoding = match.group(1).decode('ascii')
                break
        lines = [line.decode(encoding, 'replace') for line in lines]

    if hasattr(inspect, 'Traceback'):
        return inspect.Traceback(filename, lineno, frame.f_code.co_name, lines, index)
    else:
        return (filename, lineno, frame.f_code.co_name, lines, index)


def get_stack(context=1):
    """
    Get a list of records for a frame and all higher (calling) frames.

    Each record contains a frame object, filename, line number, function
    name, a list of lines of context, and index within the context.

    Modified version of ``inspect.stack()`` which calls our own ``getframeinfo()``
    """
    frame = sys._getframe(1)
    framelist = []
    while frame:
        framelist.append((frame,) + getframeinfo(frame, context))
        frame = frame.f_back
    return framelist
