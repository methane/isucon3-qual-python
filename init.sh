#!/bin/sh
set -e
exec > /tmp/init.log
exec 2>&1

#データ投入後になにかしらの作業をしたい場合はこのシェルスクリプトに書いてください

id
mysql -uisucon isucon < /home/isu-user/isucon/init.sql
echo "flush_all" | nc 127.0.0.1 11211
curl http://localhost/__init__
/home/isu-user/isucon/webapp/python/reset.sh
