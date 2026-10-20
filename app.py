"""MIGA Ahorra: API FastAPI para despliegue en Railway con un único proceso/worker.
Ver README antes de abrir registro o contacto público.
"""
from __future__ import annotations
import base64
import datetime as dt
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import time
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import HTTPError, URLError
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

import legacy_core as core
from backup import make_backup, backup_loop
import threading

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / 'public'
DATABASE_DIR = Path(os.getenv('MIGA_DATA_DIR', ROOT / 'data')).resolve()
DATABASE = DATABASE_DIR / 'miga.sqlite3'
PRODUCTION = os.getenv('MIGA_ENV', 'development').lower() == 'production'
BASE_URL = os.getenv('MIGA_PUBLIC_URL', 'http://127.0.0.1:8000').strip().rstrip('/')
core.full_url = lambda path: BASE_URL + path
CONTACT_EMAIL = os.getenv('MIGA_CONTACT_EMAIL', '').strip()
REMEMBER_SECONDS = 30 * 86400
SESSION_SECONDS = 12 * 3600
GUEST_SECONDS = 24 * 3600

# Los documentos legales requieren revisión y datos reales antes de abrir el registro.
def legal_complete():
    # El aviso legal y la privacidad deben tener datos reales y revisados por el titular.
    keys = ('MIGA_LEGAL_NAME', 'MIGA_CONTACT_EMAIL')
    return all(os.getenv(k, '').strip() for k in keys) and os.getenv('MIGA_LEGAL_REVIEWED') == '1'

def mail_ready():
    sender=os.getenv('MIGA_EMAIL_FROM', os.getenv('MIGA_SMTP_FROM','')).strip()
    via_api=bool(os.getenv('MIGA_BREVO_API_KEY','').strip() and sender)
    via_smtp=all(os.getenv(k,'').strip() for k in ('MIGA_SMTP_HOST','MIGA_SMTP_USER','MIGA_SMTP_PASSWORD')) and bool(sender)
    return (via_api or via_smtp) and core.valid_email(sender)

def can_register():
    return os.getenv('MIGA_PUBLIC_REGISTRATION') == '1' and legal_complete() and mail_ready()

def conn():
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DATABASE, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=20000')
    return c

def db_setup():
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    core.DB = DATABASE
    # core.DATA remains the public JSON data directory within the project.
    core.setup()
    with conn() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
          CREATE TABLE IF NOT EXISTS email_verifications(
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            expires INTEGER NOT NULL
          );
          CREATE INDEX IF NOT EXISTS idx_email_verifications_expires ON email_verifications(expires);
          CREATE TABLE IF NOT EXISTS password_resets(
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires INTEGER NOT NULL
          );
          CREATE TABLE IF NOT EXISTS rate_limits(
            bucket TEXT NOT NULL,
            event_at INTEGER NOT NULL
          );
          CREATE INDEX IF NOT EXISTS idx_limits ON rate_limits(bucket,event_at);
          CREATE INDEX IF NOT EXISTS idx_resets_expiry ON password_resets(expires);
        ''')
    # Migración: cuentas de ediciones anteriores conservan el acceso.
    # Los usuarios nuevos empiezan sin verificar y NO pueden iniciar sesión.
    with conn() as db:
        cols={r['name'] for r in db.execute('PRAGMA table_info(users)')}
        if 'email_verified' not in cols:
            db.execute('ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1')
        if 'privacy_accepted_at' not in cols:
            db.execute('ALTER TABLE users ADD COLUMN privacy_accepted_at TEXT')
    # Bootstrap a single admin; use only as a temporary Railway secret, then remove it.
    bootstrap_email = os.getenv('MIGA_INITIAL_ADMIN_EMAIL', '').strip().lower()
    bootstrap_password = os.getenv('MIGA_INITIAL_ADMIN_PASSWORD', '')
    if bootstrap_email and bootstrap_password:
        if not core.valid_email(bootstrap_email) or len(bootstrap_password)<16:
            raise RuntimeError('El email inicial o la contraseña de administrador no cumplen los requisitos.')
        with conn() as db:
            existing=db.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone()
            if not existing:
                db.execute('INSERT INTO users(name,email,password,role,joined_at) VALUES(?,?,?,\'admin\',?)',
                    ('Administrador MIGA',bootstrap_email,core.make_password(bootstrap_password),core.now()))
                print('Cuenta inicial de administrador creada. Retira las dos variables de bootstrap de Railway.')

# Retención definida y aplicada; las mismas reglas se explican en /privacidad.
CONTACT_RETENTION_DAYS = 90
PENDING_ACCOUNT_RETENTION_DAYS = 30


def purge_expired_personal_data():
    now_utc = dt.datetime.now(dt.timezone.utc)
    contact_limit = (now_utc - dt.timedelta(days=CONTACT_RETENTION_DAYS)).isoformat(timespec='seconds')
    pending_limit = (now_utc - dt.timedelta(days=PENDING_ACCOUNT_RETENTION_DAYS)).isoformat(timespec='seconds')
    timestamp = int(now_utc.timestamp())
    with conn() as db:
        # Se eliminan mensajes antiguos, sesiones y tokens expirados.
        db.execute('DELETE FROM contact_messages WHERE created_at < ?', (contact_limit,))
        db.execute('DELETE FROM sessions WHERE expires <= ?', (timestamp,))
        db.execute('DELETE FROM password_resets WHERE expires <= ?', (timestamp,))
        db.execute('DELETE FROM email_verifications WHERE expires <= ?', (timestamp,))
        db.execute('DELETE FROM rate_limits WHERE event_at < ?', (timestamp - 86400,))
        # Las cuentas nunca verificadas se depuran tras 30 días.
        db.execute("DELETE FROM users WHERE role='user' AND email_verified=0 AND joined_at < ?", (pending_limit,))


def housekeeping_loop():
    while True:
        try:
            purge_expired_personal_data()
        except sqlite3.Error as exc:
            print('ATENCIÓN: mantenimiento de privacidad pendiente:', type(exc).__name__)
        time.sleep(86400)

@asynccontextmanager
async def lifespan(app):
    if PRODUCTION:
        if not BASE_URL.startswith('https://') or urlsplit(BASE_URL).hostname in ('localhost','127.0.0.1',None):
            raise RuntimeError('MIGA_PUBLIC_URL debe tener un dominio HTTPS real en producción.')
        if not os.getenv('MIGA_DATA_DIR'):
            raise RuntimeError('Configura MIGA_DATA_DIR=/data y añade un volumen persistente en Railway.')
        if not legal_complete():
            print('ATENCIÓN: política de privacidad y datos del titular pendientes; registro y contacto bloqueados.')
        if os.getenv('MIGA_PUBLIC_REGISTRATION') == '1' and not mail_ready():
            print('ATENCIÓN: configura Brevo API o SMTP; registro público bloqueado.')
    db_setup()
    # Primero depuramos datos expirados para que no entren en nuevas copias.
    purge_expired_personal_data()
    threading.Thread(target=housekeeping_loop,daemon=True).start()
    # Copia automática diaria local y cifrada externa si Drive está configurado.
    threading.Thread(target=backup_loop,args=(DATABASE,),daemon=True).start()
    yield

app = FastAPI(title='MIGA Ahorra', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
# Usar siempre un hostname válido. Starlette lanza un error 500 si algún
# elemento de allowed_hosts es None (por ejemplo, por configuración parcial).
def production_allowed_hosts():
    public_host = urlsplit(BASE_URL).hostname
    if not public_host:
        raise RuntimeError('MIGA_PUBLIC_URL debe ser una URL HTTPS válida con dominio, por ejemplo https://mi-web.up.railway.app')
    hosts = [public_host]
    raw = os.getenv('MIGA_ALLOWED_HOSTS', '')
    for item in raw.split(','):
        value = item.strip()
        if not value:
            continue
        if '://' in value:
            value = urlsplit(value).hostname or ''
        else:
            value = value.split('/')[0].split(':')[0].strip()
        if value and '*' not in value and value not in hosts:
            hosts.append(value)
    return hosts

if PRODUCTION:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=production_allowed_hosts())

@app.middleware('http')
async def security_headers(request:Request, call_next):
    resp=await call_next(request)
    resp.headers['X-Content-Type-Options']='nosniff'
    resp.headers['X-Frame-Options']='DENY'
    resp.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    resp.headers['Permissions-Policy']='camera=(self), microphone=(), geolocation=()'
    resp.headers['Content-Security-Policy']=("default-src 'self'; img-src 'self' https: data: blob:; "
       "style-src 'self' 'unsafe-inline'; font-src 'self'; "
       "script-src 'self' https://cdn.jsdelivr.net 'wasm-unsafe-eval'; worker-src 'self' blob: https://cdn.jsdelivr.net; "
       "connect-src 'self' https://cdn.jsdelivr.net; frame-src 'none'; "
       "media-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
    if PRODUCTION: resp.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
    if request.url.path.startswith('/api/') or request.url.path in ('/recuperar','/verificar','/privacidad','/aviso-legal'):
        resp.headers['Cache-Control']='no-store'
    return resp

@app.exception_handler(HTTPException)
async def http_exception_handler(request:Request, exc:HTTPException):
    return JSONResponse({'error':str(exc.detail)}, status_code=exc.status_code)

def err(message='Datos incorrectos.',code=400):raise HTTPException(code, message)

def clean_ip(request:Request):
    # No se confía en cabeceras IP enviadas directamente por el visitante.
    return request.client.host if request.client else 'unknown'

def rate_check(bucket, max_requests, seconds):
    stamp=int(time.time())
    with conn() as db:
        # BEGIN IMMEDIATE serializa limitadores también con peticiones simultáneas.
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM rate_limits WHERE event_at<?',(stamp-86400,))
        count=db.execute('SELECT COUNT(*) FROM rate_limits WHERE bucket=? AND event_at>=?',(bucket,stamp-seconds)).fetchone()[0]
        if count>=max_requests:err('Demasiados intentos. Espera unos minutos.',429)
        db.execute('INSERT INTO rate_limits(bucket,event_at) VALUES(?,?)',(bucket,stamp))

def hash_token(token):return hashlib.sha256(token.encode('utf-8')).hexdigest()

def new_token():return secrets.token_urlsafe(40)

def get_session(request):
    key=request.cookies.get('miga_session','')
    if not key or len(key)>200:return None
    with conn() as db:
        return db.execute('SELECT s.*,u.id AS uid,u.name,u.email,u.role FROM sessions s LEFT JOIN users u ON s.user_id=u.id WHERE token_hash=? AND expires>?',
             (hash_token(key),int(time.time()))).fetchone()

def session_data(row):return {'id':row['uid'],'name':row['name'],'email':row['email'],'role':row['role']}

def write_cookie(resp, token, remember=False, remove=False):
    if remove:
        resp.delete_cookie('miga_session',path='/',secure=PRODUCTION,httponly=True,samesite='lax')
    else:
        resp.set_cookie('miga_session',token,max_age=REMEMBER_SECONDS if remember else None,
           secure=PRODUCTION,httponly=True,samesite='lax',path='/')

def create_session(uid=None, remember=False):
    token=new_token();csrf=new_token()
    ttl=REMEMBER_SECONDS if remember else SESSION_SECONDS if uid else GUEST_SECONDS
    with conn() as db:
        db.execute('INSERT INTO sessions(token_hash,csrf,user_id,expires) VALUES(?,?,?,?)',
                   (hash_token(token),csrf,uid,int(time.time())+ttl))
    return token,csrf

def require_session(request, authorized=False, admin=False):
    row=get_session(request)
    if row is None:err('Actualiza la página para iniciar una sesión segura.',401)
    if authorized and row['uid'] is None:err('Inicia sesión para continuar.',401)
    if admin and row['role']!='admin':err('Acceso solo para administración.',403)
    return row

def csrf_check(request,row):
    origin=request.headers.get('origin','')
    expected=BASE_URL if PRODUCTION else f'{request.url.scheme}://{request.url.netloc}'
    if not origin or origin.rstrip('/')!=expected:err('Origen no autorizado.',403)
    provided=request.headers.get('x-csrf-token','')
    if not row or not provided or not hmac.compare_digest(provided,row['csrf']):
        err('Sesión de seguridad no válida. Actualiza la página.',403)

def profile(row):
    if not row or row['uid'] is None:
        return {'user':None,'favorites':[],'completed':[],'plan':None,'customizations':{},'pantryItems':{}}
    uid=row['uid']
    with conn() as db:
        fav=[x[0] for x in db.execute('SELECT recipe_id FROM favorites WHERE user_id=?',(uid,))]
        done=[x[0] for x in db.execute('SELECT recipe_id FROM completed WHERE user_id=?',(uid,))]
        plan=db.execute('SELECT recipe_ids,servings,budget,category,store FROM plans WHERE user_id=?',(uid,)).fetchone()
        custom={str(x['recipe_id']):json.loads(x['payload']) for x in db.execute('SELECT recipe_id,payload FROM customizations WHERE user_id=?',(uid,))}
        pantry={x['ingredient_id']:{'qty':x['quantity'],'expiry':x['expires_at'] or ''} for x in db.execute('SELECT ingredient_id,quantity,expires_at FROM pantry_items WHERE user_id=?',(uid,))}
    return {'user':session_data(row),'favorites':fav,'completed':done,'customizations':custom,'pantryItems':pantry,
     'plan':{'ids':json.loads(plan['recipe_ids']),'servings':plan['servings'],'budget':plan['budget'],'category':plan['category'],'store':plan['store']} if plan else None}

async def json_body(request:Request):
    if not request.headers.get('content-type','').lower().startswith('application/json'):
        err('Se requiere JSON.',415)
    if int(request.headers.get('content-length','0') or '0')>16384:err('Petición demasiado grande.',413)
    raw=await request.body()
    if not raw or len(raw)>16384:err('Datos vacíos o demasiado grandes.',400)
    try:body=json.loads(raw)
    except (ValueError,UnicodeDecodeError):err('JSON inválido.')
    if not isinstance(body,dict):err('Datos incorrectos.')
    return body

def send_mail(to,subject,message,reply_to=None):
    """Correo transaccional: Brevo API sobre HTTPS (Railway Hobby) o SMTP (local/Pro).
    No envía claves por consola ni deja tokens en los logs.
    """
    sender=os.getenv('MIGA_EMAIL_FROM',os.getenv('MIGA_SMTP_FROM','')).strip()
    if not core.valid_email(to) or not core.valid_email(sender):return False
    api_key=os.getenv('MIGA_BREVO_API_KEY','').strip()
    if api_key:
        payload={'sender':{'name':'MIGA Ahorra','email':sender},'to':[{'email':to}],
                 'subject':subject,'textContent':message}
        if reply_to and core.valid_email(reply_to):payload['replyTo']={'email':reply_to}
        data=json.dumps(payload,ensure_ascii=False).encode('utf-8')
        req=UrlRequest('https://api.brevo.com/v3/smtp/email',data=data,method='POST',
           headers={'Content-Type':'application/json','accept':'application/json','api-key':api_key})
        try:
            with urlopen(req,timeout=12) as resp:
                return resp.status==201
        except (URLError,HTTPError,OSError,ValueError):
            print('Brevo: no se ha podido aceptar el correo. Revisa remitente y API en Railway.')
            return False
    host=os.getenv('MIGA_SMTP_HOST','').strip(); user=os.getenv('MIGA_SMTP_USER','').strip()
    password=os.getenv('MIGA_SMTP_PASSWORD','')
    if not all((host,user,password)):return False
    mail=EmailMessage();mail['From']=sender;mail['To']=to;mail['Subject']=subject
    if reply_to and core.valid_email(reply_to):mail['Reply-To']=reply_to
    mail.set_content(message)
    try:
        port=int(os.getenv('MIGA_SMTP_PORT','587'))
        if port==465:
            with smtplib.SMTP_SSL(host,port,timeout=12,context=ssl.create_default_context()) as client:
                client.login(user,password);client.send_message(mail)
        elif port==587:
            with smtplib.SMTP(host,port,timeout=12) as client:
                client.starttls(context=ssl.create_default_context());client.login(user,password);client.send_message(mail)
        else:return False
        return True
    except (OSError,ValueError,smtplib.SMTPException):
        print('SMTP: fallo de entrega (sin contraseñas ni contenido en logs).')
        return False

def send_verification(user_id,email):
    token=new_token();until=int(time.time())+24*3600
    # Primero se registra el token para que enlaces no lleguen antes de persistirlo.
    with conn() as db:
        db.execute('DELETE FROM email_verifications WHERE user_id=?',(user_id,))
        db.execute('INSERT INTO email_verifications(token_hash,user_id,expires) VALUES(?,?,?)',
                   (hash_token(token),user_id,until))
    link=BASE_URL+'/verificar#token='+token
    sent=send_mail(email,'Verifica tu correo en MIGA Ahorra',
        '¡Bienvenido a MIGA Ahorra!\n\nConfirma tu cuenta con este enlace, válido durante 24 horas:\n'
        +link+'\n\nSi no has creado esta cuenta, ignora el mensaje.')
    if not sent:
        with conn() as db:db.execute('DELETE FROM email_verifications WHERE token_hash=?',(hash_token(token),))
    return sent

def recipe_exists(rid):return type(rid) is int and any(r['id']==rid for r in core.recipe_catalog())

@app.get('/healthz')
def healthz():
    with conn() as db: db.execute('SELECT 1').fetchone()
    return {'ok':True}

@app.get('/api/version')
def version():return {'version':'web','nombre':'MIGA Ahorra','nevera':True}

@app.get('/api/bootstrap')
def bootstrap(request:Request):
    row=get_session(request);token=None
    if row is None:
        token,csrf=create_session()
        row={'uid':None,'csrf':csrf}
    d=profile(row);d['csrf']=row['csrf'];d['registrationOpen']=can_register()
    resp=JSONResponse(d)
    if token:write_cookie(resp,token)
    return resp

@app.get('/api/data')
def data():
    return {'recipes':core.recipe_catalog(),
            'prices':json.loads((ROOT/'data'/'prices.json').read_text('utf8')),'updated':'2026-10-16'}

@app.get('/api/reviews')
def reviews(recipe:int=0):
    if not recipe_exists(recipe):err('Receta no válida.')
    with conn() as db:
        result=[dict(x) for x in db.execute('SELECT u.name,r.rating,r.comment,r.created_at FROM reviews r JOIN users u ON u.id=r.user_id WHERE r.recipe_id=? ORDER BY r.created_at DESC LIMIT 60',(recipe,))]
    return {'reviews':result,'average':round(sum(x['rating'] for x in result)/len(result),1) if result else None,'count':len(result)}

@app.get('/api/ratings')
def ratings():
    with conn() as db:
        rows=[dict(x) for x in db.execute('SELECT recipe_id,ROUND(AVG(rating),1) AS average,COUNT(*) AS count FROM reviews GROUP BY recipe_id')]
    return {'ratings':rows}

@app.get('/api/admin')
def admin(request:Request):
    require_session(request,authorized=True,admin=True)
    with conn() as db:
        users=[dict(x) for x in db.execute('SELECT u.id,u.name,u.email,u.role,u.joined_at, COUNT(DISTINCT r.id) reviews_count,COUNT(DISTINCT f.recipe_id) favorites_count FROM users u LEFT JOIN reviews r ON u.id=r.user_id LEFT JOIN favorites f ON u.id=f.user_id GROUP BY u.id ORDER BY joined_at DESC')]
        rev=[dict(x) for x in db.execute('SELECT r.recipe_id,r.rating,r.comment,r.created_at,u.name,u.email FROM reviews r JOIN users u ON r.user_id=u.id ORDER BY created_at DESC LIMIT 50')]
    return {'users':users,'reviews':rev}

@app.get('/api/admin/contactos')
def contacts(request:Request):
    require_session(request,authorized=True,admin=True)
    with conn() as db:
        values=[dict(x) for x in db.execute('SELECT id,name,email,subject,message,delivered,created_at FROM contact_messages ORDER BY id DESC LIMIT 100')]
    return {'contacts':values}

@app.get('/api/account/export')
def account_export(request:Request):
    session=require_session(request,authorized=True)
    uid=session['uid']
    with conn() as db:
        account=dict(db.execute('SELECT name,email,joined_at FROM users WHERE id=?',(uid,)).fetchone())
        favorites=[dict(x) for x in db.execute('SELECT recipe_id FROM favorites WHERE user_id=?',(uid,))]
        completed=[dict(x) for x in db.execute('SELECT recipe_id,completed_at FROM completed WHERE user_id=?',(uid,))]
        reviews=[dict(x) for x in db.execute('SELECT recipe_id,rating,comment,created_at FROM reviews WHERE user_id=?',(uid,))]
        pantry=[dict(x) for x in db.execute('SELECT ingredient_id,quantity,expires_at FROM pantry_items WHERE user_id=?',(uid,))]
        customs=[dict(x) for x in db.execute('SELECT recipe_id,payload FROM customizations WHERE user_id=?',(uid,))]
        plan=db.execute('SELECT recipe_ids,servings,budget,category,store,saved_at FROM plans WHERE user_id=?',(uid,)).fetchone()
    return {'account':account,'favorites':favorites,'completed':completed,'reviews':reviews,'pantry':pantry,'customizations':customs,'plan':dict(plan) if plan else None}

@app.post('/api/{action:path}')
async def post_api(action:str,request:Request):
    row=require_session(request)
    csrf_check(request,row)
    body=await json_body(request)
    ip=clean_ip(request)
    uid=row['uid']
    if action=='contacto':
        if not legal_complete():err('El formulario de contacto aún no está habilitado.',503)
        rate_check('contact-ip:'+ip,5,3600)
        if str(body.get('website','')).strip():return {'ok':True,'saved':True,'delivered':False}
        name=str(body.get('name','')).strip();addr=str(body.get('email','')).strip().lower()
        subj=str(body.get('subject','')).strip();msg=str(body.get('message','')).strip()
        if not 2<=len(name)<=80 or not core.valid_email(addr) or subj not in ('Sugerencia','Receta','Error','Colaboración','Otro') or not 10<=len(msg)<=2000 or body.get('consent') is not True:err('Comprueba los datos y acepta la política de privacidad.')
        with conn() as db:
            mid=db.execute('INSERT INTO contact_messages(name,email,subject,message,delivered,created_at) VALUES(?,?,?,?,0,?)',(name,addr,subj,msg,core.now())).lastrowid
        delivered=send_mail(CONTACT_EMAIL,'MIGA · '+subj,'Nombre: '+name+'\nCorreo: '+addr+'\nAsunto: '+subj+'\n\n'+msg,addr)
        if delivered:
            with conn() as db:db.execute('UPDATE contact_messages SET delivered=1 WHERE id=?',(mid,))
        return {'ok':True,'saved':True,'delivered':delivered}
    if action in ('login','register'):
        email=str(body.get('email','')).strip().lower();pwd=str(body.get('password',''))
        rate_check('auth-ip:'+ip,30,600)
        if core.valid_email(email):rate_check('auth-email:'+hash_token(email),10,600)
        if not core.valid_email(email) or not 8<=len(pwd)<=256:err('Email o contraseña incorrectos.')
        remember=body.get('remember') is True
        if action=='register':
            if not can_register():err('El registro aún no está disponible: falta activar correo y privacidad.',503)
            name=str(body.get('name','')).strip()
            if len(pwd)<12 or not 2<=len(name)<=60:err('Nombre o contraseña demasiado cortos (contraseña: mínimo 12 caracteres).')
            if body.get('consent') is not True:err('Debes leer y aceptar la política de privacidad para crear tu cuenta.')
            with conn() as db:
                if db.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone():err('Ese correo ya está registrado.',409)
                uid=db.execute("INSERT INTO users(name,email,password,role,joined_at,email_verified,privacy_accepted_at) VALUES(?,?,?,'user',?,0,?)",
                    (name,email,core.make_password(pwd),core.now(),core.now())).lastrowid
            if not send_verification(uid,email):
                # Revertir alta: no se crea una cuenta imposible de verificar si falla el proveedor.
                with conn() as db:db.execute('DELETE FROM users WHERE id=? AND email_verified=0',(uid,))
                err('No hemos podido enviar la verificación. Inténtalo más tarde.',503)
            return {'ok':True,'verificationRequired':True,'message':'Te hemos enviado un enlace para confirmar tu correo. Mira también en Spam.'}
        with conn() as db:
            entry=db.execute('SELECT id,password,email_verified FROM users WHERE email=?',(email,)).fetchone()
            if not entry or not core.check_password(pwd,entry['password']):err('Correo o contraseña incorrectos.',401)
            if not entry['email_verified']:
                err('Confirma el correo que te enviamos antes de iniciar sesión. Puedes solicitar uno nuevo.',403)
            uid=entry['id']
            if request.cookies.get('miga_session'):db.execute('DELETE FROM sessions WHERE token_hash=?',(hash_token(request.cookies['miga_session']),))
        token,new_csrf=create_session(uid,remember)
        with conn() as db:user_row=db.execute('SELECT id AS uid,name,email,role FROM users WHERE id=?',(uid,)).fetchone()
        result=profile(user_row);result.update({'csrf':new_csrf,'ok':True})
        response=JSONResponse(result);write_cookie(response,token,remember)
        return response
    if action=='email/verify':
        rate_check('verify-ip:'+ip,25,3600)
        token=str(body.get('token',''))
        if not 30<=len(token)<=200:err('Enlace de verificación incorrecto.')
        with conn() as db:
            db.execute('BEGIN IMMEDIATE')
            match=db.execute('SELECT user_id FROM email_verifications WHERE token_hash=? AND expires>?',
                      (hash_token(token),int(time.time()))).fetchone()
            if not match:err('El enlace ha caducado o ya se ha utilizado.',400)
            db.execute('UPDATE users SET email_verified=1 WHERE id=?',(match['user_id'],))
            db.execute('DELETE FROM email_verifications WHERE user_id=?',(match['user_id'],))
        return {'ok':True,'message':'Correo confirmado. Ya puedes iniciar sesión.'}
    if action=='email/resend':
        rate_check('resend-ip:'+ip,6,3600)
        email=str(body.get('email','')).strip().lower()
        if not core.valid_email(email):err('Introduce un correo válido.')
        rate_check('resend-email:'+hash_token(email),3,3600)
        if mail_ready():
            with conn() as db:
                found=db.execute('SELECT id FROM users WHERE email=? AND email_verified=0',(email,)).fetchone()
            if found:send_verification(found['id'],email)
        return {'ok':True,'message':'Si tienes una cuenta pendiente, recibirás un enlace. Revisa también Spam.'}
    if action=='logout':
        key=request.cookies.get('miga_session','')
        with conn() as db:db.execute('DELETE FROM sessions WHERE token_hash=?',(hash_token(key),))
        response=JSONResponse({'ok':True});write_cookie(response,None,remove=True)
        return response
    if action=='password/forgot':
        rate_check('reset-ip:'+ip,10,3600)
        email=str(body.get('email','')).strip().lower()
        if not core.valid_email(email):err('Introduce un email válido.')
        # No revelar si la dirección está registrada.
        with conn() as db:existing=db.execute('SELECT id,email_verified FROM users WHERE email=?',(email,)).fetchone()
        if existing and mail_ready() and existing['email_verified']:
            token=new_token();until=int(time.time())+1800
            with conn() as db:
                db.execute('DELETE FROM password_resets WHERE user_id=?',(existing['id'],))
                db.execute('INSERT INTO password_resets(token_hash,user_id,expires) VALUES(?,?,?)',(hash_token(token),existing['id'],until))
            url=BASE_URL+'/recuperar#token='+token
            sent=send_mail(email,'Restablecer contraseña de MIGA',
                'Has pedido cambiar tu contraseña de MIGA. Abre este enlace (caduca en 30 minutos):\n'+url+'\n\nSi no fuiste tú, ignora este mensaje.')
            if not sent:
                with conn() as db:db.execute('DELETE FROM password_resets WHERE token_hash=?',(hash_token(token),))
        return {'ok':True,'message':'Si el correo existe y el envío está configurado, recibirás un enlace.'}
    if action=='password/reset':
        rate_check('reset-submit:'+ip,12,3600)
        token=str(body.get('token','')); password=str(body.get('password',''))
        if not 24<=len(token)<=200 or not 12<=len(password)<=256:err('Enlace o contraseña no válidos (mínimo 12 caracteres).')
        with conn() as db:
            db.execute('BEGIN IMMEDIATE')
            res=db.execute('SELECT user_id FROM password_resets WHERE token_hash=? AND expires>?',(hash_token(token),int(time.time()))).fetchone()
            if not res:err('El enlace ha caducado o ya se utilizó.',400)
            db.execute('UPDATE users SET password=? WHERE id=?',(core.make_password(password),res['user_id']))
            db.execute('DELETE FROM password_resets WHERE user_id=?',(res['user_id'],))
            db.execute('DELETE FROM sessions WHERE user_id=?',(res['user_id'],))
        return {'ok':True}
    if uid is None:err('Inicia sesión para continuar.',401)
    if action=='account/delete':
        pwd=str(body.get('password',''))
        with conn() as db:
            u=db.execute('SELECT password FROM users WHERE id=?',(uid,)).fetchone()
            if not u or not core.check_password(pwd,u['password']):err('Contraseña incorrecta.',403)
            db.execute('DELETE FROM users WHERE id=?',(uid,))
        response=JSONResponse({'ok':True});write_cookie(response,None,remove=True)
        return response
    if action=='pantry':
        items=body.get('items')
        if not isinstance(items,list) or len(items)>120:err('La nevera tiene demasiados artículos.')
        prices=json.loads((ROOT/'data'/'prices.json').read_text('utf8'));clean={}
        for item in items:
            if not isinstance(item,dict):err('Artículo incorrecto.')
            key=item.get('id');qty=item.get('qty');expiry=item.get('expiry') or ''
            if not isinstance(key,str) or key not in prices or type(qty) not in (int,float) or not 0<qty<=200000 or not isinstance(expiry,str) or len(expiry)>10:err('Ingrediente o cantidad incorrectos.')
            if expiry:
                try:
                    if dt.date.fromisoformat(expiry).isoformat()!=expiry:raise ValueError()
                except ValueError:err('Fecha de caducidad incorrecta.')
            clean[key]=(float(qty),expiry)
        with conn() as db:
            db.execute('DELETE FROM pantry_items WHERE user_id=?',(uid,))
            for key,(qty,expiry) in clean.items():db.execute('INSERT INTO pantry_items(user_id,ingredient_id,quantity,expires_at,updated_at) VALUES(?,?,?,?,?)',(uid,key,qty,expiry or None,core.now()))
        return {'ok':True,'count':len(clean)}
    if action=='favorite':
        rid=body.get('recipe_id'); active=body.get('active')
        if not recipe_exists(rid) or type(active)!=bool:err('Favorito incorrecto.')
        with conn() as db:
            if active:db.execute('INSERT OR IGNORE INTO favorites(user_id,recipe_id) VALUES(?,?)',(uid,rid))
            else:db.execute('DELETE FROM favorites WHERE user_id=? AND recipe_id=?',(uid,rid))
        return {'ok':True}
    if action=='completed':
        rid=body.get('recipe_id')
        if not recipe_exists(rid):err('Receta no válida.')
        with conn() as db:db.execute('INSERT OR REPLACE INTO completed(user_id,recipe_id,completed_at) VALUES(?,?,?)',(uid,rid,core.now()))
        return {'ok':True}
    if action=='review':
        rid=body.get('recipe_id');rating=body.get('rating');comment=str(body.get('comment','')).strip()
        if not recipe_exists(rid) or type(rating)!=int or not 1<=rating<=5 or not 15<=len(comment)<=600:err('Elige 1 a 5 estrellas y escribe entre 15 y 600 caracteres.')
        with conn() as db:
            if not db.execute('SELECT 1 FROM completed WHERE user_id=? AND recipe_id=?',(uid,rid)).fetchone():err('Marca la receta como cocinada antes de valorarla.',403)
            db.execute('INSERT INTO reviews(user_id,recipe_id,rating,comment,created_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,recipe_id) DO UPDATE SET rating=excluded.rating,comment=excluded.comment,created_at=excluded.created_at',(uid,rid,rating,comment,core.now()))
        return {'ok':True}
    if action=='customization':
        rid=body.get('recipe_id');payload=body.get('customization')
        if not recipe_exists(rid) or not isinstance(payload,dict):err('Personalización incorrecta.')
        removals=payload.get('remove',[]);swaps=payload.get('swap',{});extras=payload.get('extras',{})
        if not isinstance(removals,list) or not isinstance(swaps,dict) or not isinstance(extras,dict) or max(len(removals),len(swaps),len(extras))>30:err('Demasiados cambios.')
        prices=set(json.loads((ROOT/'data'/'prices.json').read_text('utf8')))
        recipe=next((r for r in core.recipe_catalog() if r['id']==rid),None)
        originals={x['id'] for x in recipe['ingredients']}
        if any(not isinstance(k,str) or k not in originals for k in removals) or any(k not in originals or not isinstance(v,str) or v not in prices for k,v in swaps.items()) or any(k not in prices or type(v) not in (int,float) or not 0<v<=5000 for k,v in extras.items()):err('Cambios de ingredientes incorrectos.')
        with conn() as db:
            db.execute('INSERT INTO customizations(user_id,recipe_id,payload,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id,recipe_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at',(uid,rid,json.dumps({'remove':removals,'swap':swaps,'extras':extras},ensure_ascii=False),core.now()))
        return {'ok':True}
    if action=='plan':
        ids=body.get('ids');servings=body.get('servings');budget=body.get('budget',30);category=body.get('category','todos');store=body.get('store','consum')
        if not isinstance(ids,list) or len(ids)!=14 or any(r is not None and not recipe_exists(r) for r in ids) or type(servings)!=int or not 1<=servings<=8:err('Se necesitan 14 casillas y entre 1 y 8 raciones.')
        if type(budget) not in (int,float) or not 5<=budget<=1000 or category not in ('todos','normal','saludable','vegana','sin-gluten') or store not in ('consum','mercadona'):err('Datos del menú incorrectos.')
        with conn() as db:
            db.execute('INSERT INTO plans(user_id,recipe_ids,servings,budget,category,store,saved_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET recipe_ids=excluded.recipe_ids,servings=excluded.servings,budget=excluded.budget,category=excluded.category,store=excluded.store,saved_at=excluded.saved_at',(uid,json.dumps(ids),servings,budget,category,store,core.now()))
        return {'ok':True}
    err('Ruta no encontrada.',404)

@app.get('/robots.txt')
def robots():
    return PlainTextResponse('User-agent: *\nDisallow: /api/\nDisallow: /recuperar\nDisallow: /verificar\nSitemap: '+BASE_URL+'/sitemap.xml\n')

@app.get('/sitemap.xml')
def sitemap():
    urls=[BASE_URL+'/']+[BASE_URL+'/recetas/'+core.recipe_slug(r['title']) for r in core.recipe_catalog()]
    content='<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join('<url><loc>'+html.escape(url)+'</loc></url>' for url in urls)+'</urlset>'
    return Response(content,media_type='application/xml')

@app.get('/recetas/{slug}')
def recipe_page(slug:str):
    recipe=next((r for r in core.recipe_catalog() if core.recipe_slug(r['title'])==slug),None)
    if not recipe:err('Receta no encontrada.',404)
    return HTMLResponse(core.render_recipe_page(recipe))


def legal_doc(which):
    """Información jurídica adaptada al funcionamiento actual de MIGA.

    Es una propuesta pendiente de validar, no certifica conformidad normativa.
    Se mantiene MIGA_LEGAL_REVIEWED=0 hasta resolver puntos pendientes.
    """
    owner=html.escape(os.getenv('MIGA_LEGAL_NAME','[PENDIENTE: nombre legal]'))
    ident=html.escape(os.getenv('MIGA_LEGAL_ID','').strip())
    address=html.escape(os.getenv('MIGA_LEGAL_ADDRESS','').strip())
    mail=html.escape(CONTACT_EMAIL or '[PENDIENTE: correo de contacto]')
    complete=legal_complete()
    updated=html.escape(os.getenv('MIGA_LEGAL_UPDATED','Pendiente de validación'))
    banner='' if complete else ('<div class="notice" role="status"><b>DOCUMENTO EN REVISIÓN.</b> '
      'No debe darse por aprobado para abrir registros. Revisa la conservación de mensajes, '
      'el uso de Google Drive, los proveedores y los recursos externos antes de activar MIGA_LEGAL_REVIEWED.</div>')
    if which=='privacidad':
        title='Política de privacidad'
        contents=f"""
        <h2>1. Responsable y contacto</h2>
        <p>El responsable de los datos personales tratados en MIGA Ahorra es <strong>{owner}</strong>.
        Para cuestiones de privacidad, ejercicio de derechos o consultas, escribe a
        <a href="mailto:{mail}">{mail}</a>.</p>
        <h2>2. Datos que utilizamos</h2>
        <p>Al crear una cuenta tratamos el nombre que elijas, correo electrónico,
        contraseña protegida mediante una función de derivación criptográfica (no la guardamos en texto legible),
        fecha de registro, verificación de email y constancia de haber leído la información de privacidad.</p>
        <p>Si utilizas las funciones de la web, se pueden guardar tus recetas favoritas,
        recetas completadas, reseñas, valoraciones, personalizaciones, menús, presupuestos y datos de la despensa.
        En las reseñas se muestran públicamente el nombre de usuario, la valoración,
        el comentario y su fecha. Evita incluir datos personales delicados en comentarios.</p>
        <p>Si nos escribes por el formulario de contacto, guardamos el nombre, correo, asunto, mensaje,
        fecha y el estado de entrega. También procesamos datos técnicos necesarios para la seguridad,
        prevención del abuso y gestión de sesiones, incluida la dirección IP asociada a los intentos.</p>
        <h2>3. Finalidades y fundamento jurídico</h2>
        <ul><li>Crear y mantener tu cuenta y proporcionarte las funciones que solicites:
        ejecución de la relación de prestación gratuita del servicio (art. 6.1.b RGPD), cuando resulte aplicable.</li>
        <li>Responder a consultas iniciadas por ti y mantener la seguridad, evitar fraudes y abusos:
        interés legítimo del responsable (art. 6.1.f RGPD), sujeto a su evaluación.</li>
        <li>Cumplir obligaciones legales que correspondan: art. 6.1.c RGPD, cuando sean aplicables.</li></ul>
        <p>No utilizamos los correos de registro para enviar publicidad ni vendemos datos personales.
        No realizamos decisiones automatizadas con efectos jurídicos sobre los usuarios.</p>
        <h2>4. Datos obligatorios y correo</h2>
        <p>Para crear una cuenta necesitas indicar nombre, correo válido y contraseña.
        Sin ellos no podemos ofrecerte funciones personalizadas. Brevo se utiliza para enviar
        correos relacionados con el servicio, como verificación de cuenta y recuperación de contraseña.
        Los enlaces de verificación caducan a las 24 horas y los de recuperación de contraseña a los 30 minutos.</p>
        <h2>5. Alojamiento, destinatarios y posibles transferencias</h2>
        <p>La aplicación y la base de datos activa se alojan mediante <strong>Railway</strong>,
        configurado inicialmente en la región europea de Ámsterdam (sujeto a la
        configuración real que mantenga el titular). Ello no implica
        que toda operación de soporte, los registros técnicos o los subencargados de Railway
        permanezcan en la Unión Europea. Los envíos de correo transaccional los gestiona
        <strong>Brevo</strong>. Las copias cifradas de la base de datos se conservan en
        <strong>Google Drive</strong> mediante Google Apps Script, y requieren una clave separada
        para descifrarlas.</p>
        <p>Estos proveedores pueden tener subencargados o realizar accesos o transferencias
        internacionales. Antes de abrir el registro debemos comprobar los acuerdos de tratamiento,
        la información sobre países de destino y las garantías aplicables (como decisiones de adecuación
        o cláusulas contractuales tipo, según el caso). No se afirma que todos los datos permanezcan en Europa.</p>
        <h2>6. Conservación y eliminación</h2>
        <p>Mantenemos los datos de cuenta y tus preferencias mientras conserves la cuenta,
        salvo cuando exista una obligación legal de mantener cierta información.
        Puedes exportar tus datos y eliminar tu cuenta desde el apartado de cuenta.
        La eliminación borra los registros asociados de la base activa, pero los datos
        pueden permanecer temporalmente en copias de seguridad anteriores.</p>
        <p>El programa mantiene hasta <strong>14 copias locales</strong> y hasta
        <strong>30 copias cifradas externas</strong>, con una limpieza automática de copias
        de más de <strong>30 días</strong> en Google Drive una vez instalada y activada la
        versión de mantenimiento de Apps Script. También se purgan los respaldos locales
        de más de 14 días. Las copias retiradas de Google Drive pueden permanecer un tiempo
        adicional en la papelera del proveedor, según su política de eliminación.
        Puede realizarse una copia extra al reiniciarse el servicio;
        estas copias no deben utilizarse para recuperar cuentas que hayan sido suprimidas,
        salvo necesidad justificada y aplicando de nuevo las supresiones pertinentes.</p>
        <p>Los mensajes enviados mediante el formulario de contacto se eliminan de la base activa
        automáticamente cuando cumplen <strong>90 días</strong>. Las cuentas que no completan
        la verificación del correo se eliminan tras <strong>30 días</strong>. Los tokens de
        confirmación caducan a las 24 horas, los de recuperación a los 30 minutos y los
        registros internos de limitación de intentos se limpian tras 24 horas.
        Los plazos de los registros técnicos de Railway, Brevo y Google se rigen por sus
        condiciones y se comprobarán como parte de la supervisión de proveedores.</p>
        <h2>7. Cookies y almacenamiento del navegador</h2>
        <p>Utilizamos una cookie técnica de sesión denominada <code>miga_session</code>
        para iniciar sesión y mantener la seguridad. Es una cookie técnica necesaria. Si marcas «Recordarme», la cookie puede
        durar hasta 30 días; en caso contrario es una cookie de sesión.
        También usamos almacenamiento local del navegador para recordar determinadas
        preferencias, menús y datos introducidos como invitado. Puedes eliminar esos datos
        desde tu navegador; no todo ese contenido se sincroniza con una cuenta.</p>
        <h2>8. Servicios externos y fotos de listas</h2>
        <p>La web descarga recursos de jsDelivr cuando activas el
        lector de fotografías de listas de la compra; ese reconocimiento se ejecuta en
        el dispositivo del usuario. Los vídeos relacionados se ofrecen como enlaces externos a YouTube y no se
        cargan reproductores incrustados de forma automática. La página no carga
        tipografías remotas de Google Fonts. Si decides abrir enlaces externos o activar
        el lector OCR, esos servicios pueden recibir información técnica, como la IP,
        de acuerdo con sus políticas. El reconocimiento de la foto se realiza en tu
        navegador; la foto no se sube a MIGA para su lectura.</p>
        <h2>9. Tus derechos</h2>
        <p>Puedes solicitar acceso, rectificación, supresión, oposición, limitación y
        portabilidad cuando legalmente proceda, escribiendo a <a href="mailto:{mail}">{mail}</a>.
        También puedes presentar una reclamación ante la
        <a href="https://www.aepd.es/" target="_blank" rel="noopener noreferrer">Agencia Española de Protección de Datos</a>.
        Para acceder, exportar o borrar los datos de la cuenta, hay también herramientas dentro de MIGA.
        No es necesario facilitar datos adicionales que no sean razonablemente necesarios
        para verificar tu identidad y atender la solicitud.</p>
        <h2>10. Cambios en esta política</h2>
        <p>La información está sujeta a revisión y confirmación de las condiciones de
        los proveedores antes del lanzamiento público. Podemos actualizar esta política si cambian las funciones o los proveedores.
        Publicaremos la fecha de revisión y comunicaremos los cambios relevantes cuando corresponda.</p>
        """
    else:
        title='Aviso legal'
        identification = ('<p><strong>Identificación fiscal:</strong> '+ident+'</p>') if ident else ''
        domicile = ('<p><strong>Domicilio:</strong> '+address+'</p>') if address else ''
        contents=f"""
        <h2>1. Identificación</h2>
        <p><strong>Web:</strong> MIGA Ahorra.</p>
        <p><strong>Titular:</strong> {owner}, persona física.</p>
        {identification}{domicile}
        <p><strong>Contacto:</strong> <a href="mailto:{mail}">{mail}</a>.</p>
        <p>MIGA es un proyecto gratuito de información y herramientas para organizar
        comidas y compras. Actualmente no ofrece suscripciones ni venta de productos,
        ni obtiene ingresos mediante anuncios en la web.</p>
        <h2>2. Uso de MIGA</h2>
        <p>Las recetas, calorías, precios y estimaciones son orientativos.
        Los precios pueden cambiar y no constituyen una oferta de venta.
        Comprueba siempre el etiquetado, alérgenos, conservación y disponibilidad
        de los alimentos; MIGA no sustituye consejo médico o dietético profesional.</p>
        <h2>3. Contenidos de usuarios</h2>
        <p>Al publicar reseñas, evita información falsa, ilegal, ofensiva o datos
        personales de terceros. El titular podrá retirar contenido que incumpla estas
        normas. El usuario conserva los derechos que le correspondan sobre sus comentarios.</p>
        <h2>4. Servicios y marcas ajenas</h2>
        <p>MIGA puede mostrar enlaces a supermercados y contenidos de vídeo de terceros.
        Las marcas pertenecen a sus respectivos titulares. Salvo indicación expresa,
        MIGA no está afiliada a dichos comercios ni garantiza su información.</p>
        <h2>5. Protección de datos y contacto</h2>
        <p>Consulta la <a href="/privacidad">política de privacidad</a> para conocer
        el tratamiento de datos personales y el ejercicio de derechos. Las consultas
        generales pueden enviarse a <a href="mailto:{mail}">{mail}</a>.</p>
        <h2>6. Información legal complementaria</h2>
        <p>La aplicabilidad de las obligaciones de identificación de la LSSI debe
        revisarse si el proyecto empieza a constituir una actividad económica,
        incluso cuando el acceso siga siendo gratuito. En ese caso habrá que completar
        todos los datos de identificación y contacto legalmente exigidos.</p>
        """
    return HTMLResponse(f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex"><title>{title} — MIGA Ahorra</title>
    <style>body{{font:16px/1.75 system-ui,sans-serif;max-width:850px;margin:2.5rem auto;padding:0 1.1rem;color:#18372e;background:#f9fcfa}}
    a{{color:#087451}}h1,h2{{line-height:1.3}}h1{{font-size:2rem}}h2{{margin-top:2rem;font-size:1.25rem}}
    li{{margin:.55rem 0}}.notice{{padding:1rem 1.2rem;background:#fff0ce;border:1px solid #eed5a1;border-radius:12px}}
    .meta{{color:#52665c}}code{{background:#ebf2ed;padding:.12rem .3rem;border-radius:4px}}</style></head>
    <body><p><a href="/">← Volver a MIGA</a></p><h1>{title}</h1>{banner}
    <p class="meta">Última revisión: {updated}</p>{contents}
    <p><a href="/">Volver al inicio</a></p></body></html>""")

@app.get('/privacidad')
def privacidad():return legal_doc('privacidad')

@app.get('/aviso-legal')
def aviso():return legal_doc('legal')

@app.get('/verificar')
def verificar():return FileResponse(PUBLIC/'verificar.html',media_type='text/html')

@app.get('/recuperar')
def recuperar():return FileResponse(PUBLIC/'recuperar.html',media_type='text/html')

@app.get('/')
def homepage():return FileResponse(PUBLIC/'index.html',media_type='text/html')


# Ilustraciones locales para las 170 recetas nuevas. No hay peticiones a terceros.
@app.get('/imagenes/receta-{recipe_id:int}.svg', include_in_schema=False)
def imagen_receta(recipe_id: int):
    if recipe_id < 31 or recipe_id > 200:
        raise HTTPException(404, 'Imagen no encontrada')
    from recipe_art import recipe_image_by_id
    image = recipe_image_by_id(recipe_id)
    if image is None:
        raise HTTPException(404, 'Imagen no encontrada')
    return Response(image, media_type='image/svg+xml',
                    headers={'Cache-Control':'public, max-age=86400'})

app.mount('/',StaticFiles(directory=str(PUBLIC)),name='static')
