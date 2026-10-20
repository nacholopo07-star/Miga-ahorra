#!/usr/bin/env python3
"""Auditoría de imágenes de MIGA: rutas locales, licencias y pendientes."""
import json
from pathlib import Path
from urllib.parse import urlsplit

root = Path(__file__).resolve().parents[1]
recipes = json.loads((root / 'data/recipes.json').read_text(encoding='utf-8'))
results = {'total':len(recipes),'fotos_reales_verificables':0,'ilustraciones_o_provisionales':0,'foto_principal_inexistente':[],'alternativa_inexistente':[],'sin_creditos':[],'pendientes_revision_visual':[]}
for recipe in recipes:
    rid = recipe['id']
    def exists(relative):
        path = urlsplit(str(relative or '')).path.lstrip('/')
        if not path or '..' in Path(path).parts: return False
        return (root / 'public' / path).is_file()
    if not exists(recipe.get('photo')): results['foto_principal_inexistente'].append(rid)
    if not exists(recipe.get('fallback')): results['alternativa_inexistente'].append(rid)
    if recipe.get('photoSource'):
        if not (recipe.get('photoCredit') and recipe.get('photoLicense') and recipe.get('photoLicenseUrl')):
            results['sin_creditos'].append(rid)
        if exists(recipe.get('photo')) and recipe.get('photoCredit') and recipe.get('photoLicense'):
            results['fotos_reales_verificables'] += 1
            results['pendientes_revision_visual'].append(rid)
    else: results['ilustraciones_o_provisionales'] += 1
report = root / 'data/diagnostico_imagenes.json'
report.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(results,ensure_ascii=False,indent=2))
print('Informe guardado en data/diagnostico_imagenes.json')
