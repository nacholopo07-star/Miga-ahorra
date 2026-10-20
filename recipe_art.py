"""Ilustraciones SVG creadas en el propio servidor para recetas del catálogo.
No son fotografías y no utilizan servicios externos ni datos del usuario.
"""
import hashlib, html, math, random

def svg_for_recipe(recipe):
    # Ilustración editorial, no fotografía. No carga proveedores externos ni imágenes no verificadas.
    title=html.escape(recipe['title']);cat=recipe['category']; color={'normal':('#f0aa7b','#a94e30'),'saludable':('#afd7c6','#237a63'),'vegana':('#d8e6a2','#528a42'),'sin-gluten':('#f5d5a5','#95663a')}[cat]
    # Plato y alimentos en estilo de dibujo vectorial sencillo; acento ajustado a nombre.
    seed=int(hashlib.sha256(recipe['title'].encode('utf8')).hexdigest()[:8],16)
    rng=random.Random(seed)
    xs=[]
    for i in range(18):
        angle=i*2*math.pi/18; rr=rng.randint(30,137);x=400+math.cos(angle)*rr; y=237+math.sin(angle)*rr*.7
        fill=['#dd8c44','#5fa367','#e7ca72','#dd5943','#a8b46e','#ede4ba','#6f9962'][rng.randrange(7)]
        xs.append(f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="{rng.randrange(11,28)}" ry="{rng.randrange(10,24)}" transform="rotate({rng.randrange(0,180)} {x:.1f} {y:.1f})" fill="{fill}" opacity=".92"/>')
    text=html.escape(recipe['title'])
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 550" role="img" aria-label="Ilustración de {title}"><defs><linearGradient id="a" x2="1" y2="1"><stop stop-color="{color[0]}"/><stop offset="1" stop-color="{color[1]}"/></linearGradient><filter id="b" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="14"/></filter></defs><rect width="800" height="550" fill="url(#a)"/><circle cx="110" cy="92" r="220" fill="#fff" opacity=".1"/><circle cx="700" cy="480" r="190" fill="#fff" opacity=".1"/><ellipse cx="410" cy="247" rx="218" ry="168" fill="#000" opacity=".14" filter="url(#b)"/><ellipse cx="400" cy="234" rx="215" ry="167" fill="#fffdf6"/><ellipse cx="400" cy="234" rx="188" ry="141" fill="#f5ead6"/><ellipse cx="400" cy="234" rx="167" ry="126" fill="#e3d8b8"/>'''+''.join(xs)+f'''<rect x="0" y="425" width="800" height="125" fill="#0e2b20" opacity=".88"/><text x="34" y="455" fill="#e5f5d6" font-family="Arial,sans-serif" font-size="16" font-weight="700" letter-spacing="2">MIGA AHORRA · ILUSTRACIÓN</text><text x="34" y="496" fill="#fff" font-family="Arial,sans-serif" font-size="29" font-weight="700">{title[:41]}</text></svg>'''



from functools import lru_cache

@lru_cache(maxsize=200)
def recipe_image_by_id(recipe_id):
    import legacy_core as core
    rec = next((r for r in core.recipe_catalog() if r['id']==recipe_id), None)
    return svg_for_recipe(rec) if rec is not None else None
