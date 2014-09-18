#!/bin/sh
sudo rm -rf /dev/shm/nginx-cache
sudo service nginx restart
cat /tmp/urls.txt | xargs -P4 curl -s > /dev/null
