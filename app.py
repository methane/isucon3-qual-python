from __future__ import print_function

import codecs
from collections import defaultdict

try:
    import MySQLdb
    from MySQLdb.cursors import DictCursor
except ImportError:
    import pymysql as MySQLdb
    from pymysql.cursors import DictCursor

import jinja2
from jinja2 import Markup
import bottle
from bottle import (
    request, response, redirect, abort, jinja2_template,
)


jinja_env = jinja2.Environment(autoescape=True, loader=jinja2.FileSystemLoader("templates"),
                               extensions=['jinja2.ext.autoescape'], auto_reload=False)

def render_template(name, **kwargs):
    return jinja_env.get_template(name).render(**kwargs)
    #return jinja2_template("templates/" + name, **kwargs)

#import memcache
#from flask_memcache_session import Session

import json, os, hashlib, tempfile, subprocess, time

import misaka
markdown = misaka.Markdown(misaka.HtmlRenderer())
#import mistune

config = {}

app = bottle.app()
#app = Flask(__name__, static_url_path='')
#app.debug = True
#app.cache = memcache.Client(['localhost:11211'], debug=0)
#app.session_interface = Session()
#app.session_cookie_name = "isucon_session_python"
#app.wsgi_app = ProxyFix(app.wsgi_app)
_sessions = {}

SESSION_COOKIE_NAME = "isucon_session_python"

_memolist = []
_last_public = None

with open('templates/frame_nouser.html') as f:
    FRAME_A, FRAME_B = f.read().split('{{ content }}')

index_frame = jinja_env.get_template('frame.html')
mypage_tmpl = jinja_env.get_template('mypage.html')
memo_tmpl = jinja_env.get_template('memo.html')


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


def get_session():
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id is None:
        return None
    sess = _sessions.get(session_id)
    if sess is None:
        #raise Exception("Session %s is not found" % (session_id,))
        response.delete_cookie(SESSION_COOKIE_NAME)
    return sess


def get_user():
    session = get_session()
    if not session:
        return None, None
    user = get_user_by_id(session['user_id'])
    if user is not None:
        response.headers['Cache-Control'] = 'private'
    return user, session


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
    if 'title' not in memo:
        memo['title'] = memo['content'].split('\n', 1)[0]
    if 'title_li' not in memo:
        s = render_template("memo_s.html",
                            memo_id=memo['id'],
                            title=memo['title'],
                            username=memo['username'],
                            created_at=memo['created_at'],
                            )
        memo['title_li'] = str(s)
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

_user_memo = defaultdict(list)


def anti_csrf():
    session = get_session()
    if request.forms['sid'] != session.get('token'):
        abort(400)


def require_user(user):
    if not user:
        abort(403)


_md_cache = {}

def gen_markdown(memo_id, md):
    html = _md_cache.get(memo_id)
    if html:
        return html
    html = markdown.render(md)
    #html = mistune.markdown(md)
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


@app.route("/recent/<page:int>")
def recent(page):
    user, session = get_user()
    content = get_memos(page)
    if not user:
        return FRAME_A + str(content) + FRAME_B
    return index_frame.render(user=user, content=content, session=session)


@app.route("/mypage")
def mypage():
    user, session = get_user()
    if not user:
        abort(403)
    #cur = get_db().cursor()
    #cur.execute('SELECT id, content, is_private, created_at, updated_at FROM memos WHERE user=%s ORDER BY created_at DESC', (user["id"],))
    #memos = cur.fetchall()
    #cur.close()
    memos = reversed(_user_memo[user['id']])
    return mypage_tmpl.render(
        user=user,
        memos=memos,
        session=session,
    )


@app.route("/signin", method='GET')
def signin():
    user, session = get_user()
    return render_template('signin.html', user=user, session=session)


@app.route("/signin", method='POST')
def signin_post():
    username = request.forms['username']
    password = request.forms['password']
    cur  = get_db().cursor()
    cur.execute('SELECT id, username, password, salt FROM users WHERE username=%s', (username,))
    user = cur.fetchone()
    if user and user["password"] == hashlib.sha256((user["salt"] + password).encode('utf-8')).hexdigest():
        session = {}
        session["user_id"] = user["id"]
        session["token"] = codecs.encode(os.urandom(10), 'hex').decode('ascii')
        session_id = codecs.encode(os.urandom(30), 'hex').decode('ascii')
        _sessions[session_id] = session
        response.set_cookie(SESSION_COOKIE_NAME, session_id, httponly=True)
        return redirect("http://localhost/mypage")
    else:
        return render_template('signin.html', user=None, session={})


@app.route("/signout", method='POST')
def signout():
    anti_csrf()
    response.delete_cookie(SESSION_COOKIE_NAME)
    return redirect("/")


@app.route("/memo/<memo_id:int>")
def memo(memo_id):
    user, session = get_user()

    memo = get_memo_by_id(memo_id)
    if not memo:
        abort(404)

    if memo["is_private"] == 1:
        if not user or user["id"] != memo["user"]:
            abort(404)

    show_private = user and user['id'] == memo['user']
    #if user and user["id"] == memo["user"]:
    #    cond = ""
    #else:
    #    cond = "AND is_private=0"
    if show_private:
        older = memo['prev_private']
        newer = memo['next_private']
    else:
        older = memo['prev_id']
        newer = memo['next_id']
    #cur  = get_db().cursor()
    #cur.execute("SELECT id FROM memos WHERE user=%s " + cond + " AND id<%s ORDER BY id DESC LIMIT 1", (memo["user"], memo['id']))
    #older_memos = cur.fetchall()
    #cur.execute("SELECT id FROM memos WHERE user=%s " + cond + " AND id>%s ORDER BY id LIMIT 1", (memo["user"], memo['id']))
    #newer_memos = cur.fetchall()
    #cur.close()
    #if older_memos:
    #    older = older_memos[0]
    #if newer_memos:
    #    newer = newer_memos[0]

    return memo_tmpl.render(
        user=user,
        memo=memo,
        older=older,
        newer=newer,
        session=session,
    )


@app.route("/memo", method='POST')
def memo_post():
    global _last_public
    user, token = get_user()
    if not user:
        abort(403)
    anti_csrf()
    private = int(request.forms.get("is_private") or 0)
    content = request.forms["content"]
    created_at=time.strftime('%Y-%m-%d %H:%M:%S')

    db  = get_db()
    cur = db.cursor()
    cur.execute(
        "INSERT INTO memos (user, content, is_private, created_at) VALUES (%s, %s, %s, %s)",
        (user["id"], content, private, created_at)
    )
    memo_id = db.insert_id()
    cur.close()
    memo = {
        'id': memo_id,
        'user': user['id'],
        'username': user['username'],
        'is_private': private,
        'content': content,
        'created_at': created_at,
        'updated_at': created_at,
    }
    set_memo_cache(memo)
    ul = _user_memo[user['id']]
    memo['prev_id'] = memo['next_id'] = None
    memo['prev_private'] = memo['next_private'] = None
    if ul:
        ul[-1]['next_private'] = memo['id']
        memo['prev_private'] = ul[-1]['id']
        for m in reversed(ul):
            if not m['is_private']:
                m['next_id'] = memo['id']
                memo['prev_id'] = m['id']
                break

    ul.append(memo)
    if not private:
        _memolist.append(memo['title_li'])
        if _last_public:
            memo['prev_id'] = _last_public['id']
            _last_public['next_id'] = memo['id']
        _last_public = memo

    return redirect("/memo/%s" % (memo_id,))

@app.route('/__init__')
def _init_():
    _md_cache.clear()
    _userid_cache.clear()
    _memo_cache.clear()
    _sessions.clear()
    _user_memo.clear()
    cur  = get_db().cursor()

    cur.execute('SELECT * FROM users')
    for user in cur:
        _userid_cache[user['id']] = user

    cur.execute('SELECT id, user, content, is_private, created_at, updated_at FROM memos ORDER BY created_at')
    memos = cur.fetchall()
    f = open('/tmp/memos.txt', 'w')
    for memo in memos:
        memo['next_id'] = memo['prev_id'] = None
        memo['next_private'] = memo['prev_private'] = None
        set_memo_cache(memo)
        _user_memo[memo['user']].append(memo)
        if memo['is_private']:
            continue

        _memolist.append(memo['title_li'])
        print('http://localhost/memo/' + str(memo['id']), file=f)
    f.close()

    for uid, memos in _user_memo.items():
        for a, b in zip(memos, memos[1:]):
            a['next_private'] = b['id']
            b['prev_private'] = a['id']
        last_public = None
        for m in memos:
            if m['is_private']:
                continue
            if last_public is not None:
                last_public['next_id'] = m['id']
                m['prev_id'] = last_public['id']
            last_public = m

    return 'OK'


load_config()
if __name__ == "__main__":
    import sys
    port = int(os.environ.get("PORT", '5000'))
    #app.run(debug=1, host='0.0.0.0', port=port)
    _init_()
    bottle.run(host='0.0.0.0', port=port, debug=False, quiet=True)
