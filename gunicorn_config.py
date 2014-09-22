import os

#accesslog = '-'
errorlog = '-'
keepalive = 60
worker_class = "meinheld.gmeinheld.MeinheldWorker"
port = os.environ.get("PORT", '5000')
#bind = '0.0.0.0:' + port
#bind = 'unix:/tmp/gunicorn.sock'
enable_stdio_inheritance = True


#def post_fork(server, worker):
#    # Disalbe access log
#    import meinheld.server
#    meinheld.server.set_access_logger(None)
