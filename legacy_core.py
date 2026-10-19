"""MIGA Ahorra: servidor HTTP + API con SQLite; solo Python 3.10+ (stdlib)."""
from __future__ import annotations
import argparse, base64, datetime as dt, getpass, hashlib, hmac, html, json, mimetypes, os, re, secrets, sqlite3, sys, time, unicodedata, smtplib, ssl
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote, quote
from collections import defaultdict
from email.message import EmailMessage

BASE = Path(__file__).resolve().parent
PUBLIC = BASE / 'public'
DATA = BASE / 'data'
DB = DATA / 'miga.sqlite3'
TIMEOUT = 30*24*3600
SESSION_TIMEOUT = 12*3600
ATTEMPTS = defaultdict(list)
CONTACT_ATTEMPTS = defaultdict(list)

def conn():
    DATA.mkdir(exist_ok=True)
    c=sqlite3.connect(DB,timeout=15)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys = ON')
    c.execute('PRAGMA busy_timeout=15000')
    return c

def setup():
    with conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', joined_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, recipe_id INTEGER NOT NULL, PRIMARY KEY(user_id,recipe_id));
        CREATE TABLE IF NOT EXISTS completed(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, recipe_id INTEGER NOT NULL, completed_at TEXT NOT NULL, PRIMARY KEY(user_id,recipe_id));
        CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, recipe_id INTEGER NOT NULL, rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), comment TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(user_id,recipe_id));
        CREATE TABLE IF NOT EXISTS plans(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, recipe_ids TEXT NOT NULL, servings INTEGER NOT NULL, budget REAL NOT NULL DEFAULT 30, category TEXT NOT NULL DEFAULT 'todos', store TEXT NOT NULL DEFAULT 'consum', saved_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS contact_messages(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL, subject TEXT NOT NULL, message TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_reviews_recipe ON reviews(recipe_id);
        CREATE TABLE IF NOT EXISTS customizations(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, recipe_id INTEGER NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(user_id,recipe_id));
        CREATE TABLE IF NOT EXISTS pantry_items(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, ingredient_id TEXT NOT NULL, quantity REAL NOT NULL CHECK(quantity >= 0), expires_at TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(user_id,ingredient_id));
        ''')
        columns={r[1] for r in c.execute('PRAGMA table_info(plans)')}
        for col,typ,default in [('budget','REAL','30'),('category','TEXT',"'todos'"),('store','TEXT',"'consum'")]:
            if col not in columns:c.execute(f'ALTER TABLE plans ADD COLUMN {col} {typ} NOT NULL DEFAULT {default}')

def now():return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')

def digest(token):return hashlib.sha256(token.encode()).hexdigest()

def make_password(passwd):
    salt=secrets.token_bytes(20); rounds=260_000
    val=hashlib.pbkdf2_hmac('sha256',passwd.encode(),salt,rounds)
    return f'pbkdf2_sha256${rounds}${base64.b64encode(salt).decode()}${base64.b64encode(val).decode()}'

def check_password(passwd,stored):
    try:
        algo,n,salt,val=stored.split('$')
        if algo!='pbkdf2_sha256':return False
        test=hashlib.pbkdf2_hmac('sha256',passwd.encode(),base64.b64decode(salt),int(n))
        return hmac.compare_digest(test,base64.b64decode(val))
    except (ValueError,TypeError):return False

def create_admin():
    setup()
    name=input('Nombre administrador: ').strip()
    email=input('Email: ').strip().lower()
    password=getpass.getpass('Contraseña (12+ caracteres): ')
    if not 2<=len(name)<=60 or not valid_email(email) or len(password)<12:
        sys.exit('Nombre, email o contraseña no válidos.')
    with conn() as c:
        try:c.execute("INSERT INTO users(name,email,password,role,joined_at) VALUES(?,?,?,'admin',?)",(name,email,make_password(password),now()))
        except sqlite3.IntegrityError:sys.exit('Ya existe una cuenta con ese email.')
    print('Cuenta de administrador creada. Puedes iniciar sesión en la web.')

def valid_email(email):return bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email)) and len(email)<=180

def notify_contact(name, address, subject, content):
    """Optional SMTP delivery. Always keep a private admin copy in SQLite."""
    host=os.getenv('MIGA_SMTP_HOST','').strip()
    user=os.getenv('MIGA_SMTP_USER','').strip()
    password=os.getenv('MIGA_SMTP_PASSWORD','')
    if not all((host,user,password)):
        return False
    port=int(os.getenv('MIGA_SMTP_PORT','587'))
    sender=os.getenv('MIGA_SMTP_FROM',user).strip()
    if not valid_email(sender):return False
    mail=EmailMessage()
    mail['Subject']='MIGA Ahorra · '+subject
    mail['From']=sender
    mail['To']='miga.ahorra@gmail.com'
    mail['Reply-To']=address
    mail.set_content('Nombre: '+name+'\nEmail: '+address+'\nAsunto: '+subject+'\n\n'+content)
    try:
        if port==465:
            with smtplib.SMTP_SSL(host,port,timeout=12,context=ssl.create_default_context()) as smtp:
                smtp.login(user,password);smtp.send_message(mail)
        else:
            with smtplib.SMTP(host,port,timeout=12) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(user,password);smtp.send_message(mail)
        return True
    except (OSError,smtplib.SMTPException,ValueError) as exc:
        print('No se pudo enviar el aviso SMTP:',type(exc).__name__)
        return False

def recipe_exists(rid):return type(rid) is int and 1<=rid<=30

def public_user(row):return {'id':row['id'],'name':row['name'],'email':row['email'],'role':row['role']}

# Paginas publicas individuales para compartir y posicionar las recetas.
def recipe_slug(name):
    plain=''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn')
    return re.sub(r'(^-+|-+$)', '', re.sub(r'[^a-z0-9]+', '-', plain.lower()))

def recipe_catalog():
    return json.loads((DATA/'recipes.json').read_text(encoding='utf-8'))

def full_url(path):
    base=os.getenv('MIGA_PUBLIC_URL', '').strip().rstrip('/')
    # Esta URL es solo para uso local hasta configurar un dominio publico.
    if not base or not base.startswith(('https://','http://')):base='http://127.0.0.1:8765'
    return base+path

def render_recipe_page(recipe):
    prices=json.loads((DATA/'prices.json').read_text(encoding='utf-8'))
    esc=lambda value:html.escape(str(value),quote=True)
    yield_count=recipe.get('servings',2)
    ingredients=[]
    cost=0.0
    for ing in recipe['ingredients']:
        p=prices.get(ing['id'])
        if not p:continue
        unit=p.get('unit','g')
        quantity=f"{ing['qty']:g}"
        description=f"{quantity} {unit} de {p['name']}"
        ingredients.append(description)
        store=p.get('stores',{}).get('consum',{})
        if p.get('pack') and store.get('price'):
            cost+=ing['qty']/p['pack']*store['price']
    slug=recipe_slug(recipe['title'])
    path='/recetas/'+slug
    image=full_url('/'+recipe['photo'])
    # Los recursos estan en /assets; las fotos de las recetas almacenan rutas relativas.
    image=full_url('/'+recipe['photo'])
    canonical=full_url(path)
    structured={
      '@context':'https://schema.org','@type':'Recipe','name':recipe['title'],
      'description':recipe['description'],'image':[image],
      'totalTime':f"PT{int(recipe['minutes'])}M",'recipeYield':f"{yield_count} raciones",
      'recipeIngredient':ingredients,
      'recipeInstructions':[{'@type':'HowToStep','position':idx+1,'text':step} for idx,step in enumerate(recipe['steps'])],
      'author':{'@type':'Organization','name':'MIGA Ahorra'},
    }
    # Evita cerrar el elemento <script> accidentalmente.
    jsonld=json.dumps(structured,ensure_ascii=False).replace('<','\\u003c')
    instructions=''.join('<li><span class="step-number">%s</span><div><p>%s</p>%s</div></li>' % (i+1,esc(step), ('<small>💡 '+esc(recipe.get('stepDetails',[{}]*len(recipe['steps']))[i].get('tip',''))+'</small>' if i<len(recipe.get('stepDetails',[])) and recipe['stepDetails'][i].get('tip') else '')) for i,step in enumerate(recipe['steps']))
    lines=''.join('<li>'+esc(x)+'</li>' for x in ingredients)
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#143b2c"><title>{esc(recipe['title'])} | MIGA Ahorra</title>
    <meta name="description" content="{esc(recipe['description'])} Aprende a hacer {esc(recipe['title'])} en {int(recipe['minutes'])} minutos, con ingredientes, cantidades y pasos claros.">
    <meta property="og:type" content="article"><meta property="og:title" content="{esc(recipe['title'])} | MIGA Ahorra"><meta property="og:description" content="{esc(recipe['description'])}"><meta property="og:image" content="{esc(image)}"><meta property="og:url" content="{esc(canonical)}">
    <meta name="twitter:card" content="summary_large_image"><link rel="canonical" href="{esc(canonical)}"><link rel="icon" href="/assets/icon.svg" type="image/svg+xml"><link rel="stylesheet" href="/recipe-page.css"><script type="application/ld+json">{jsonld}</script></head><body>
    <header><div class="container header-flex"><a href="/#inicio" class="brand">✳ MIGA<span>.</span><small>ahorra</small></a><a class="nav-back" href="/#inicio">← Ver todas las recetas</a></div></header>
    <main class="container"><div class="crumb"><a href="/#inicio">Recetas MIGA</a> / {esc(recipe['title'])}</div><section class="hero-recipe"><div class="text"><span class="pill">{esc({'normal':'Cocina casera','saludable':'Saludable','vegana':'Vegana','sin-gluten':'Sin gluten'}.get(recipe['category'],'Receta'))}</span><h1>{esc(recipe['title'])}</h1><p>{esc(recipe['description'])}</p><div class="facts"><span>⏱ {int(recipe['minutes'])} min</span><span>👥 {yield_count} raciones</span><span>🧺 Desde {cost/max(1,yield_count):.2f} €/ración*</span></div><a class="cta" href="/#inicio">Explorar más recetas →</a></div><img src="/{esc(recipe['photo'])}" alt="Fotografía ilustrativa de {esc(recipe['title'])}" width="540" height="420"></section>
    <div class="two-col"><section class="panel"><h2>Preparación paso a paso</h2><ol class="step-list">{instructions}</ol></section><aside class="panel ingredients"><h2>Ingredientes</h2><p>Para {yield_count} personas</p><ul>{lines}</ul><p class="total">Coste proporcional estimado: <strong>{cost:.2f} €</strong></p><small>El coste no incluye envases completos, transporte ni energía. Comprueba precios y alérgenos en cada tienda. Imagen ilustrativa generada.</small><a class="link" href="/#inicio">Explorar otras recetas en MIGA ↗</a></aside></div><div class="notice"><strong>Sobre esta receta:</strong> las cantidades y las calorías son orientativas. Si la necesitas sin gluten, comprueba los ingredientes certificados y evita la contaminación cruzada. El precio no es una oferta vinculante de ningún supermercado.</div></main>
    <footer class="container">MIGA Ahorra · Comer bien sin gastar de más · No afiliado a Consum ni Mercadona.</footer></body></html>"""

class App(BaseHTTPRequestHandler):
    server_version='MIGA'
    def log_message(self,fmt,*args):
        print('%s %s'%(self.address_string(),fmt%args))
    def headers_common(self,typ='application/json; charset=utf-8',status=200,cookie=None,remember=False):
        self.send_response(status)
        self.send_header('Content-Type',typ)
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','strict-origin-when-cross-origin')
        self.send_header('Cache-Control','no-store' if '/api/' in self.path else 'no-cache, must-revalidate')
        self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' https: data: blob:; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self' https://cdn.jsdelivr.net 'wasm-unsafe-eval'; worker-src 'self' blob: https://cdn.jsdelivr.net; connect-src 'self' https://cdn.jsdelivr.net; frame-src https://www.youtube-nocookie.com; media-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        if cookie is not None:
            secure='; Secure' if os.getenv('MIGA_HTTPS')=='1' else ''
            max_age=('; Max-Age='+str(TIMEOUT)) if remember else ('; Max-Age=0' if cookie=='' else '')
            self.send_header('Set-Cookie',f'miga_session={cookie}; HttpOnly; SameSite=Lax; Path=/{max_age}{secure}')
    def json(self,data,status=200,cookie=None,remember=False):
        raw=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.headers_common(status=status,cookie=cookie,remember=remember)
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers();self.wfile.write(raw)
    def fail(self,message,status=400):self.json({'error':message},status)
    def get_cookie(self):
        try:
            ck=SimpleCookie();ck.load(self.headers.get('Cookie',''))
            return ck['miga_session'].value if 'miga_session' in ck else None
        except Exception:return None
    def session(self,create=False):
        token=self.get_cookie()
        if token:
            with conn() as c:
                row=c.execute('SELECT sessions.*, users.id AS uid,users.name,users.email,users.role FROM sessions LEFT JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND expires>?',(digest(token),int(time.time()))).fetchone()
            if row:return row,None
        if not create:return None,None
        new=secrets.token_urlsafe(42);csrf=secrets.token_urlsafe(36)
        with conn() as c:
            c.execute('INSERT INTO sessions(token_hash,csrf,user_id,expires) VALUES(?,?,NULL,?)',(digest(new),csrf,int(time.time())+TIMEOUT))
        return {'uid':None,'csrf':csrf},new
    def user(self,s):
        return {'id':s['uid'],'name':s['name'],'email':s['email'],'role':s['role']} if s and s['uid'] else None
    def auth(self,admin=False):
        s,_=self.session()
        if not s or not s['uid']:
            self.fail('Inicia sesión para continuar.',401);return None
        if admin and s['role']!='admin':
            self.fail('Acceso solo para administración.',403);return None
        return s
    def parse_body(self):
        try:
            if not self.headers.get('Content-Type','').startswith('application/json'):raise ValueError('Se requiere JSON.')
            n=int(self.headers.get('Content-Length','0'))
            if n<=0 or n>16_384:raise ValueError('Petición demasiado grande o vacía.')
            v=json.loads(self.rfile.read(n))
            if not isinstance(v,dict):raise ValueError('Datos incorrectos.')
            return v
        except (ValueError,json.JSONDecodeError):
            self.fail('Datos enviados no válidos.');return None
    def csrf_check(self,s):
        if not s or not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),s['csrf']):
            self.fail('Sesión de seguridad no válida. Actualiza la página.',403);return False
        origin=self.headers.get('Origin')
        if origin:
            from urllib.parse import urlsplit
            origin_host=urlsplit(origin).netloc
            if origin_host!=self.headers.get('Host'):
                self.fail('Origen no autorizado.',403);return False
        return True
    def get_me(self,s):
        user=self.user(s)
        if not user:return {'user':None,'favorites':[],'completed':[],'plan':None,'customizations':{},'pantryItems':{}}
        with conn() as c:
            fav=[r['recipe_id'] for r in c.execute('SELECT recipe_id FROM favorites WHERE user_id=?',(user['id'],))]
            done=[r['recipe_id'] for r in c.execute('SELECT recipe_id FROM completed WHERE user_id=?',(user['id'],))]
            row=c.execute('SELECT recipe_ids,servings,budget,category,store FROM plans WHERE user_id=?',(user['id'],)).fetchone()
            custom={str(r['recipe_id']):json.loads(r['payload']) for r in c.execute('SELECT recipe_id,payload FROM customizations WHERE user_id=?',(user['id'],))}
            pantry={r['ingredient_id']:{'qty':r['quantity'],'expiry':r['expires_at'] or ''} for r in c.execute('SELECT ingredient_id,quantity,expires_at FROM pantry_items WHERE user_id=?',(user['id'],))}
        return {'user':user,'pantryItems':pantry,'favorites':fav,'completed':done,'plan':{'ids':json.loads(row['recipe_ids']),'servings':row['servings'],'budget':row['budget'],'category':row['category'],'store':row['store']} if row else None,'customizations':custom}
    def do_GET(self):
        route=urlparse(self.path)
        if route.path=='/api/version':
            # Distingue instalaciones diferentes incluso si son de la misma version.
            instance=hashlib.sha256(str(BASE.resolve()).encode('utf-8')).hexdigest()[:16]
            return self.json({'version':'publicacion','nombre':'MIGA Ahorra','nevera':True,'instancia':instance,'puerto':self.server.server_address[1]})
        if route.path=='/robots.txt':
            body=('User-agent: *\nAllow: /\nSitemap: '+full_url('/sitemap.xml')+'\n').encode('utf-8')
            self.headers_common('text/plain; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
        if route.path=='/sitemap.xml':
            pages=[full_url('/')]+[full_url('/recetas/'+recipe_slug(r['title'])) for r in recipe_catalog()]
            body=('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join('<url><loc>'+html.escape(url)+'</loc></url>' for url in pages)+'</urlset>').encode('utf-8')
            self.headers_common('application/xml; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
        if route.path.startswith('/recetas/'):
            slug=route.path.removeprefix('/recetas/').strip('/')
            recipe=next((r for r in recipe_catalog() if recipe_slug(r['title'])==slug),None)
            if recipe is None:return self.serve_404()
            body=render_recipe_page(recipe).encode('utf-8')
            self.headers_common('text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
        if route.path=='/api/bootstrap':
            s,new=self.session(create=True)
            d=self.get_me(s);d['csrf']=s['csrf'];return self.json(d,cookie=new)
        if route.path=='/api/data':
            return self.json({'recipes':json.loads((DATA/'recipes.json').read_text(encoding='utf8')),'prices':json.loads((DATA/'prices.json').read_text(encoding='utf8')),'updated':'2026-10-16'})
        if route.path=='/api/reviews':
            try:rid=int(parse_qs(route.query).get('recipe',['0'])[0])
            except ValueError:rid=0
            if not recipe_exists(rid):return self.fail('Receta no válida.')
            with conn() as c:
                reviews=[dict(r) for r in c.execute('SELECT u.name,r.rating,r.comment,r.created_at,r.user_id FROM reviews r JOIN users u ON u.id=r.user_id WHERE r.recipe_id=? ORDER BY r.created_at DESC LIMIT 60',(rid,))]
            return self.json({'reviews':reviews,'average':round(sum(r['rating'] for r in reviews)/len(reviews),1) if reviews else None,'count':len(reviews)})
        if route.path=='/api/ratings':
            with conn() as c:
                rows=[dict(r) for r in c.execute('SELECT recipe_id, ROUND(AVG(rating),1) AS average, COUNT(*) AS count FROM reviews GROUP BY recipe_id')]
            return self.json({'ratings':rows})
        if route.path=='/api/admin':
            s=self.auth(True)
            if not s:return
            with conn() as c:
                users=[dict(r) for r in c.execute("SELECT u.id,u.name,u.email,u.role,u.joined_at,COUNT(DISTINCT r.id) reviews_count,COUNT(DISTINCT f.recipe_id) favorites_count FROM users u LEFT JOIN reviews r ON u.id=r.user_id LEFT JOIN favorites f ON u.id=f.user_id GROUP BY u.id ORDER BY joined_at DESC")]
                reviews=[dict(r) for r in c.execute('SELECT r.recipe_id,r.rating,r.comment,r.created_at,u.name,u.email FROM reviews r JOIN users u ON r.user_id=u.id ORDER BY created_at DESC LIMIT 50')]
            return self.json({'users':users,'reviews':reviews})
        if route.path=='/api/admin/contactos':
            s=self.auth(True)
            if not s:return
            with conn() as c:
                contacts=[dict(row) for row in c.execute('SELECT id,name,email,subject,message,delivered,created_at FROM contact_messages ORDER BY id DESC LIMIT 100')]
            return self.json({'contacts':contacts})
        if route.path.startswith('/api/'):return self.fail('Ruta no encontrada.',404)
        path=unquote(route.path).lstrip('/') or 'index.html'
        candidate=(PUBLIC/path).resolve()
        if not candidate.is_relative_to(PUBLIC.resolve()) or not candidate.is_file():return self.serve_404()
        typ=mimetypes.guess_type(candidate.name)[0] or 'application/octet-stream'
        buf=candidate.read_bytes()
        self.headers_common(typ+'; charset=utf-8' if typ.startswith(('text/','application/javascript')) else typ)
        self.send_header('Content-Length',str(len(buf)));self.end_headers();self.wfile.write(buf)
    def serve_404(self):
        msg=b'404 - No encontrado';self.headers_common('text/plain',404);self.send_header('Content-Length',str(len(msg)));self.end_headers();self.wfile.write(msg)
    def do_POST(self):
        path=urlparse(self.path).path
        if not path.startswith('/api/'):return self.fail('Ruta no encontrada.',404)
        s,new=self.session(create=False)
        # First visit may POST without bootstrap but UI always calls bootstrap
        if not self.csrf_check(s):return
        body=self.parse_body()
        if body is None:return
        if path=='/api/contacto':
            # Abuse control plus a honeypot; public form works without authentication.
            key=self.client_address[0];ts=time.monotonic()
            CONTACT_ATTEMPTS[key]=[x for x in CONTACT_ATTEMPTS[key] if ts-x<3600]
            if len(CONTACT_ATTEMPTS[key])>=5:return self.fail('Demasiados mensajes. Inténtalo más tarde.',429)
            if str(body.get('website','')).strip():return self.json({'ok':True,'saved':True,'delivered':False})
            name=str(body.get('name','')).strip();address=str(body.get('email','')).strip().lower()
            subject=str(body.get('subject','')).strip();message=str(body.get('message','')).strip()
            if not 2<=len(name)<=80 or not valid_email(address) or subject not in ('Sugerencia','Receta','Error','Colaboración','Otro') or not 10<=len(message)<=2000 or body.get('consent') is not True:
                return self.fail('Revisa los datos del mensaje y acepta la información de privacidad.')
            CONTACT_ATTEMPTS[key].append(ts)
            with conn() as c:
                cur=c.execute('INSERT INTO contact_messages(name,email,subject,message,delivered,created_at) VALUES(?,?,?,?,0,?)',(name,address,subject,message,now()))
                mid=cur.lastrowid
            delivered=notify_contact(name,address,subject,message)
            if delivered:
                with conn() as c:c.execute('UPDATE contact_messages SET delivered=1 WHERE id=?',(mid,))
            return self.json({'ok':True,'saved':True,'delivered':delivered})
        if path in ('/api/login','/api/register'):
            key=(self.client_address[0],path);ts=time.monotonic();ATTEMPTS[key]=[x for x in ATTEMPTS[key] if ts-x<300]
            if len(ATTEMPTS[key])>=12:return self.fail('Demasiados intentos. Espera 5 minutos.',429)
            ATTEMPTS[key].append(ts)
            email=str(body.get('email','')).strip().lower();passwd=str(body.get('password','')); remember=body.get('remember') is True
            if not valid_email(email) or len(passwd)<8 or len(passwd)>256:return self.fail('Email o contraseña incorrectos.')
            with conn() as c:
                if path=='/api/register':
                    name=str(body.get('name','')).strip()
                    if len(name)<2 or len(name)>60:return self.fail('El nombre debe tener entre 2 y 60 caracteres.')
                    if c.execute('SELECT 1 FROM users WHERE email=?',(email,)).fetchone():return self.fail('Ese correo ya está registrado.',409)
                    c.execute("INSERT INTO users(name,email,password,role,joined_at) VALUES(?,?,?,'user',?)",(name,email,make_password(passwd),now()))
                    uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                else:
                    row=c.execute('SELECT id,password FROM users WHERE email=?',(email,)).fetchone()
                    if not row or not check_password(passwd,row['password']):return self.fail('Correo o contraseña incorrectos.',401)
                    uid=row['id']
                c.execute('DELETE FROM sessions WHERE token_hash=?',(digest(self.get_cookie() or ''),))
                session_key=secrets.token_urlsafe(42);csrf=secrets.token_urlsafe(36)
                c.execute('INSERT INTO sessions(token_hash,csrf,user_id,expires) VALUES(?,?,?,?)',(digest(session_key),csrf,uid,int(time.time())+(TIMEOUT if remember else SESSION_TIMEOUT)))
                row=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            d={'ok':True,'user':public_user(row),'csrf':csrf,'favorites':[],'completed':[],'plan':None,'customizations':{},'pantryItems':{}}
            # logging in: get stored data for account
            s2={'uid':uid,'name':row['name'],'email':row['email'],'role':row['role'],'csrf':csrf}
            d.update(self.get_me(s2))
            return self.json(d,cookie=session_key,remember=remember)
        if path=='/api/logout':
            with conn() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(digest(self.get_cookie() or ''),))
            return self.json({'ok':True},cookie='')
        s=self.auth()
        if not s:return
        uid=s['uid']
        if path=='/api/pantry':
            items=body.get('items')
            if not isinstance(items,list) or len(items)>120:return self.fail('La nevera tiene demasiados artículos.')
            prices=json.loads((DATA/'prices.json').read_text(encoding='utf8'))
            cleaned={}
            for item in items:
                if not isinstance(item,dict):return self.fail('Artículo incorrecto.')
                key=item.get('id');qty=item.get('qty');expiry=item.get('expiry') or ''
                if not isinstance(key,str) or key not in prices or type(qty) not in (int,float) or not 0<qty<=200000:
                    return self.fail('Ingrediente o cantidad no válida.')
                if not isinstance(expiry,str) or len(expiry)>10:return self.fail('Fecha no válida.')
                if expiry:
                    try:
                        date=dt.date.fromisoformat(expiry)
                        if date.isoformat()!=expiry:raise ValueError()
                    except ValueError:return self.fail('Fecha de caducidad no válida.')
                cleaned[key]=(float(qty),expiry)
            with conn() as c:
                c.execute('DELETE FROM pantry_items WHERE user_id=?',(uid,))
                for key,(qty,expiry) in cleaned.items():
                    c.execute('INSERT INTO pantry_items(user_id,ingredient_id,quantity,expires_at,updated_at) VALUES(?,?,?,?,?)',(uid,key,qty,expiry or None,now()))
            return self.json({'ok':True,'count':len(cleaned)})
        if path=='/api/favorite':
            rid=body.get('recipe_id');active=body.get('active')
            if not recipe_exists(rid) or not isinstance(active,bool):return self.fail('Favorito incorrecto.')
            with conn() as c:
                if active:c.execute('INSERT OR IGNORE INTO favorites(user_id,recipe_id) VALUES(?,?)',(uid,rid))
                else:c.execute('DELETE FROM favorites WHERE user_id=? AND recipe_id=?',(uid,rid))
            return self.json({'ok':True})
        if path=='/api/completed':
            rid=body.get('recipe_id')
            if not recipe_exists(rid):return self.fail('Receta no válida.')
            with conn() as c:c.execute('INSERT OR REPLACE INTO completed(user_id,recipe_id,completed_at) VALUES(?,?,?)',(uid,rid,now()))
            return self.json({'ok':True})
        if path=='/api/review':
            rid=body.get('recipe_id');rating=body.get('rating');comment=str(body.get('comment','')).strip()
            if not recipe_exists(rid) or type(rating)!=int or rating<1 or rating>5 or not 15<=len(comment)<=600:return self.fail('Valora de 1 a 5 estrellas y escribe entre 15 y 600 caracteres.')
            with conn() as c:
                if not c.execute('SELECT 1 FROM completed WHERE user_id=? AND recipe_id=?',(uid,rid)).fetchone():return self.fail('Marca la receta como cocinada antes de reseñarla.',403)
                c.execute('INSERT INTO reviews(user_id,recipe_id,rating,comment,created_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,recipe_id) DO UPDATE SET rating=excluded.rating,comment=excluded.comment,created_at=excluded.created_at',(uid,rid,rating,comment,now()))
            return self.json({'ok':True})
        if path=='/api/customization':
            rid=body.get('recipe_id'); payload=body.get('customization')
            if not recipe_exists(rid) or not isinstance(payload,dict):return self.fail('Personalización no válida.')
            removals=payload.get('remove',[]); swaps=payload.get('swap',{}); extras=payload.get('extras',{})
            if not isinstance(removals,list) or not isinstance(swaps,dict) or not isinstance(extras,dict) or len(removals)>30 or len(swaps)>30 or len(extras)>30:return self.fail('Demasiados cambios.')
            valid=set(json.loads((DATA/'prices.json').read_text('utf8')))
            recipe=next((r for r in json.loads((DATA/'recipes.json').read_text('utf8')) if r['id']==rid),None)
            original={x['id'] for x in recipe['ingredients']}
            if any(not isinstance(k,str) or k not in original for k in removals):return self.fail('Ingrediente no válido.')
            if any(k not in original or v not in valid or not isinstance(v,str) for k,v in swaps.items()):return self.fail('Sustitución no válida.')
            if any(k not in valid or type(v) not in (int,float) or not 0<v<=5000 for k,v in extras.items()):return self.fail('Extra no válido.')
            safe={'remove':removals,'swap':swaps,'extras':extras}
            with conn() as c:c.execute('INSERT INTO customizations(user_id,recipe_id,payload,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id,recipe_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at',(uid,rid,json.dumps(safe,ensure_ascii=False),now()))
            return self.json({'ok':True})
        if path=='/api/plan':
            ids=body.get('ids');servings=body.get('servings');budget=body.get('budget',30);category=body.get('category','todos');store=body.get('store','consum')
            if not isinstance(ids,list) or len(ids)!=14 or any(i is not None and not recipe_exists(i) for i in ids) or type(servings)!=int or not 1<=servings<=8:return self.fail('El menú debe tener 7 comidas y 7 cenas (14 platos), para 1 a 8 raciones.')
            if type(budget) not in (int,float) or not 5<=budget<=1000 or category not in ('todos','normal','saludable','vegana','sin-gluten') or store not in ('consum','mercadona'):return self.fail('Datos del menú incorrectos.')
            with conn() as c:c.execute('INSERT INTO plans(user_id,recipe_ids,servings,budget,category,store,saved_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET recipe_ids=excluded.recipe_ids,servings=excluded.servings,budget=excluded.budget,category=excluded.category,store=excluded.store,saved_at=excluded.saved_at',(uid,json.dumps(ids),servings,budget,category,store,now()))
            return self.json({'ok':True})
        return self.fail('Ruta no encontrada.',404)

def main():
    a=argparse.ArgumentParser();a.add_argument('--crear-admin',action='store_true');a.add_argument('--puerto',type=int,default=int(os.getenv('PORT','8000')));a.add_argument('--host',default='127.0.0.1')
    args=a.parse_args()
    setup()
    if args.crear_admin:return create_admin()
    print(f'\n  MIGA Ahorra → http://{args.host}:{args.puerto}\n  Pulsa Ctrl+C para parar\n')
    ThreadingHTTPServer((args.host,args.puerto),App).serve_forever()
if __name__=='__main__':main()
