from __future__ import with_statement

import MySQLdb
from MySQLdb.cursors import DictCursor

import flask
from flask import (
    Flask, request, redirect, session, url_for, abort,
    render_template, _app_ctx_stack, Response,
    after_this_request,
)

import meinheld.server
import memcache
from redis import StrictRedis
from flask_memcache_session import Session
#from werkzeug.contrib.fixers import ProxyFix

import json, os, hashlib, tempfile, subprocess, time
import misaka

redis = StrictRedis()
markdown = misaka.Markdown(misaka.HtmlRenderer())

config = {}

app = Flask(__name__, static_url_path='')
app.debug = False
app.cache = memcache.Client(['localhost:11211'], debug=0)
app.session_interface = Session()
app.session_cookie_name = "isucon_session_python"
#app.wsgi_app = ProxyFix(app.wsgi_app)

with open('templates/frame_nouser.html') as f:
    FRAME_A, FRAME_B = f.read().split('{{ content }}')

def load_config():
    global config
    print("Loading configuration")
    env = os.environ.get('ISUCON_ENV') or 'local'
    with open('../config/' + env + '.json') as fp:
        config = json.load(fp)

def connect_db():
    global config
    host = config['database']['host']
    port = config['database']['port']
    username = config['database']['username']
    password = config['database']['password']
    dbname   = config['database']['dbname']
    db = MySQLdb.connect(host=host,
            port=port,
            db=dbname,
            user=username,
            passwd=password,
            cursorclass=DictCursor,
            charset="utf8",
            autocommit=True)
    return db


def get_user():
    user_id = session.get('user_id')
    user = None
    if user_id:
        user = get_user_by_id(user_id)
    if user:
        @after_this_request
        def add_header(response):
            response.headers['Cache-Control'] = 'private'
            return response
    return user


_userid_cache = {}

def get_user_by_id(user_id):
    user = _userid_cache.get(user_id)
    if user:
        return user
    cur  = get_db().cursor()
    cur.execute('SELECT * FROM users WHERE id=%s', (user_id,))
    user = cur.fetchone()
    cur.close()
    _userid_cache[user_id] = user
    return user


def anti_csrf():
    if request.form['sid'] != session['token']:
        abort(400)


def require_user(user):
    if not user:
        redirect(url_for("top_page"))
        abort(403)


def gen_markdown(memo_id, md):
    key = "memo:%s" % (memo_id,)
    html = app.cache.get(key)
    if html:
        return html
    html = markdown.render(md)
    app.cache.set(key, html)
    return html


_db = None
def get_db():
    global _db
    if _db is None:
        _db = connect_db()
    return _db


def get_memos(page):
    cache_key = 'page:%d' % (page,)
    content = app.cache.get(cache_key)
    if content:
        return content

    total = redis.llen('memos')
    start = (page+1)*(-100)
    end = (page)*(-100)-1
    memos = redis.lrange('memos', start, end)

    if not memos:
        abort(404)

    memos.reverse()
    memos = flask.Markup(b'\n'.join(memos).decode('utf-8'))
    content = flask.Markup(render_template(
        'index.html',
        total=total,
        memos=memos,
        page=page,
    ))
    app.cache.set(cache_key, content, time=1)
    return content


@app.route("/")
def top_page():
    user = get_user()
    content = get_memos(0)
    if not user:
        return FRAME_A + content + FRAME_B
    return render_template('frame.html', user=user, content=content)

@app.route("/recent/<int:page>")
def recent(page):
    user = get_user()
    content = get_memos(page)
    if not user:
        return FRAME_A + content + FRAME_B
    return render_template('frame.html', user=user, content=content)


@app.route("/mypage")
def mypage():
    user  = get_user()
    require_user(user)

    cur = get_db().cursor()
    cur.execute('SELECT id, content, is_private, created_at, updated_at FROM memos WHERE user=%s ORDER BY created_at DESC', (user["id"],))
    memos = cur.fetchall()
    cur.close()

    return render_template(
        'mypage.html',
        user=user,
        memos=memos,
    )

@app.route("/signin", methods=['GET','HEAD'])
def signin():
    user = get_user()
    return render_template('signin.html', user=user)


@app.route("/signin", methods=['POST'])
def signin_post():
    db  = get_db()
    cur = db.cursor()
    username = request.form['username']
    password = request.form['password']
    cur.execute('SELECT id, username, password, salt FROM users WHERE username=%s', (username,))
    user = cur.fetchone()
    if user and user["password"] == hashlib.sha256(bytes(user["salt"] + password, 'UTF-8')).hexdigest():
        session["user_id"] = user["id"]
        session["token"] = hashlib.sha256(os.urandom(40)).hexdigest()
        return redirect(url_for("mypage"))
    else:
        return render_template('signin.html', user=None)


@app.route("/signout", methods=['POST'])
def signout():
    anti_csrf()
    session.clear()

    @after_this_request
    def remove_cookie(response):
        response.set_cookie(app.session_cookie_name, "", expires=0)
        return response

    return redirect(url_for("top_page"))

@app.route("/memo/<int:memo_id>")
def memo(memo_id):
    user = get_user()

    cur  = get_db().cursor()
    cur.execute('SELECT id, user, content, is_private, created_at, updated_at FROM memos WHERE id=%s', (memo_id,))
    memo = cur.fetchone()
    if not memo:
        abort(404)

    if memo["is_private"] == 1:
        if not user or user["id"] != memo["user"]:
            abort(404)

    memo["username"] = get_user_by_id(memo['user'])["username"]
    memo["content_html"] = gen_markdown(memo['id'], memo["content"])
    if user and user["id"] == memo["user"]:
        cond = ""
    else:
        cond = "AND is_private=0"
    memos = []
    older = None
    newer = None
    cur.execute("SELECT id FROM memos WHERE user=%s " + cond + " AND id<%s ORDER BY id DESC LIMIT 1", (memo["user"], memo['id']))
    older_memos = cur.fetchall()
    cur.execute("SELECT id FROM memos WHERE user=%s " + cond + " AND id>%s ORDER BY id LIMIT 1", (memo["user"], memo['id']))
    newer_memos = cur.fetchall()
    cur.close()

    if older_memos:
        older = older_memos[0]
    if newer_memos:
        newer = newer_memos[0]

    return render_template(
        "memo.html",
        user=user,
        memo=memo,
        older=older,
        newer=newer,
    )

@app.route("/memo", methods=['POST'])
def memo_post():
    user = get_user()
    require_user(user)
    anti_csrf()
    private = int(request.form.get("is_private") or 0)
    content = request.form["content"]
    created_at=time.strftime('%Y-%m-%d %H:%M:%S')

    db  = get_db()
    cur = db.cursor()
    cur.execute(
        "INSERT INTO memos (user, content, is_private, created_at) VALUES (%s, %s, %s, %s)",
        (user["id"], content, private, created_at)
    )
    memo_id = db.insert_id()
    cur.close()
    db.commit()
    if not private:
        s = flask.render_template("memo_s.html",
                                  memo_id=memo_id,
                                  content=content,
                                  username=user['username'],
                                  created_at=created_at,
                                  )
        redis.rpush('memos', s.encode('utf-8'))
    gen_markdown(memo_id, content)
    return redirect(url_for('memo', memo_id=memo_id))

def init_memos():
    with app.app_context():
        url_list = open('/tmp/urls.txt', 'w')
        redis.delete('memos')
        cur  = get_db().cursor()
        cur.execute('SELECT id, user, content, is_private, created_at, updated_at FROM memos WHERE is_private=0 ORDER BY created_at')
        memos = cur.fetchall()
        for memo in memos:
            if memo['is_private']:
                gen_markdown(memo['id'], memo['content'])
                continue

            username = get_user_by_id(memo['user'])['username']
            s = flask.render_template("memo_s.html",
                                      memo_id=memo['id'],
                                      content=memo['content'],
                                      username=username,
                                      created_at=memo['created_at'],
                                      )
            redis.rpush('memos', s.encode('utf-8'))
            print('http://localhost/memo/%s' % (memo['id'],), file=url_list)
        url_list.close()

if __name__ == "__main__":
    import sys
    load_config()
    if sys.argv[-1] == 'init':
        init_memos()
    else:
        port = int(os.environ.get("PORT", '5000'))
        app.run(debug=1, host='0.0.0.0', port=port)
else:
    load_config()
