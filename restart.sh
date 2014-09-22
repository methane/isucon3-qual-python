#!/bin/sh
sudo rm -rf /dev/shm/nginx-cache/*
sudo rm /var/log/nginx/access.log
sudo service nginx restart
sudo supervisorctl restart isucon_python
sudo supervisorctl restart isucon_python2
curl http://localhost/__init__
