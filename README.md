# zlog-sql
MySQL/SQLite logging plugin for ZNC IRC bouncer written in Python 3

## Features
* Supports both MySQL and SQLite databases.
* Asynchronous database writes on separate thread. Guarantees that ZNC won't hang during SQL connection timeout.
* Automatic table creation (`CREATE TABLE IF NOT EXIST`)
* Retry after failed inserts. When database server is offline, logs are buffered to memory. They are saved when database is back online, so you won't lose logs during MySQL outages. 

## Some statistics
After having this plugin enabled for around 2 months, below are my statistics of MySQL table:
* Total logs count: more than 810k
* Space usage: 66 MB (data 45 MB, index 21 MB)

MySQL gives great compression ratio and is easily searchable. SQLite database doesn't support compression, but they're easier to setup and migrate.

## Quick start
1. Copy `zlog_sql.py` to `~/.znc/modules/zlog_sql.py`.
2. In Webadmin, open the list of Global Modules.
3. Make sure `modpython` is enabled.
4. Enable module `zlog_sql` and set its argument.

![Screenshot](docs/webadmin_modules.png)

For MySQL, set module argument matching following format:
```
mysql://username:password@localhost/database_name
```
**Important:** you need `PyMySQL` pip package for MySQL logging. Install it with `pip3 install PyMySQL` command.


For SQLite use following string format:
```
sqlite:///home/user/logs.sqlite
```

or simply leave out the path
```
sqlite
```
in this case, logs are going to be written to the default path `~/.znc/moddata/zlog_sql/logs.sqlite`.

5. Save changes. SQL table schema is going to be created automatically.
