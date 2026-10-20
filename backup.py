"""Copias SQLite consistentes; opcionalmente se cifran y envían a Google Drive.

La subida exige tres variables: MIGA_DRIVE_BACKUP_URL, MIGA_DRIVE_BACKUP_SECRET,
MIGA_DRIVE_BACKUP_KEY. Sin ellas continúa el respaldo local en /data/backups.
La clave de cifrado debe guardarse FUERA de Railway para poder restaurar.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

HEADER = b'MIGA_BACKUP_AESGCM_V1\0'
AAD = b'MIGA-AHORRA-SQLITE-BACKUP-V1'
BACKUP_PATTERN = re.compile(r'^miga-\d{8}-\d{6}-\d{6}\.migaenc$')


def make_backup(db_path: Path, target_dir=None, keep=14):
    """Crea un snapshot consistente incluso con SQLite en modo WAL."""
    db_path = Path(db_path)
    if not db_path.is_file():
        return None
    destination = Path(target_dir or os.getenv('MIGA_BACKUP_DIR', db_path.parent / 'backups'))
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    output = destination / f'miga-{stamp}.sqlite3'
    source = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=20)
    try:
        with sqlite3.connect(output) as target:
            source.backup(target)
    finally:
        source.close()
    output.chmod(0o600)
    files = sorted(destination.glob('miga-*.sqlite3'), reverse=True)
    expiry = time.time() - 14 * 86400
    for ix, old in enumerate(files):
        if ix >= keep or old.stat().st_mtime < expiry:
            old.unlink()
    return output


def _encryption_key() -> bytes:
    value = os.getenv('MIGA_DRIVE_BACKUP_KEY', '').strip()
    try:
        key = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError('MIGA_DRIVE_BACKUP_KEY debe ser base64 válido') from exc
    if len(key) != 32:
        raise ValueError('MIGA_DRIVE_BACKUP_KEY debe decodificar a 32 bytes')
    return key


def encrypt_backup(snapshot: Path) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    contents = Path(snapshot).read_bytes()
    if not contents.startswith(b'SQLite format 3\x00'):
        raise ValueError('El archivo de origen no parece una base SQLite')
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(_encryption_key()).encrypt(nonce, gzip.compress(contents), AAD)
    return HEADER + nonce + ciphertext


def decrypt_backup(encrypted_file: Path, destination: Path) -> Path:
    """Restaura a archivo nuevo, comprobando integridad criptográfica y SQLite."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    source = Path(encrypted_file).read_bytes()
    if not source.startswith(HEADER) or len(source) < len(HEADER) + 28:
        raise ValueError('Formato de copia no reconocido')
    offset = len(HEADER)
    plain = gzip.decompress(AESGCM(_encryption_key()).decrypt(source[offset:offset + 12], source[offset + 12:], AAD))
    if not plain.startswith(b'SQLite format 3\x00'):
        raise ValueError('La copia descifrada no contiene una base SQLite válida')
    target = Path(destination)
    if target.exists():
        raise FileExistsError('No se sobrescribe ninguna base existente: ' + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.temporal')
    if temp.exists():
        raise FileExistsError('Archivo temporal existente: ' + str(temp))
    try:
        temp.write_bytes(plain)
        with sqlite3.connect(f'file:{temp}?mode=ro', uri=True) as db:
            ok = db.execute('PRAGMA quick_check').fetchone()[0]
            if ok != 'ok':
                raise ValueError('Integridad SQLite no verificada: ' + str(ok))
        temp.chmod(0o600)
        temp.rename(target)
    finally:
        if temp.exists():
            temp.unlink()
    return target


def _drive_configured() -> bool:
    keys = ('MIGA_DRIVE_BACKUP_URL', 'MIGA_DRIVE_BACKUP_SECRET', 'MIGA_DRIVE_BACKUP_KEY')
    provided = [bool(os.getenv(k, '').strip()) for k in keys]
    if any(provided) and not all(provided):
        print('ATENCIÓN: copia Drive incompleta. Faltan algunas variables MIGA_DRIVE_BACKUP_*.')
    return all(provided)


def upload_backup(snapshot: Path) -> bool:
    """Envía exclusivamente la copia cifrada. Ningún dato SQLite viaja sin cifrar."""
    url = os.environ['MIGA_DRIVE_BACKUP_URL'].strip()
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'script.google.com' or not parsed.path.endswith('/exec'):
        raise ValueError('MIGA_DRIVE_BACKUP_URL debe ser la URL /exec HTTPS de Google Apps Script')
    secret = os.environ['MIGA_DRIVE_BACKUP_SECRET'].strip()
    if len(secret) < 32:
        raise ValueError('El secreto de copia debe tener al menos 32 caracteres')
    content = encrypt_backup(snapshot)
    filename = snapshot.stem + '.migaenc'
    if not BACKUP_PATTERN.fullmatch(filename):
        raise ValueError('Nombre de copia inesperado')
    if len(content) > 7_000_000:
        raise ValueError('La copia supera el límite de 7 MB del puente Apps Script')
    request_body = json.dumps({'secret': secret, 'filename': filename,
                               'data': base64.b64encode(content).decode('ascii')},
                              separators=(',', ':')).encode('utf-8')
    req = Request(url, data=request_body, headers={'Content-Type': 'application/json'}, method='POST')
    with urlopen(req, timeout=55) as response:
        if response.status != 200:
            raise RuntimeError('HTTP inesperado del puente Drive: ' + str(response.status))
        payload = json.loads(response.read(4096).decode('utf-8'))
    if payload.get('ok') is not True:
        raise RuntimeError('El puente Drive no confirmó la copia (comprueba Apps Script)')
    print('Copia externa cifrada confirmada por Google Drive:', filename)
    return True


def backup_loop(db_path):
    """Copia local al iniciar y cada 24 horas; también Drive si está configurado."""
    while True:
        try:
            result = make_backup(Path(db_path))
            if result:
                print('Copia local SQLite guardada:', result.name)
                if _drive_configured():
                    try:
                        upload_backup(result)
                    except Exception as exc:
                        print('ATENCIÓN: no se pudo completar copia externa Drive:', type(exc).__name__, str(exc)[:180])
        except (OSError, sqlite3.Error) as exc:
            print('ATENCIÓN: copia local no completada:', type(exc).__name__)
        time.sleep(86400)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=os.getenv('MIGA_DATA_DIR', 'data') + '/miga.sqlite3')
    parser.add_argument('--dest', default=None)
    parser.add_argument('--restore-file', default=None)
    parser.add_argument('--output', default=None)
    args = parser.parse_args()
    if args.restore_file:
        if not args.output:
            parser.error('Para restaurar usa --restore-file ARCHIVO --output RUTA')
        print('Copia validada y recuperada en:', decrypt_backup(Path(args.restore_file), Path(args.output)))
    else:
        print(make_backup(Path(args.db), args.dest) or 'Base de datos no encontrada')
