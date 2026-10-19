"""Copias SQLite consistentes. En producción es necesario duplicarlas FUERA del servidor."""
from pathlib import Path
from datetime import datetime, timezone
import argparse, os, sqlite3, time

def make_backup(db_path:Path, target_dir=None, keep=14):
    db_path=Path(db_path)
    if not db_path.is_file():return None
    destination=Path(target_dir or os.getenv('MIGA_BACKUP_DIR',db_path.parent/'backups'))
    destination.mkdir(parents=True,exist_ok=True)
    date=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    output=destination / f'miga-{date}.sqlite3'
    source=sqlite3.connect(f'file:{db_path}?mode=ro',uri=True,timeout=20)
    try:
        with sqlite3.connect(output) as target:
            source.backup(target)
    finally:source.close()
    output.chmod(0o600)
    files=sorted(destination.glob('miga-*.sqlite3'),reverse=True)
    for old in files[keep:]:old.unlink()
    return output

def backup_loop(db_path):
    # Ejecutar una copia al arrancar y otra cada 24h si el servicio está activo.
    while True:
        try:
            result=make_backup(db_path)
            if result:print('Copia de seguridad local guardada:',result.name)
        except (OSError,sqlite3.Error) as e:
            print('ATENCIÓN: la copia local no ha podido completarse:',type(e).__name__)
        time.sleep(86400)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',default=os.getenv('MIGA_DATA_DIR','data')+'/miga.sqlite3');p.add_argument('--dest',default=None)
    a=p.parse_args();print(make_backup(Path(a.db),a.dest) or 'Base de datos no encontrada')
