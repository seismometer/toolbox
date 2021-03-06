*********
DumbProbe
*********

Synopsis
========

.. code-block:: none

   dumb-probe [options] --checks=<checks-file>

Description
===========

DumbProbe is a simple tool that checks whether all the services defined in its
config are healthy and submits the results of the checks to monitoring system.

The checks file is a Python module that defines what, how, and how often
should be checked. Results are packed into a Seismometer message and sent to
a :manpage:`messenger(8)` (or a compatible router).

Options
=======

.. program:: dumb-probe

.. cmdoption:: --checks <checks-file>

   Python module that defines checks. See :ref:`dumbprobe-checks-file`.

.. cmdoption:: --once

   Go through the checks immediately and just once and exit, instead of usual
   infinite loop with a schedule.

   This mode of operation is only supported if :obj:`CHECKS` in checks file is
   a list or tuple, and it ignores any
   :class:`seismometer.dumbprobe.BaseHandle` checks that were defined.

.. cmdoption:: --destination stdout | tcp:<host>:<port> | udp:<host>:<port> | unix:<path>

   Address to send check results to.

   If unix socket is specified, it's datagram type, like
   :manpage:`messenger(8)` uses.

   If no destination was provided, messages are printed to STDOUT.

.. cmdoption:: --logging <config>

   logging configuration, in JSON or YAML format (see :ref:`dumbprobe-logging`
   for details); default is to log warnings to *STDERR*

.. _dumbprobe-checks-file:

Configuration
=============

Configuration file is a Python module. The only thing expected from the module
is defining :obj:`CHECKS` object, which usually will be a list of check
objects (typically a :class:`seismometer.dumbprobe.BaseCheck` subclass
instances). DumbProbe will take care of scheduling runs of each of the checks
according to their specified intervals.

If there is a need for any other scheduling logic, :obj:`CHECKS` can be an
arbitrary Python object that has :meth:`run_next()` method, which is
responsible for waiting for next check and running it. This method will be
called with no arguments and should return a sequence (e.g. list) of messages
that are either :class:`seismometer.message.Message` objects or dictionaries
(serializable to JSON). These messages will be sent to DumbProbe's
destination.

Supported check types
---------------------

The simplest case of a check is a Python function that produces a dictionary,
:class:`seismometer.message.Message` object, or list of these. Such function
is wrapped in :class:`seismometer.dumbprobe.Function` object in :obj:`CHECKS`
list.

There are also several built-in classes that facilitate working with external
commands and scripts:

* :class:`seismometer.dumbprobe.ShellCommand` -- a command whose *STDOUT*
  output and exit code are passed to a Python function, which in turn produces
  above-mentioned messages
* :class:`seismometer.dumbprobe.Nagios` -- command that conforms to
  `Monitoring Plugins <https://www.monitoring-plugins.org/>`_ protocol,
  including performance data for collecting metrics
* :class:`seismometer.dumbprobe.ShellStream` -- command that writes statistics
  to *STDOUT*, line by line, in a continuous fashion (like
  :manpage:`vmstat(8)` and :manpage:`iostat(1)` do); such lines are parsed by
  a Python function (default: :func:`json.loads()`) to produce useful
  monitoring data

Typically, checks file will look somewhat like this:

.. code-block:: python

   from seismometer.dumbprobe import *
   from seismometer.message import Message, Value
   import os
   import json

   #--------------------------------------------------------------------

   def hostname():
       return os.uname()[1]

   #--------------------------------------------------------------------

   def uptime():
       with open("/proc/uptime") as f:
           return Message(
               aspect = "uptime",
               location = {"host": hostname()},
               value = float(f.read().split()[0]),
           )

   def df(mountpoint):
       stat = os.statvfs(mountpoint)
       result = Message(
           aspect = "disk space",
           location = {
               "host": hostname(),
               "filesystem": mountpoint,
           },
       )
       result["free"] = Value(
           stat.f_bfree  * stat.f_bsize / 1024.0 / 1024.0,
           unit = "MB",
       )
       result["total"] = Value(
           stat.f_blocks * stat.f_bsize / 1024.0 / 1024.0,
           unit = "MB",
       )
       return result

   def parse_iostat(line):
       if not line.startswith("sd") and not line.startswith("dm-"):
           return ()
       (device, tps, rspeed, wspeed, rbytes, wbytes) = line.split()
       result = Message(
           aspect = "disk I/O",
           location = {
               "host": hostname(),
               "device": device,
           },
       )
       result["read_speed"] = Value(float(rspeed), unit = "kB/s")
       result["write_speed"] = Value(float(wspeed), unit = "kB/s")
       result["transactions"] = Value(float(tps), unit = "tps")
       return result

   #--------------------------------------------------------------------

   CHECKS = [
       # function called every 60s with empty arguments list
       Function(uptime, interval = 60),
       # function called every 30 minutes with a single argument
       Function(df, args = ["/"],     interval = 30 * 60),
       Function(df, args = ["/home"], interval = 30 * 60),
       Function(df, args = ["/tmp"],  interval = 30 * 60),
       # shell command (`sh -c ...'), prints list of JSON objects to
       # STDOUT
       ShellCommand(
           "/usr/local/bin/read-etc-passwd",
           parse = lambda stdout,code: [
               json.loads(l) for l in stdout.strip().split("\n")
           ],
           interval = 60
       ),
       # external command (run without `sh -c'), prints single number
       ShellCommand(
           ["/usr/local/bin/random", "0.5"],
           parse = lambda stdout,code: Message(
             aspect = "random",
             value = float(stdout),
           ),
           interval = 30,
           host = hostname(),
       ),
       # and two Monitoring Plugins
       Nagios(
           # this one runs without shell
           ["/usr/lib/nagios/plugins/check_load", "-w", "0.25", "-c", "0.5"],
           interval = 10,
           aspect = "load average",
           host = hostname(), service = "load",
       ),
       Nagios(
           # this one runs with shell
           "/usr/lib/nagios/plugins/check_users -w 3 -c 5",
           interval = 60,
           aspect = "wtmp",
           host = hostname(), service = "users",
       ),
       # spawn iostat(1), make it print statistics every 20s, and make
       # them proper Seismometer messages
       ShellStream(["/usr/bin/iostat", "-p", "20"], parse = parse_iostat),
   ]

.. _dumbprobe-logging:

Logging configuration
=====================

.. include:: logging.rst.common

Programming interface
=====================

**NOTE**: User doesn't need to use these classes/functions if they happen not
to suit the needs. They are merely a proposal, but the author thinks they
should at least help somewhat in deployment.

.. automodule:: seismometer.dumbprobe

See Also
========

.. only:: man

   * message schema v3 <http://seismometer.net/message-schema/v3/>
   * :manpage:`seismometer-message(7)`
   * :manpage:`daemonshepherd(8)`
   * :manpage:`messenger(8)`
   * :manpage:`hailerter(8)`
   * Monitoring Plugins <https://www.monitoring-plugins.org/>

.. only:: html

   * message schema v3 <http://seismometer.net/message-schema/v3/>
   * :doc:`../api/message`
   * :doc:`daemonshepherd`
   * :doc:`messenger`
   * :doc:`hailerter`
   * Monitoring Plugins <https://www.monitoring-plugins.org/>

