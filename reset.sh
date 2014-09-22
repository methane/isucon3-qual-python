#!/bin/sh
sudo rm -rf /var/log/nginx/*
sudo rm -rf /dev/shm/nginx-cache
sudo service nginx restart
time cat /tmp/memos.txt | xargs -P2 curl -s > /dev/null
