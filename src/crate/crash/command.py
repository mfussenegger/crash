# -*- coding: utf-8; -*-
#
# Licensed to CRATE Technology GmbH ("Crate") under one or more contributor
# license agreements.  See the NOTICE file distributed with this work for
# additional information regarding copyright ownership.  Crate licenses
# this file to you under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.  You may
# obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
# License for the specific language governing permissions and limitations
# under the License.
#
# However, if you have executed another commercial license agreement
# with Crate these terms will supersede the license and you may use the
# software solely pursuant to the terms of the relevant commercial agreement.
"""
crate cli

can be used to query crate using SQL
"""
from __future__ import print_function
import inspect
import json
import logging
import os
import sys
import select

_has_readline = False
try:
    import readline
    _has_readline = True
except ImportError:
    _has_readline = False

import atexit
from appdirs import user_data_dir

from cmd import Cmd
from argparse import ArgumentParser

from crate import client
from crate.client.exceptions import ConnectionError, Error, Warning
from crate.client.compat import raw_input
from .tabulate import TableFormat, Line, DataRow, tabulate


MAX_HISTORY_LENGTH = 10000


crate_fmt = TableFormat(lineabove=Line("+", "-", "+", "+"),
                        linebelowheader=Line("+", "-", "+", "+"),
                        linebetweenrows=None,
                        linebelow=Line("+", "-", "+", "+"),
                        headerrow=DataRow("|", "|", "|"),
                        datarow=DataRow("|", "|", "|"),
                        padding=1, with_header_hide=None)


class CrateCmd(Cmd):
    prompt = 'cr> '
    line_delimiter = ';'
    multi_line_prompt = '... '
    NULL = u'NULL'
    TRUE = u'TRUE'
    FALSE = u'FALSE'

    keywords = [
        "table", "index", "from", "into", "where", "values", "and", "or",
        "set", "with", "by", "using", "like",
        "boolean", "integer", "string", "float", "double", "short", "long",
        "byte", "timestamp", "ip", "object", "dynamic", "strict", "ignored",
        "array", "blob", "primary key",
        "analyzer", "extends", "tokenizer", "char_filters", "token_filters",
        "number_of_replicas", "clustered",
        "refresh", "alter",
    ]

    def __init__(self, stdin=None, stdout=None, error_trace=False):
        Cmd.__init__(self, "tab", stdin, stdout)
        self.exit_code = 0
        self.partial_lines = []
        self.error_trace = error_trace

    def do_connect(self, server, error_trace=False):
        """
        connect to one or more server
        with "connect servername:port[ servername:port [...]]"
        """
        self.conn = client.connect(servers=server, error_trace=self.error_trace)
        self.cursor = self.conn.cursor()
        results = []
        failed = 0
        for server in self.conn.client.active_servers:
            try:
                server_infos = self.conn.client.server_infos(server)
            except ConnectionError as e:
                failed += 1
                results.append([server, None, False, e.message])
            else:
                results.append(
                    server_infos + (True, "OK", )
                )
        self.pprint(
            results,
            ["server_url", "node_name", "connected", "message"])
        if failed == len(results):
            self.print_error("connect")
        else:
            self.print_success("connect")

    def execute_query(self, statement):
        if self.execute(statement):
            self.pprint(self.cursor.fetchall())
            self.print_rows_selected()

    def execute(self, statement):
        try:
            self.cursor.execute(statement)
            return True
        except ConnectionError:
            self.exit_code = 1
            print(
                'Use "connect <hostname:port>" to connect to a server first')
        except (Error, Warning) as e:
            self.exit_code = 1
            if hasattr(e, 'message'):
                print(e.message)
            else:
                print(e)
            if self.error_trace and hasattr(e, "error_trace") and e.error_trace:
                print(e.error_trace)

        return False

    def pprint(self, rows, cols=None):
        if cols is None:
            cols = self.cols()
        rows = [list(map(self._transform_field, row)) for row in rows]
        out = tabulate(rows, headers=cols, tablefmt=crate_fmt, floatfmt="",
                       missingval=self.NULL)
        try:
            print(out)
        except UnicodeEncodeError:
            try:
                print(out.encode('utf-8').decode('ascii', 'replace'))
            except UnicodeEncodeError:
                print(out.encode('utf-8').decode('ascii', 'ignore'))
            print('WARNING: Unicode characters found that cannot be displayed. Check your system locale.')

    def _transform_field(self, field):
        """transform field for displaying"""
        if isinstance(field, bool):
            return self.TRUE if field else self.FALSE
        elif isinstance(field, (list, dict)):
            return json.dumps(field, sort_keys=True, ensure_ascii=False)
        else:
            return field

    def cols(self):
        return [c[0] for c in self.cursor.description]

    def do_select(self, statement):
        """execute a SQL select statement

        E.g.:
            "select name from locations where name = 'Algol'"
        """
        self.execute_query('select ' + statement)

    def do_insert(self, statement):
        """execute a SQL insert statement

        E.g.:
            "insert into locations (name) values ('Algol')"
        """
        if self.execute('insert ' + statement):
            self.print_rows_affected("insert")

    def do_delete(self, statement):
        """execute a SQL delete statement

        E.g.:
            "delete from locations where name = 'Algol'"
        """
        if self.execute('delete ' + statement):
            self.print_rows_affected("delete")

    def do_update(self, statement):
        """execute a SQL update statement

        E.g.:
            "update from locations set name = 'newName' where name = 'Algol'"
        """
        if self.execute('update ' + statement):
            self.print_rows_affected("update")

    def do_alter(self, statement):
        """execute a SQL ALTER statement

        E.g.:
            "alter table locations set (number_of_replicas=2)"
        """
        if self.execute('alter ' + statement):
            self.print_success("alter")

    def do_create(self, statement):
        """execute a SQL create statement

        E.g.:
            "create table locations (id integer, name string)"
        """
        if self.execute('create ' + statement):
            self.print_success("create")

    def do_crate(self, statement):
        """alias for ``do_create``"""
        self.do_create(statement)

    def do_drop(self, statement):
        """execute a SQL drop statement

        E.g.:
            "drop table locations"
        """
        if self.execute('drop ' + statement):
            self.print_success("drop")

    def do_copy(self, statement):
        """execute a SQL copy statement

        E.g.:
            "copy locations from 'path/to/import/data.json'"
        """
        if self.execute('copy ' + statement):
            self.print_rows_affected("copy")

    def do_refresh(self, statement):
        """execute a SQL refresh statement

        E.g.:
            "refresh table locations"
        """
        if self.execute('refresh ' + statement):
            self.print_success("refresh")

    def do_set(self, statement):
        """execute a SQL set statement

        E.g.:
            "set global persistent collect_stats=true"
        """
        if self.execute('set ' + statement):
            self.print_success("set")

    def do_reset(self, statement):
        """execute a SQL reset statement

        E.g.:
            "reset global persistent collect_stats"
        """
        if self.execute('reset ' + statement):
            self.print_success("reset")

    def do_exit(self, *args):
        """exit the shell"""
        self.stdout.write("Bye\n")
        sys.exit(0)

    def do_quit(self, *args):
        """exit the shell"""
        self.stdout.write("Bye\n")
        sys.exit(0)

    do_EOF = do_exit

    def print_rows_affected(self, command):
        """print success status with rows affected and query duration"""
        rowcount = self.cursor.rowcount
        if self.cursor.duration > -1:
            print("{0} OK, {1} row{2} affected ({3:.3f} sec)".format(
                command.upper(), rowcount, "s"[rowcount == 1:],
                float(self.cursor.duration) / 1000))
        else:
            print("{0} OK, {1} row{2} affected".format(
                command.upper(), rowcount, "s"[rowcount == 1:]))

    def print_rows_selected(self):
        """print count of rows in result set and query duration"""
        rowcount = self.cursor.rowcount
        if self.cursor.duration > -1:
            print("SELECT {0} row{1} in set ({2:.3f} sec)".format(
                rowcount, "s"[rowcount == 1:],
                float(self.cursor.duration) / 1000))
        else:
            print("SELECT {0} row{1} in set".format(
                rowcount, "s"[rowcount == 1:]))

    def print_success(self, command):
        """print success status only and duration"""
        if self.cursor.duration > -1:
            print("{0} OK ({1:.3f} sec)".format(
                command.upper(), float(self.cursor.duration) / 1000))
        else:
            print("{0} OK".format(command.upper()))

    def print_error(self, command, exception=None):
        if exception is not None:
            print("{0}: {1}".format(
                exception.__class__.__name__, exception.message))
        if self.cursor.duration > -1:
            print("{0} ERROR ({1:.3f} sec)".format(
                command.upper(), float(self.cursor.duration) / 1000))
        else:
            print("{0} ERROR".format(command.upper()))

    def cmdloop(self, intro=None):
        """Repeatedly issue a prompt, accept input, parse an initial prefix
        off the received input, and dispatch to action methods, passing them
        the remainder of the line as argument.

        """

        self.preloop()
        if self.use_rawinput and self.completekey:
            if _has_readline:
                try:
                    self.old_completer = readline.get_completer()
                    readline.set_completer(self.complete)
                    if getattr(readline, "__doc__") is not None \
                            and 'libedit' in readline.__doc__:
                        readline.parse_and_bind("bind ^I rl_complete")
                    else:
                        readline.parse_and_bind("tab: complete")
                except ImportError:
                    pass
        try:
            if intro is not None:
                self.intro = intro
            if self.intro:
                self.stdout.write(str(self.intro) + "\n")
            stop = None
            prompt = self.prompt
            while not stop:
                if self.cmdqueue:
                    line = self.cmdqueue.pop(0)
                else:
                    if self.use_rawinput:
                        try:
                            line = raw_input(prompt)
                        except EOFError:
                            line = 'EOF'
                    else:
                        self.stdout.write(prompt)
                        self.stdout.flush()
                        line = self.stdin.readline()
                        if not len(line):
                            line = 'EOF'
                        else:
                            line = line.rstrip('\r\n')
                    if (line or self.partial_lines) and line != 'EOF':
                        if line[-1:] != self.line_delimiter:
                            self.partial_lines.append(line)
                            prompt = self.multi_line_prompt
                        else:
                            self.partial_lines.append(
                                line.rstrip(self.line_delimiter))
                            line = " ".join(self.partial_lines)
                            self.partial_lines = []
                            prompt = self.prompt
                if not self.partial_lines or line == 'EOF':
                    line = self.precmd(line)
                    stop = self.onecmd(line)
                    stop = self.postcmd(stop, line)
            self.postloop()
        finally:
            if self.use_rawinput and self.completekey and _has_readline:
                readline.set_completer(self.old_completer)

    def default(self, line):
        if line.lstrip().startswith('--'):
            return  # ignore comments
        Cmd.default(self, line)

    def completedefault(self, text, line, begidx, endidx):
        """Method called to complete an input line when no command-specific
        complete_*() method is available.

        """
        mline = line.split(' ')[-1]
        offs = len(mline) - len(text)
        return [s[offs:] for s in self.keywords if s.startswith(mline)]

    def emptyline(self):
        """Called when an empty line is entered in response to the prompt.
        """
        pass

# uppercase commands
for name, attr in inspect.getmembers(
        CrateCmd,
        lambda attr: inspect.ismethod(attr) or inspect.isfunction(attr)):
    if name.startswith("do_"):
        cmd_name = name.split("do_")[-1]
        setattr(CrateCmd, "do_{0}".format(cmd_name.upper()), attr)


USER_DATA_DIR = user_data_dir("Crate", "Crate")
HISTORY_FILE_NAME = 'crash_history'
HISTORY_PATH = os.path.join(USER_DATA_DIR, HISTORY_FILE_NAME)


def parse_args():
    parser = ArgumentParser(description='crate shell')
    parser.add_argument('-v', '--verbose', action='count',
                        dest='verbose', default=0,
                        help='use -v to get debug output')
    parser.add_argument('--history',
                        type=str,
                        help='the history file to use', default=HISTORY_PATH)
    parser.add_argument('-c', '--command', type=str,
                        help='execute sql statement')
    parser.add_argument('--hosts', type=str, nargs='*',
                        help='the crate hosts to connect to', metavar='HOST')
    args = parser.parse_args()
    return args


def get_stdin():
    """Get data from stdin, if any
    """
    # use select.select to check if input is available
    # otherwise sys.stdin would block
    partial_lines = []
    delim = CrateCmd.line_delimiter
    while sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
        line = sys.stdin.readline()
        if line:
            line = line.strip()
            if line.startswith('--'):
                continue
            if line.endswith(delim):
                line = line.rstrip(delim)
                if partial_lines:
                    yield ' '.join(partial_lines + [line])
                    partial_lines = []
                else:
                    yield line
            else:
                partial_lines.append(line)
        else:
            if partial_lines:
                yield ' '.join(partial_lines + [line])
            break
    return


def main():
    args = parse_args()
    # optionally read and write history file
    if _has_readline:
        history_file_path = args.history
        readline.set_history_length(MAX_HISTORY_LENGTH)
        if not os.path.exists(USER_DATA_DIR):
            os.makedirs(USER_DATA_DIR)
        try:
            readline.read_history_file(history_file_path)
        except IOError:
            pass
        atexit.register(readline.write_history_file, history_file_path)

    level = logging.ERROR
    error_trace = args.verbose > 0
    if args.verbose == 1:
        level = logging.INFO
    elif args.verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="[%(asctime)s %(name)-15s %(levelname)-8s] %(message)s]",
        stream=sys.stdout
    )

    cmd = CrateCmd(error_trace=error_trace)
    cmd.do_connect(args.hosts)
    # select.select on sys.stdin doesn't work on windows
    # so currently there is no pipe support
    done = False
    if os.name == 'posix':
        stdin_data = get_stdin()
    if args.command:
        for single_cmd in args.command.split(CrateCmd.line_delimiter):
            cmd.onecmd(single_cmd)
        done = True
    elif stdin_data:
        for single_cmd in stdin_data:
            cmd.onecmd(single_cmd)
            done = True
    if not done:
        try:
            cmd.cmdloop()
        except KeyboardInterrupt:
            print("interrupted exiting...")
    else:
        sys.exit(cmd.exit_code)

if __name__ == '__main__':
    main()
