import inspect
import json
import multiprocessing
import os
import pprint
import re
import traceback
import warnings
from datetime import datetime
from time import sleep

import znc
import sys

if not hasattr(sys, 'argv'):
    sys.argv  = ['']

TYPE_PRIVMSG = 1
TYPE_ACTION = 2
TYPE_NOTICE = 3
TYPE_MODE = 4
TYPE_JOIN = 5
TYPE_PART = 6
TYPE_QUIT = 7
TYPE_NICK = 8
TYPE_KICK = 9

# noinspection PyPep8Naming
class zlog_sql(znc.Module):
    description = 'Logs all channels to a MySQL/SQLite database.'
    module_types = [znc.CModInfo.GlobalModule]

    wiki_page = 'ZLog_SQL'

    has_args = True
    args_help_text = ('Connection string in format: mysql://user:pass@host/database_name'
                      ' or postgres://user:pass@host/database_name'
                      ' or sqlite://path/to/db.sqlite'
                      'use --extras for join/parts/quits')

    log_extras = False
    log_queue = multiprocessing.SimpleQueue()
    internal_log = None
    hook_debugging = False

    def OnLoad(self, args, message):
        """
        This module hook is called when a module is loaded.
        :type args: const CString &
        :type args: CString &
        :rtype: bool
        :param args: The arguments for the modules.
        :param message: A message that may be displayed to the user after loading the module.
        :return: True if the module loaded successfully, else False.
        """
        self.internal_log = InternalLog(self.GetSavePath())
        self.debug_hook()

        try:
            db = self.parse_args(args)
            multiprocessing.Process(target=DatabaseThread.worker_safe,
                                    args=(db, self.log_queue, self.internal_log)).start()
            return True
        except Exception as e:
            message.s = str(e)

            with self.internal_log.error() as target:
                target.write('Could not initialize module caused by: {} {}\n'.format(type(e), str(e)))
                target.write('Stack trace: ' + traceback.format_exc())
                target.write('\n')

            return False

    def __del__(self):
        # Terminate worker process.
        self.log_queue.put(None)

    def GetServer(self):
        pServer = self.GetNetwork().GetCurrentServer()

        if pServer is None:
            return '(no server)'

        sSSL = '+' if pServer.IsSSL() else ''
        return pServer.GetName() + ' ' + sSSL + pServer.GetPort()

    # GENERAL IRC EVENTS
    # ==================

    def OnIRCConnected(self):
        """
        This module hook is called after a successful login to IRC.
        :rtype: None
        """
        self.debug_hook()
        self.put_log('Connected to IRC (' + self.GetServer() + ')', 'znc', TYPE_PRIVMSG)

    def OnIRCDisconnected(self):
        """
        This module hook is called when a user gets disconnected from IRC.
        :rtype: None
        """
        self.debug_hook()
        self.put_log('Disconnected from IRC (' + self.GetServer() + ')', 'znc', TYPE_PRIVMSG)

    def OnBroadcast(self, message):
        """
        This module hook is called when a message is broadcasted to all users.
        :type message: CString &
        :rtype: EModRet
        """
        self.debug_hook()
        self.put_log('Broadcast: ' + str(message), 'znc', TYPE_PRIVMSG)
        return znc.CONTINUE

    def OnRawMode(self, opNick, channel, modes, args):
        if self.log_extras is not True:
            return
        """
        Called on any channel mode change.
        This is called before the more detailed mode hooks like e.g. OnOp() and OnMode().
        :type opNick: const CNick &
        :type channel: CChan &
        :type modes: const CString &
        :type args: const CString &
        :rtype: None
        """
        self.debug_hook()
        sNick = opNick.GetNick() if opNick is not None else 'Server'
        self.put_log(sNick + ' sets mode: ' + modes + ' ' + args, channel.GetName(), sNick, TYPE_MODE)

    def OnKick(self, opNick, kickedNick, channel, message):
        if self.log_extras is not True:
            return
        """
        Called when a nick is kicked from a channel.
        :type opNick: const CNick &
        :type kickedNick: const CString &
        :type channel: CChan &
        :type message: const CString &
        :rtype: None
        """
        self.debug_hook()
        self.put_log(kickedNick + ' was kicked by ' + opNick.GetNick() + ' (' + message + ')',
                     channel.GetName(), kickedNick, TYPE_KICK)

    def OnQuit(self, nick, message, channels):
        if self.log_extras is not True:
            return
        """
        Called when a nick quit from IRC.
        :type nick: const CNick &
        :type message: const CString &
        :type channels: std::vector<CChan*>
        :rtype: None
        """
        self.debug_hook()
        for channel in channels:
            self.put_log(
                nick.GetNick() + ' (' + nick.GetIdent() + '@' + nick.GetHost() + ') (' + message + ')',
                channel.GetName(), nick.GetNick(), TYPE_QUIT)

    def OnJoin(self, nick, channel):
        if self.log_extras is not True:
            return
        """
        Called when a nick joins a channel.
        :type nick: const CNick &
        :type channel: CChan &
        :rtype: None
        """
        self.debug_hook()
        self.put_log(nick.GetNick() + ' (' + nick.GetIdent() + '@' + nick.GetHost() + ')',
                     channel.GetName(), nick.GetNick(), TYPE_JOIN)

    def OnPart(self, nick, channel, message):
        if self.log_extras is not True:
            return
        """
        Called when a nick parts a channel.
        :type nick: const CNick &
        :type channel: CChan &
        :type message: const CString &
        :rtype: None
        """
        self.debug_hook()
        self.put_log(
            nick.GetNick() + ' (' + nick.GetIdent() + '@' + nick.GetHost() + ') (' + message + ')',
            channel.GetName(), nick.GetNick(), TYPE_PART)

    def OnNick(self, oldNick, newNick, channels):
        if self.log_extras is not True:
            return
        """
        Called when a nickname change occurs.
        :type oldNick: const CNick &
        :type newNick: const CString &
        :type channels: std::vector<CChan*>
        :rtype: None
        """
        self.debug_hook()
        for channel in channels:
            self.put_log(oldNick.GetNick() + ' is now known as ' + newNick, channel.GetName(), oldNick.GetNick(), TYPE_NICK)

    # NOTICES
    # =======

    def OnUserNotice(self, buffer, message):
        """
        This module hook is called when a user sends a NOTICE message.
        :type buffer: CString &
        :type message: CString &
        :rtype: EModRet
        """
        self.debug_hook()
        network = self.GetNetwork()
        if network:
            self.put_log(str(message), str(buffer), network.GetCurNick(), TYPE_NOTICE)

        return znc.CONTINUE

    def OnPrivNotice(self, nick, message):
        """
        Called when we receive a private NOTICE message from IRC.
        :type nick: CNick &
        :type message: CString &
        :rtype: EModRet
        """
        self.debug_hook()
        self.put_log(str(message), nick.GetNick(). nick.GetNick(), TYPE_NOTICE)
        return znc.CONTINUE

    def OnChanNotice(self, nick, channel, message):
        """
        Called when we receive a channel NOTICE message from IRC.
        :type nick: CNick &
        :type channel: CChan &
        :type message: CString &
        :rtype: EModRet
        """
        self.debug_hook()
        self.put_log(str(message), channel.GetName(), nick.GetNick(), TYPE_NOTICE)
        return znc.CONTINUE

    # ACTIONS
    # =======

    def OnUserAction(self, buffer, message):
        """
        Called when a client sends a CTCP ACTION request ("/me").
        :type buffer: CString &
        :type message: CString &
        :rtype: EModRet
        """
        self.debug_hook()
        pNetwork = self.GetNetwork()
        if pNetwork:
            self.put_log(str(message), str(buffer), pNetwork.GetCurNick(), TYPE_ACTION)

        return znc.CONTINUE

    def OnPrivAction(self, nick, message):
        """
        Called when we receive a private CTCP ACTION ("/me" in query) from IRC.
        :type nick: CNick &
        :type message: CString &
        :rtype: EModRet
        """
        self.debug_hook()
        self.put_log(str(message), nick.GetNick(). nick.GetNick(), TYPE_ACTION)
        return znc.CONTINUE

    def OnChanAction(self, nick, channel, message):
        """
        Called when we receive a channel CTCP ACTION ("/me" in a channel) from IRC.
        :type nick: CNick &
        :type channel: CChan &
        :type message: CString &
        :rtype: EModRet
        """
        self.debug_hook()
        self.put_log(str(message), channel.GetName(), nick.GetNick(), TYPE_ACTION)
        return znc.CONTINUE

    # MESSAGES
    # ========

    def OnUserMsg(self, buffer, message):
        """
        This module hook is called when a user sends a PRIVMSG message.
        :type buffer: CString &
        :type message: CString &
        :rtype: EModRet
        """
        self.debug_hook()
        network = self.GetNetwork()
        if network:
            self.put_log(str(message), str(buffer), network.GetCurNick(), TYPE_PRIVMSG)

        return znc.CONTINUE

    def OnPrivMsg(self, nick, message):
        """
        Called when we receive a private PRIVMSG message from IRC.
        :type nick: CNick &
        :type message: CString &
        :rtype: EModRet
        """
        self.debug_hook()
        self.put_log(str(message), nick.GetNick(), nick.GetNick(), TYPE_PRIVMSG)
        return znc.CONTINUE

    def OnChanMsg(self, nick, channel, message):
        """
        Called when we receive a channel PRIVMSG message from IRC.
        :type nick: CNick &
        :type channel: CChan &
        :type message: CString &
        :rtype: EModRet
        """
        self.debug_hook()
        self.put_log(str(message), channel.GetName(), nick.GetNick(), TYPE_PRIVMSG)
        return znc.CONTINUE

    # LOGGING
    # =======

#include <string>
    def put_log(self, line, buffer="Status", source="", type=TYPE_PRIVMSG):
        """
        Adds the log line to database write queue.
        """
        self.log_queue.put({
            'created_at': datetime.utcnow().isoformat(),
            'user': self.GetUser().GetUserName() if self.GetUser() is not None else None,
            'network': self.GetNetwork().GetName() if self.GetUser() is not None else None,
            'buffer': buffer,
            'message': line.encode('utf8', 'replace').decode('utf8'),
            'source': source,
            'type': type})

    # DEBUGGING HOOKS
    # ===============

    def debug_hook(self):
        """
        Dumps parent calling method name and its arguments to debug logfile.
        """

        if self.hook_debugging is not True:
            return

        frameinfo = inspect.stack()[1]
        argvals = frameinfo.frame.f_locals

        with self.internal_log.debug() as target:
            target.write('Called method: ' + frameinfo.function + '()\n')
            for argname in argvals:
                if argname == 'self':
                    continue
                target.write('    ' + argname + ' -> ' + pprint.pformat(argvals[argname]) + '\n')
            target.write('\n')

    # ARGUMENT PARSING
    # ================

    def parse_args(self, args):
        if args.strip() == '':
            raise Exception('Missing argument. Provide connection string as an argument.')
        
        match = re.search('(^|\\s)--extras(\\s|$)', args)
        if match:
            self.log_extras = True

        match = re.search('(^|\\s)sqlite(?:://(.+))?(\\s|$)', args)
        if match:
            if match.group(2) is None:
                return SQLiteDatabase({'database': os.path.join(self.GetSavePath(), 'logs.sqlite')})
            else:
                return SQLiteDatabase({'database': match.group(2)})

        match = re.search('(^|\\s)mysql://(.+?):(.+?)@(.+?)/(.+?)(\\s|$)', args)
        if match:
            return MySQLDatabase({'host': match.group(4),
                                  'user': match.group(2),
                                  'passwd': match.group(3),
                                  'db': match.group(5)})

        match = re.search('(^|\\s)postgres://(.+?):(.+?)@(.+?)/(.+)(\\s|$)', args)
        if match:
            return PostgresDatabase({'host': match.group(4),
                                     'user': match.group(2),
                                     'password': match.group(3),
                                     'database': match.group(5)})

        raise Exception('Unrecognized connection string. Check the documentation.')


class DatabaseThread:
    @staticmethod
    def worker_safe(db, log_queue: multiprocessing.SimpleQueue, internal_log) -> None:
        try:
            DatabaseThread.worker(db, log_queue, internal_log)
        except Exception as e:
            with internal_log.error() as target:
                target.write('Unrecoverable exception in worker thread: {0} {1}\n'.format(type(e), str(e)))
                target.write('Stack trace: ' + traceback.format_exc())
                target.write('\n')
            raise

    @staticmethod
    def worker(db, log_queue: multiprocessing.SimpleQueue, internal_log) -> None:
        db.connect()

        while True:
            item = log_queue.get()
            if item is None:
                break

            try:
                db.ensure_connected()
                db.insert_into('logs', item)
            except Exception as e:
                sleep_for = 10

                with internal_log.error() as target:
                    target.write('Could not save to database caused by: {0} {1}\n'.format(type(e), str(e)))
                    if 'open' in dir(db.conn):
                        target.write('Database handle state: {}\n'.format(db.conn.open))
                        target.write('Stack trace: ' + traceback.format_exc())
                        target.write('Current log: ')
                        json.dump(item, target)
                        target.write('\n\n')
                        target.write('Retry in {} s\n'.format(sleep_for))

                sleep(sleep_for)

                with internal_log.error() as target:
                    target.write('Retrying now.\n')
                    log_queue.put(item)


class InternalLog:
    def __init__(self, save_path: str):
        self.save_path = save_path

    def debug(self):
        return self.open('debug')

    def error(self):
        return self.open('error')

    def open(self, level: str):
        target = open(os.path.join(self.save_path, level + '.log'), 'a')
        line = 'Log opened at: {} UTC\n'.format(datetime.utcnow())
        target.write(line)
        target.write('=' * len(line) + '\n\n')
        return target


class Database:
    def __init__(self, dsn: dict):
        self.dsn = dsn
        self.conn = None

class PostgresDatabase(Database):
    def connect(self) -> None:
        import psycopg2
        self.conn = psycopg2.connect(**self.dsn)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            self.conn.cursor().execute('''
CREATE TABLE IF NOT EXISTS logs (
  "id" BIGSERIAL NOT NULL,
  "created_at" TIMESTAMP WITH TIME ZONE NOT NULL,
  "user" VARCHAR(128) DEFAULT NULL,
  "network" VARCHAR(128) DEFAULT NULL,
  "buffer" VARCHAR(255) NOT NULL,
  "message" TEXT,
  "source" VARCHAR(128) DEFAULT NULL,
  "type" INT(1) DEFAULT NULL,
  PRIMARY KEY (id)
);
''')
        self.conn.commit()
    def ensure_connected(self):
        if self.conn.status == 0:
            self.connect()
    def insert_into(self, table, row):
        cols = ', '.join('"{}"'.format(col) for col in row.keys())
        vals = ', '.join('%({})s'.format(col) for col in row.keys())
        sql = 'INSERT INTO {} ({}) VALUES ({})'.format(table, cols, vals)
        self.conn.cursor().execute(sql, row)
        self.conn.commit()

class MySQLDatabase(Database):
    def connect(self) -> None:
        import pymysql
        self.conn = pymysql.connect(use_unicode=True, charset='utf8mb4', **self.dsn)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            self.conn.cursor().execute('''
CREATE TABLE IF NOT EXISTS `logs` (
  `id` INT(11) NOT NULL AUTO_INCREMENT,
  `created_at` DATETIME NOT NULL,
  `user` VARCHAR(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `network` VARCHAR(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `buffer` VARCHAR(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `message` TEXT COLLATE utf8mb4_unicode_ci,
  `source` VARCHAR(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `type` INT(1) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `created_at` (`created_at`),
  KEY `user` (`user`),
  KEY `network` (`network`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=COMPRESSED;
''')
        self.conn.commit()

    def ensure_connected(self):
        if self.conn.open is False:
            self.connect()

    def insert_into(self, table, row):
        cols = ', '.join('`{}`'.format(col) for col in row.keys())
        vals = ', '.join('%({})s'.format(col) for col in row.keys())
        sql = 'INSERT INTO `{}` ({}) VALUES ({})'.format(table, cols, vals)
        self.conn.cursor().execute(sql, row)
        self.conn.commit()


class SQLiteDatabase(Database):
    def connect(self) -> None:
        import sqlite3
        self.conn = sqlite3.connect(**self.dsn)
        self.conn.cursor().execute('''
CREATE TABLE IF NOT EXISTS [logs](
    [id] INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, 
    [created_at] DATETIME NOT NULL, 
    [user] VARCHAR, 
    [network] VARCHAR, 
    [buffer] VARCHAR, 
    [message] TEXT,
    [source] VARCHAR,
    [type] INTEGER);
''')
        self.conn.commit()

    def ensure_connected(self):
        pass

    def insert_into(self, table: str, row: dict) -> None:
        cols = ', '.join('[{}]'.format(col) for col in row.keys())
        vals = ', '.join(':{}'.format(col) for col in row.keys())
        sql = 'INSERT INTO [{}] ({}) VALUES ({})'.format(table, cols, vals)
        self.conn.cursor().execute(sql, row)
        self.conn.commit()
