### HOW TO RUN ###

    $ sudo mkdir /dev/shm/nginx-cache
    $ sudo chmod 777 /dev/shm/nginx-cache
    $ pip install -r requirements.txt
    $ gunicorn -c gunicorn_config.py app:app

    $ redis-server redis.conf

