#!/usr/bin/env python3
"""MIGA: importa fotografías con licencias abiertas de Wikimedia Commons.

Uso en GitHub Actions (requiere Internet): python scripts/importar_fotos_reales.py
NO afirma que haya 200 fotografías verificadas: las coincidencias sin confianza
suficiente se dejan para revisión humana en data/fotos_reales_revision.json.
"""
from __future__ import annotations

import argparse
import hashlib
import html
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import unicodedata
from urllib.parse import urlparse, urlencode, quote

try:
    import requests
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:
    sys.exit('Instala primero: python -m pip install requests Pillow')

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'/'recipes.json'
MANIFEST=ROOT/'data'/'fotos_reales_revision.json'
MANUAL=ROOT/'data'/'fotos_reales_manuales.json'
ASSETS=ROOT/'public'/'assets'/'fotos-reales'
SOURCE_API='https://commons.wikimedia.org/w/api.php'
USER_AGENT='MIGAAhorra-PhotoImporter/1.0 (photograph credit; contact: migaahorra@gmail.com)'

# Tratar la búsqueda como sugerencias: no asignar imágenes con nombres demasiado genéricos.
TRANSLATION={
 'pollo':'chicken','pavo':'turkey','lomo':'pork','cerdo':'pork','carne':'beef','huevo':'egg','huevos':'eggs',
 'merluza':'hake','atun':'tuna','pescado':'fish','arroz':'rice','lentejas':'lentils','lenteja':'lentil',
 'garbanzos':'chickpeas','garbanzo':'chickpea','alubias':'beans','alubia':'bean',
 'espaguetis':'spaghetti','macarrones':'macaroni','pasta':'pasta','pizza':'pizza',
 'patatas':'potatoes','patata':'potato','espinacas':'spinach','espinaca':'spinach',
 'brocoli':'broccoli','calabacin':'zucchini','calabacines':'zucchini','champinones':'mushrooms',
 'champiñones':'mushrooms','setas':'mushrooms','pimiento':'pepper','pimientos':'peppers',
 'zanahoria':'carrot','zanahorias':'carrots','guisantes':'peas','maiz':'corn','tomate':'tomato',
 'tomates':'tomatoes','verduras':'vegetables','verdura':'vegetable','ensalada':'salad',
 'sopa':'soup','crema':'soup','pure':'puree','guiso':'stew','guisadas':'stew',
 'estofado':'stew','estofadas':'stew','salteado':'stir fry','salteada':'stir fry',
 'tortilla':'omelette','revuelto':'scrambled eggs','gratinado':'gratin',
 'rellenas':'stuffed','rellenos':'stuffed','al':'','con':'','de':'','y':'','en':'',
 'yogur':'yogurt','avena':'oatmeal','platano':'banana','manzana':'apple',
 'limon':'lemon','cebolla':'onion','ajo':'garlic','queso':'cheese',
 'horno':'roast','asado':'roast','asada':'roast','asadas':'roast',
 'tostada':'toast','tostadas':'toast','bocadillo':'sandwich','bol':'bowl',
 'bowl':'bowl','fajitas':'fajitas','wrap':'wrap','chili':'chili','curry':'curry',
}
STOP=set('a la el los las un una de del con por y al en para sin ligera ligero casero casera suave suaves rapido rapida faciles facil al horno a los'.split())
STOP_EN=set('a the and for in with of on to baked from homemade delicious easy healthy grilled light roast at no'.split())
FOOD_WORDS=set('chicken turkey pork beef egg eggs hake tuna fish rice lentils lentil chickpeas chickpea beans bean spaghetti macaroni pasta pizza potatoes potato spinach broccoli zucchini mushrooms mushroom peppers pepper carrot carrots peas corn tomato tomatoes vegetables salad soup puree stew stir fry omelette scrambled gratin stuffed yogurt oatmeal banana apple onion garlic lemon cheese fajitas wrap chili curry sandwich toast bowl'.split())
LICENCES=(('cc by-sa','https://creativecommons.org/licenses/by-sa/'),('cc by','https://creativecommons.org/licenses/by/'),('cc0','https://creativecommons.org/publicdomain/zero/'),('public domain','https://creativecommons.org/publicdomain/mark/'))
BAD_WORDS=set('drawing illustration sketch vector svg icon ai generated ai-generated midjourney stable diffusion dalle stock render diagram cookbook book poster infographic collage art painting logo screenshot'.split())

class Strip(HTMLParser):
 def __init__(self):super().__init__();self.words=[]
 def handle_data(self, data):self.words.append(data)
def strip_tags(v):
 p=Strip();p.feed(str(v or ''));return html.unescape(' '.join(' '.join(p.words).split())).strip()[:160]
def norm(text):
 return ''.join(c for c in unicodedata.normalize('NFKD', str(text).lower()) if not unicodedata.combining(c))
def toks(v):return re.findall(r'[a-z]{3,}', norm(v))
def dish_terms(recipe):
 terms=[TRANSLATION.get(t,t) for t in toks(recipe['title']) if t not in STOP]
 return list(dict.fromkeys(t for v in terms for t in v.split() if t not in STOP_EN and len(t)>=3))
def search_terms(recipe):
 words=dish_terms(recipe)
 # Usar ingredientes cruciales y plato principal; no confiar en resultados genéricos.
 en=' '.join(words[:5])
 es=' '.join(w for w in toks(recipe['title']) if w not in STOP)[:80]
 queries=[en,es]
 if len(words)>3:queries.append(' '.join(words[:3]))
 return list(dict.fromkeys(q for q in queries if q.strip()))

def get_json(session,params,max_attempts=3):
 for n in range(max_attempts):
  try:
   resp=session.get(SOURCE_API,params=params,timeout=35)
   if resp.status_code in (429,500,502,503,504):
    time.sleep(3*(n+1));continue
   resp.raise_for_status()
   return resp.json()
  except (requests.RequestException, ValueError):
   if n==max_attempts-1:return {}
   time.sleep(2*(n+1))
 return {}

def find_candidates(session, query):
 params={'action':'query','generator':'search','gsrsearch':query+' filetype:bitmap',
   'gsrnamespace':6,'gsrlimit':20,'prop':'imageinfo','iiprop':'url|mime|size|extmetadata',
   'iiurlwidth':1100,'format':'json','formatversion':'2'}
 data=get_json(session,params)
 return [p for p in data.get('query',{}).get('pages',[]) if p.get('imageinfo')]

def license_ok(meta):
 ln=strip_tags(meta.get('LicenseShortName',{}).get('value','')).lower().replace('-', ' ').replace('_', ' ')
 u=str(meta.get('LicenseUrl',{}).get('value','')).lower()
 if any(t in ln for t in ('noncommercial','no derivatives',' cc nc',' cc nd')):return False
 if 'creativecommons.org/licenses/by-nc' in u or 'creativecommons.org/licenses/by-nd' in u:return False
 if 'cc by sa' in ln or 'cc by-sa' in ln:
  return 'creativecommons.org/licenses/by-sa/' in u
 if 'cc by' in ln:
  return 'creativecommons.org/licenses/by/' in u
 if 'cc0' in ln or 'cc zero' in ln:
  return 'creativecommons.org/publicdomain/zero/' in u
 if 'public domain' in ln:
  return not u or 'creativecommons.org/publicdomain/mark/' in u
 return False

def photo_score(recipe, page):
 info=page['imageinfo'][0];name=page.get('title','').removeprefix('File:')
 desc=strip_tags(info.get('extmetadata',{}).get('ImageDescription',{}).get('value',''))[:250]
 txt=set(toks(name+' '+desc))
 words=dish_terms(recipe)
 matched=[w for w in words if w in txt or (w.endswith('s') and w[:-1] in txt)]
 core=[x for x in words if x in FOOD_WORDS]
 # Evitar fotos de un ingrediente crudo cuando la receta describe plato preparado.
 if len(matched)==0:return -100
 if len(core)>=2 and not set(core).intersection(matched):return -100
 first=words[0] if words else ''
 score=4*len(matched)+(5 if first in matched else 0)
 if len(matched)>=2:score+=4
 fname=set(toks(name))
 score+=2*len(fname.intersection(matched))
 forbidden={'raw','uncooked','ingredients','supermarket','package','packaging','logo','drawing','illustration','sketch','artwork','vector','painting','render'}
 if any(f in txt for f in forbidden):score-=15
 if all(w in txt for w in words[:min(2,len(words))]):score+=2
 return score

def select_candidate(recipe, pages, used):
 result=[]
 for page in pages:
  inf=page['imageinfo'][0];m=inf.get('extmetadata',{})
  img_url=inf.get('thumburl') or inf.get('url','')
  host=urlparse(img_url).hostname or ''
  if host not in {'upload.wikimedia.org','thumb.wikimedia.org'}:continue
  if not license_ok(m):continue
  if inf.get('mime') not in ('image/jpeg','image/png','image/webp'):continue
  if min(int(inf.get('width') or 0),int(inf.get('height') or 0)) < 420:continue
  url=inf.get('descriptionurl') or 'https://commons.wikimedia.org/wiki/'+quote(page['title'].replace(' ','_'),safe=':/_')
  if url in used:continue
  desc=strip_tags(m.get('ImageDescription',{}).get('value',''))
  filetext=norm(page['title']+' '+desc)
  if any(phrase in filetext for phrase in ['generated by ai','artificial intelligence','midjourney','stable diffusion','dall-e']):continue
  score=photo_score(recipe,page)
  if score>=12:result.append((score,page))
 return sorted(result,key=lambda q:q[0],reverse=True)

def safe_photo_download(session, url):
 response=session.get(url,timeout=50,stream=True,allow_redirects=True)
 response.raise_for_status()
 if urlparse(response.url).hostname not in ('upload.wikimedia.org','thumb.wikimedia.org'):
  response.close();raise ValueError('El archivo salió de Wikimedia')
 clen=int(response.headers.get('Content-Length') or 0)
 if clen>8_000_000:raise ValueError('Foto mayor que el límite')
 raw=bytearray()
 for chunk in response.iter_content(65536):
  raw.extend(chunk)
  if len(raw)>8_000_000:raise ValueError('Foto demasiado grande')
 response.close()
 Image.MAX_IMAGE_PIXELS=25_000_000
 im=Image.open(io.BytesIO(raw))
 if im.format not in ('JPEG','PNG','WEBP'):raise ValueError('Tipo de imagen incorrecto')
 if im.width<420 or im.height<420:raise ValueError('Resolución insuficiente')
 im=ImageOps.exif_transpose(im).convert('RGB')
 im.thumbnail((1100,900),Image.Resampling.LANCZOS)
 out=io.BytesIO();im.save(out,'WEBP',quality=82,method=5)
 return out.getvalue()

def render_credits(recipes):
 rows=[]
 for r in recipes:
  if r.get('photoSource') and r.get('photoLicense'):
   source=html.escape(r['photoSource'],quote=True);license_url=html.escape(r.get('photoLicenseUrl',''),quote=True)
   author=html.escape(r.get('photoCredit','Autor indicado en Wikimedia Commons'))
   license=html.escape(r['photoLicense']); title=html.escape(r['title'])
   rows.append(f'<li><strong>{r["id"]}. {title}</strong> — {author} · <a href="{source}" target="_blank" rel="noopener noreferrer">Fuente en Commons</a> · <a href="{license_url}" target="_blank" rel="noopener noreferrer">{license}</a>. Redimensionada y convertida a WebP.</li>')
 pending=sum(not(r.get('photoSource') and r.get('photoLicense')) for r in recipes)
 text=f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Créditos de fotografías — MIGA Ahorra</title><style>body{{font:16px system-ui,Arial,sans-serif;line-height:1.6;max-width:900px;padding:24px;margin:auto;color:#20362d;background:#fbfcf8}}a{{color:#146848}}li{{margin-bottom:14px}}p{{color:#4c6258}}</style></head><body><a href="/">← Volver a MIGA</a><h1>Fotografías y licencias</h1><p>{len(rows)} recetas con fotografía de Wikimedia Commons. {pending} recetas pendientes de una fotografía verificada. Las fotografías son orientativas: los ingredientes y el emplatado pueden variar. Cada fotografía conserva la licencia indicada por su autor.</p><ol>{''.join(rows)}</ol></body></html>'''
 dest=ROOT/'public'/'creditos-fotos.html';dest.write_text(text,encoding='utf8')

def patch_credits_ui():
 app=ROOT/'public'/'app.js'
 s=app.read_text(encoding='utf8')
 # Sustitución puntual: leer el código actual y no sobreescribir otras mejoras.
 original="${r.id>30?'🎨 Imagen propia de MIGA para esta receta.':'📷 Foto ilustrativa de la receta; el resultado puede variar.'}"
 alt="${r.photoSource&&r.photoLicense?`📷 Fotografía real: <a href=\"${escapeHtml(r.photoSource)}\" target=\"_blank\" rel=\"noopener noreferrer\">${escapeHtml(r.photoCredit||'Autor en Wikimedia Commons')}</a> · ${escapeHtml(r.photoLicense)} · <a href=\"/creditos-fotos.html\" target=\"_blank\" rel=\"noopener noreferrer\">Licencias</a> · preparación orientativa.`:(r.id>30?'🎨 Imagen provisional: todavía no hay fotografía real verificada.':'📷 Imagen ilustrativa anterior, pendiente de sustituir por fotografía real.')}"
 if original in s:
  s=s.replace(original,alt)
  app.write_text(s,encoding='utf8')
 elif 'creditos-fotos.html' not in s:
  print('AVISO: no se encontró el texto de créditos de app.js. No se cambió el resto del JS.')
 index=ROOT/'public'/'index.html'
 s=index.read_text(encoding='utf8')
 if '/creditos-fotos.html' not in s:
  pos='<a href="/aviso-legal">Aviso legal</a>'
  if pos in s:s=s.replace(pos,pos+'<a href="/creditos-fotos.html">Créditos de fotos</a>')
  else:print('AVISO: no se encontró el pie de página para añadir el enlace a créditos.')
  index.write_text(s,encoding='utf8')

def main():
 parser=argparse.ArgumentParser()
 parser.add_argument('--desde',type=int,default=1)
 parser.add_argument('--hasta',type=int,default=200)
 parser.add_argument('--pausa',type=float,default=0.55)
 parser.add_argument('--max-queries',type=int,default=3)
 args=parser.parse_args()
 ASSETS.mkdir(exist_ok=True,parents=True)
 (ASSETS/'.gitkeep').touch(exist_ok=True)
 recipes=json.loads(DATA.read_text(encoding='utf8'))
 done={r.get('photoSource') for r in recipes if r.get('photoSource') and (ROOT/'public'/r.get('photo','')).is_file()}
 used={x for x in done if x}
 manual=json.loads(MANUAL.read_text(encoding='utf8')) if MANUAL.is_file() else {}
 review=json.loads(MANIFEST.read_text(encoding='utf8')) if MANIFEST.is_file() else {}
 ses=requests.Session();ses.headers.update({'User-Agent':USER_AGENT,'Accept':'application/json,image/webp,image/jpeg,image/png;q=0.8,*/*;q=0.5'})
 success=0;skipped=0;failed=0
 for r in recipes:
  rid=int(r['id']);key=str(rid)
  if rid<args.desde or rid>args.hasta:continue
  local=ROOT/'public'/r.get('photo','')
  if r.get('photoSource') and local.is_file():
   skipped+=1;continue
  print(f'[{rid:03d}/200] {r["title"]}',flush=True)
  chosen=None
  queries=[manual[key]] if key in manual else search_terms(r)[:args.max_queries]
  for q in queries:
   # La selección manual permite un nombre exacto de archivo Wikimedia Commons.
   if key in manual:
    page_title=q if q.startswith('File:') else 'File:'+q
    obj=get_json(ses,{'action':'query','titles':page_title,'prop':'imageinfo','iiprop':'url|mime|size|extmetadata','iiurlwidth':1100,'format':'json','formatversion':'2'})
    pages=obj.get('query',{}).get('pages',[])
   else:pages=find_candidates(ses,q)
   opts=select_candidate(r,pages,used)
   if key in manual and not opts:
    # La elección humana NO exime la revisión de licencia, pero evita la puntuación automática.
    opts=[]
    for p in pages:
     if 'imageinfo' in p and license_ok(p['imageinfo'][0].get('extmetadata',{})):
      opts=[(20,p)];break
   if opts:
    chosen=opts[0]
    break
   time.sleep(args.pausa)
  if chosen is None:
   failed+=1
   review[key]={'receta':r['title'],'estado':'PENDIENTE','busquedas':queries,'motivo':'Sin fotografía con licencia y coincidencia suficientes'}
   continue
  points,page=chosen
  inf=page['imageinfo'][0]; url=inf.get('thumburl') or inf.get('url')
  source=inf.get('descriptionurl') or 'https://commons.wikimedia.org/wiki/'+quote(page['title'].replace(' ','_'),safe=':/_')
  metadata=inf.get('extmetadata',{})
  lic=strip_tags(metadata.get('LicenseShortName',{}).get('value',''))
  licurl=strip_tags(metadata.get('LicenseUrl',{}).get('value',''))
  author=strip_tags(metadata.get('Artist',{}).get('value','')) or 'Autor indicado en Wikimedia Commons'
  try:
   image=safe_photo_download(ses,url)
   sha=hashlib.sha256(image).hexdigest()
   target=ASSETS/f'receta-{rid:03d}.webp'
   target.write_bytes(image)
   r['photo']=target.relative_to(ROOT/'public').as_posix()
   r['photoSource']=source
   r['photoCredit']=author
   r['photoLicense']=lic
   r['photoLicenseUrl']=licurl
   r['photoChanges']='Redimensionada; convertida a WebP'
   review[key]={'receta':r['title'],'estado':'CANDIDATA_PENDIENTE_REVISION_VISUAL', 'fuente':source,'autor':author,'licencia':lic,'licencia_url':licurl,'puntuacion':points,'archivo':r['photo'],'sha256':sha,'consulta':q}
   used.add(source);success+=1
  except Exception as exc:
   failed+=1;review[key]={'receta':r['title'],'estado':'PENDIENTE','motivo':str(exc)[:220],'busquedas':queries}
  time.sleep(args.pausa)
 DATA.write_text(json.dumps(recipes,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 MANIFEST.write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 render_credits(recipes)
 patch_credits_ui()
 print(f'TERMINADO: {success} fotografias candidatas nuevas, {skipped} existentes, {failed} pendientes.')
 print('Revisa visualmente data/fotos_reales_revision.json y cada fotografía antes de anunciar que las 200 son exactas.')
 return 0

if __name__=='__main__':raise SystemExit(main())
