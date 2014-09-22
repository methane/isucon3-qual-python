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
from flask_memcache_session import Session
#from werkzeug.contrib.fixers import ProxyFix

import json, os, hashlib, tempfile, subprocess, time
import misaka

markdown = misaka.Markdown(misaka.HtmlRenderer())

config = {}

app = Flask(__name__, static_url_path='')
app.debug = True
app.cache = memcache.Client(['localhost:11211'], debug=0)
app.session_interface = Session()
app.session_cookie_name = "isucon_session_python"
#app.wsgi_app = ProxyFix(app.wsgi_app)


_memolist = []

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
            unix_socket='/var/lib/mysql/mysql.sock',
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


_memo_cache = {}

def set_memo_cache(memo):
    if 'username' not in memo:
        memo["username"] = get_user_by_id(memo['user'])["username"]
    if 'content_html' not in memo:
        memo["content_html"] = gen_markdown(memo['id'], memo["content"])
    _memo_cache[memo['id']] = memo


def get_memo_by_id(memo_id):
    memo = _memo_cache.get(memo_id)
    if not memo:
        cur = get_db().cursor()
        cur.execute('SELECT id, user, content, is_private, created_at, updated_at FROM memos WHERE id=%s', (memo_id,))
        memo = cur.fetchone()
        cur.close()
        set_memo_cache(memo)
    return memo


def anti_csrf():
    if request.form['sid'] != session['token']:
        abort(400)


def require_user(user):
    if not user:
        redirect(url_for("top_page"))
        abort(403)


_md_cache = {}

def gen_markdown(memo_id, md):
    html = _md_cache.get(memo_id)
    if html:
        return html
    html = markdown.render(md)
    _md_cache[memo_id] = html
    return html


_db = None
def get_db():
    global _db
    if _db is None:
        _db = connect_db()
    return _db


memos_templ = """\
<h3>public memos</h3>
<p id="pager">
  recent {start} - {end} / total <span id="total">{total}</span>
</p>
<ul id="memos">
{memos}
</ul>
"""

def get_memos(page):
    total = len(_memolist)
    start = (page+1)*(-100)-1
    end = (page)*(-100)-1
    memos = _memolist[end:start:-1]

    if not memos:
        abort(404)

    memos = '\n'.join(memos)
    content = memos_templ.format(start=page*100+1, end=page*100+100, total=total, memos=memos)
    return content


@app.route("/")
def top_page():
    return recent(0)

@app.route("/recent/<int:page>")
def recent(page):
    user = get_user()
    content = get_memos(page)
    if not user:
        return FRAME_A + str(content) + FRAME_B
    return render_template('frame.html', user=user, content=flask.Markup(content))


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

    memo = get_memo_by_id(memo_id)
    if not memo:
        abort(404)

    if memo["is_private"] == 1:
        if not user or user["id"] != memo["user"]:
            abort(404)

    if user and user["id"] == memo["user"]:
        cond = ""
    else:
        cond = "AND is_private=0"
    memos = []
    older = None
    newer = None
    cur  = get_db().cursor()
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
        _memolist.append(s)
    gen_markdown(memo_id, content)
    return redirect(url_for('memo', memo_id=memo_id))

@app.route('/__init__')
def _init_():
    _md_cache.clear()
    _userid_cache.clear()
    _memo_cache.clear()
    cur  = get_db().cursor()

    cur.execute('SELECT * FROM users')
    for user in cur:
        _userid_cache[user['id']] = user

    cur.execute('SELECT id, user, content, is_private, created_at, updated_at FROM memos ORDER BY created_at')
    memos = cur.fetchall()
    f = open('/tmp/memos.txt', 'w')
    for memo in memos:
        set_memo_cache(memo)
        if memo['is_private']:
            continue

        s = flask.render_template("memo_s.html",
                                  memo_id=memo['id'],
                                  content=memo['content'],
                                  username=memo['username'],
                                  created_at=memo['created_at'],
                                  )
        _memolist.append(s)
        print('http://localhost/memo/' + str(memo['id']), file=f)
    f.close()
    return 'OK'


load_config()
if __name__ == "__main__":
    import sys
    port = int(os.environ.get("PORT", '5000'))
    app.run(debug=1, host='0.0.0.0', port=port)
