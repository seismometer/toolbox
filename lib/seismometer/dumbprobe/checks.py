#!/usr/bin/python
'''
Available checks
----------------

.. autoclass:: BaseCheck
   :members:

.. autoclass:: ShellOutputJSON
   :members:

.. autoclass:: ShellOutputMetric
   :members:

.. autoclass:: ShellOutputState
   :members:

.. autoclass:: ShellExitState
   :members:

.. autoclass:: Nagios
   :members:

.. autoclass:: Function
   :members:

'''
#-----------------------------------------------------------------------------

import time
import re
import json
import signal
import subprocess
import seismometer.message

__all__ = [
    'BaseCheck',
    'ShellOutputJSON', 'ShellOutputMetric', 'ShellOutputState',
    'ShellExitState', 'Nagios',
    'Function',
]

# XXX: this should be a field of `ShellExitState' class, but caused Sphinx to
# include unexpected parts of `signal' module documentation
_SIGNALS = dict([
    (num, name.lower())
    for (name, num) in signal.__dict__.items()
    if name.startswith("SIG") and not name.startswith("SIG_")
])

#-----------------------------------------------------------------------------
# base class for checks {{{

class BaseCheck(object):
    '''
    Base class for checks.
    '''
    def __init__(self, interval, aspect = None, location = {}, **kwargs):
        '''
        :param interval: number of seconds between consequent checks
        :param aspect: aspect name, as in :class:`seismometer.message.Message`
        :param location: ``str => str`` dictionary, as in
            :class:`seismometer.message.Message`
        :param kwargs: additional keys to be added to :obj:`location` (kwargs
            take precedence over individual values in :obj:`location`)

        Fields defined by this class:

        .. attribute:: interval

           interval at which this check should be run, in seconds

        .. attribute:: last_run

           last time when this check was run (epoch timestamp)

        .. attribute:: aspect

           name of monitored aspect to be set

        .. attribute:: location

           location to be set (dict ``str => str``)

        '''
        self.interval = interval
        self.aspect = aspect
        self.location = location.copy()
        self.location.update(kwargs)
        # XXX: it's epoch time; let's assume January 1970 is a good -infinity
        self.last_run = 0

    def run(self):
        '''
        :return: check result
        :rtype: :class:`seismometer.message.Message`, dict, list of these, or
            ``None``

        Run the check.

        Implementing method should manually call :meth:`mark_run()` for
        :meth:`next_run()` to work correctly. To limit problems with
        unexpected exceptions, :meth:`mark_run()` should be run just at the
        beginning.
        '''
        raise NotImplementedError("method run() not implemented")

    def mark_run(self):
        '''
        Update last run timestamp.
        '''
        self.last_run = time.time()

    def next_run(self):
        '''
        :return: epoch time when the check should be run next time
        '''
        return self.last_run + self.interval

    def _populate(self, message):
        '''
        Helper to add aspect name and location to message, whatever it is.
        '''
        if isinstance(message, seismometer.message.Message):
            if self.aspect is not None:
                message.aspect = self.aspect
            for (l,v) in self.location.iteritems():
                message.location[l] = v
        else: # dict
            if self.aspect is not None:
                # XXX: if message['event'] does't exist, it's not valid
                # SSMM.Msg v3, so "aspect" term doesn't have any sense
                message['event']['name'] = self.aspect
            if len(self.location) > 0:
                message['location'].update(self.location)
        return message

# }}}
#-----------------------------------------------------------------------------
# plugins that call external commands {{{

class ShellOutputJSON(BaseCheck):
    '''
    Plugin to run external command and collect its *STDOUT* as a message.

    The command is expected to print JSON, and this JSON is returned
    unmodified. Command may output more than one JSON object, all of them will
    be returned as check results.
    '''
    def __init__(self, command, **kwargs):
        '''
        :param command: command to run (string for shell command, or list of
            strings for direct command to run)
        '''
        super(ShellOutputJSON, self).__init__(**kwargs)
        self.command = command
        self.use_shell = not isinstance(command, (list, tuple))
        self.json = json.JSONDecoder()

    def run(self):
        self.mark_run()

        (exitcode, stdout) = run(self.command, self.use_shell)
        if exitcode != 0:
            # TODO: report error (exitcode < 0 -- signal)
            return None

        def skip_spaces(string, offset = 0):
            while offset < len(string) and \
                  string[offset] in (' ', '\t', '\r', '\n'):
                offset += 1
            return offset

        messages = []
        offset = skip_spaces(stdout)
        while offset < len(stdout):
            (msg, offset) = self.json.raw_decode(stdout, offset)
            messages.append(self._populate(msg))
            offset = skip_spaces(stdout, offset)

        return messages

class ShellOutputMetric(BaseCheck):
    '''
    Plugin to collect metric from *STDOUT* of a command.

    The command is expected to print just a number (integer or floating
    point).
    '''
    def __init__(self, command, aspect, **kwargs):
        '''
        :param command: command to run (string for shell command, or list of
            strings for direct command to run)
        :param aspect: aspect name, as in :class:`seismometer.message.Message`
        '''
        super(ShellOutputMetric, self).__init__(aspect = aspect, **kwargs)
        self.command = command
        self.use_shell = not isinstance(command, (list, tuple))

    def run(self):
        self.mark_run()

        (exitcode, stdout) = run(self.command, self.use_shell)
        if exitcode != 0:
            # TODO: report error (exitcode < 0 -- signal)
            return None

        metric = stdout.strip().lower()
        if '.' in metric or 'e' in metric:
            metric = float(metric)
        else:
            metric = int(metric)

        return seismometer.message.Message(
            value = metric,
            aspect = self.aspect,
            location = self.location,
        )

class ShellOutputState(BaseCheck):
    '''
    Plugin to collect state from *STDOUT* of a command.

    The command should print the state as a single word. The state is then
    checked against expected states to determine its severity.
    '''
    def __init__(self, command, expected, aspect, **kwargs):
        '''
        :param command: command to run (string for shell command, or list of
            strings for direct command to run)
        :param expected: list of states of severity *expected*; all the others
            are considered *error*
        :param aspect: aspect name, as in :class:`seismometer.message.Message`
        '''
        super(ShellOutputState, self).__init__(aspect = aspect, **kwargs)
        self.command = command
        self.use_shell = not isinstance(command, (list, tuple))
        self.expected = set(expected)

    def run(self):
        self.mark_run()

        (exitcode, stdout) = run(self.command, self.use_shell)
        if exitcode != 0:
            # TODO: report error (exitcode < 0 -- signal)
            return None

        state = stdout.strip()
        if state in self.expected:
            severity = 'expected'
        else:
            severity = 'error'

        return seismometer.message.Message(
            state = state, severity = severity,
            aspect = self.aspect,
            location = self.location,
        )

class ShellExitState(BaseCheck):
    '''
    Plugin to collect state from exit code of a command.

    Exit code of 0 renders ``ok, expected`` message, any other renders
    ``exit_$?, error`` or ``$signame, error`` (``$?`` being the actual exit
    code and ``$signame`` name of signal, like ``sighup`` or ``sigsegv``).
    '''
    def __init__(self, command, aspect, **kwargs):
        '''
        :param command: command to run (string for shell command, or list of
            strings for direct command to run)
        :param aspect: aspect name, as in :class:`seismometer.message.Message`
        '''
        super(ShellExitState, self).__init__(aspect = aspect, **kwargs)
        self.command = command
        self.use_shell = not isinstance(command, (list, tuple))

    def run(self):
        self.mark_run()

        (exitcode, stdout) = run(self.command, self.use_shell)
        if exitcode == 0:
            state = 'ok'
            severity = 'expected'
        elif exitcode > 0:
            state = 'exit_%d' % (exitcode,)
            severity = 'error'
        else: # exitcode < 0
            signum = -exitcode
            if signum in _SIGNALS:
                state = _SIGNALS[signum]
            else:
                state = 'signal_%d' % (signum,)
            severity = 'error'

        return seismometer.message.Message(
            state = state, severity = severity,
            aspect = self.aspect,
            location = self.location,
        )

class Nagios(BaseCheck):
    '''
    Plugin to collect state and possibly metrics from a `Monitoring Plugin
    <https://www.monitoring-plugins.org/>`_.

    Metrics to be recognized need to be specified as described in section
    *Performance data* of `Monitoring Plugins Development Guidelines
    <https://www.monitoring-plugins.org/doc/guidelines.html>`_.
    '''
    _PERFDATA = re.compile(
        "(?P<label>[^ '=]+|'(?:[^']|'')*')="     \
        "(?P<value>[0-9.]+)"                     \
        "(?P<unit>[um]?s|%|[KMGT]?B|c)?"         \
            "(?:;(?P<warn>[0-9.]*)"              \
                "(?:;(?P<crit>[0-9.]*)"          \
                    "(?:;(?P<min>[0-9.]*)"       \
                        "(?:;(?P<max>[0-9.]*))?" \
                    ")?" \
                ")?" \
            ")?"
    )
    _EXIT_CODES = {
        0: ('ok', 'expected'),
        1: ('warning', 'warning'),
        2: ('critical', 'error'),
        #3: ('unknown', 'error'), # will be handled by codes.get()
    }
    def __init__(self, plugin, aspect, **kwargs):
        '''
        :param plugin: command to run (string for shell command, or list of
            strings for direct command to run)
        :param aspect: aspect name, as in :class:`seismometer.message.Message`
        '''
        super(Nagios, self).__init__(aspect = aspect, **kwargs)
        self.plugin = plugin
        self.use_shell = not isinstance(plugin, (list, tuple))

    def run(self):
        self.mark_run()

        (code, stdout) = run(self.plugin, self.use_shell)
        (state, severity) = Nagios._EXIT_CODES.get(code, ('unknown', 'error'))
        message = seismometer.message.Message(
            state = state, severity = severity,
            aspect = self.aspect,
            location = self.location,
        )

        status_line = stdout.split('\n')[0]
        if '|' not in status_line:
            # nothing more to parse
            return message

        metrics = []
        perfdata = status_line.split('|', 1)[1].strip()
        while perfdata != '':
            match = Nagios._PERFDATA.match(perfdata)
            if match is None: # non-plugins-conforming perfdata, abort
                return message
            metrics.append(match.groupdict())
            perfdata = perfdata[match.end():].lstrip()

        #---------------------------------------------------
        # helper functions {{{

        def number(string):
            if string == "":
                return None
            elif "." in string:
                return float(string)
            else:
                return int(string)

        def make_value(metric):
            # create a value
            value = seismometer.message.Value(number(metric['value']))
            if metric['warn'] != "":
                value.set_above(number(metric['warn']), "warning", "warning")
            if metric['crit'] != "":
                value.set_above(number(metric['crit']), "critical", "error")
            if metric['unit'] != "":
                value.unit = metric['unit']

            # extract value's name
            if metric['label'].startswith("'"):
                name = metric['label'][1:-1].replace("''", "'")
            else:
                name = metric['label']

            return (name, value)

        # }}}
        #---------------------------------------------------

        for metric in metrics:
            (name, value) = make_value(metric)
            message[name] = value
            # if any of the values has thresholds, state is expected to be
            # derivable from those thresholds
            if value.has_thresholds():
                del message.state # safe to do multiple times

        return message

# }}}
#-----------------------------------------------------------------------------
# Python function check {{{

class Function(BaseCheck):
    '''
    Plugin to collect a message to send by calling a Python function (or any
    callable).

    Function is expected to return a dict,
    :class:`seismometer.message.Message`, a list of these, or ``None``.
    '''
    def __init__(self, function, args = [], kwargs = {}, **_kwargs):
        '''
        :param interval: number of seconds between consequent checks
        :param function: function to run
        :param args: positional arguments to pass to the function call
        :param kwargs: keyword arguments to pass to the function call
        :param _kwargs: keyword arguments to pass to :class:`BaseCheck`
            constructor
        '''
        super(Function, self).__init__(**_kwargs)
        self.function = function
        self.args = args
        self.kwargs = kwargs

    def run(self):
        self.mark_run()
        result = self.function(*self.args, **self.kwargs)
        if len(self.location) == 0 and self.aspect is None:
            return result
        return [self._populate(m) for m in each(result)]

# }}}
#-----------------------------------------------------------------------------

def each(msglist):
    if isinstance(msglist, (dict, seismometer.message.Message)):
        yield msglist
    elif isinstance(msglist, (list, tuple)):
        for msg in msglist:
            yield msg
    else:
        raise ValueError("invalid message (%s)" % (type(msglist),))

def run(command, use_shell):
    # TODO: what to do with STDERR?
    proc = subprocess.Popen(
        command,
        stdin = open("/dev/null"),
        stdout = subprocess.PIPE,
        shell = use_shell,
    )
    (stdout, stderr) = proc.communicate()
    # returncode <  0 -- signal
    # returncode >= 0 -- exit code
    return (proc.returncode, stdout)

#-----------------------------------------------------------------------------

#-----------------------------------------------------------------------------
# vim:ft=python:foldmethod=marker
