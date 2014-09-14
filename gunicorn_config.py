import os

errorlog = '-'
workers = 12
keepalive = 120
worker_class = "meinheld.gmeinheld.MeinheldWorker"
port = os.environ.get("PORT", '5000')
bind = '0.0.0.0:' + port


def post_fork(server, worker):
    # Disalbe access log
    import meinheld.server
    meinheld.server.set_access_logger(None)
