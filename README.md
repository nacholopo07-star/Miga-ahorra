# MIGA Ahorra — edición para GitHub con registro confirmado por email

MIGA incluye las **30 recetas, 47 ingredientes, Mi nevera, listas de la compra, calendario semanal, Crea tu plato, contacto, reseñas, favoritos y administración**. Esta entrega añade **verificación por correo**, reenvío del enlace, recuperación de contraseña y **envío por API HTTPS de Brevo**, que funciona en los planes económicos de Railway. La URL visible sigue llamándose MIGA Ahorra, sin números de versión.

> **Estado de publicación:** el código está preparado para GitHub, pero NO se han contratado ni configurado el alojamiento, el correo real, los textos legales ni las copias externas. El registro se abre solo cuando tú habilitas las variables de producción, revisas la documentación legal y hay un proveedor de email configurado. Esta entrega no sustituye una auditoría de seguridad.

## A. Subir a GitHub

1. Descomprime el ZIP. Entra en la carpeta resultante.
2. En tu repositorio privado `miga-ahorra`, entra en **Code → Add file → Upload files** (o `uploading an existing file` si está vacío).
3. Sube **los archivos y carpetas del interior**: `app.py`, `legacy_core.py`, `backup.py`, `Dockerfile`, `requirements.txt`, `data/`, `public/`, `README.md`, `.gitignore`, `.env.example`, etc. No subas el ZIP cerrado.
4. No subas `miga.sqlite3`, tu correo con contraseña, claves de API, archivos `.env`, copias de datos ni ficheros de usuarios. Comprueba los cambios y pulsa **Commit changes**.
5. Si tu repositorio ya tiene MIGA, los archivos con el mismo nombre se reemplazarán al confirmarlos. Conserva una copia de tu carpeta anterior antes de modificarla.

## B. Publicación en Railway

1. Crea proyecto → **Deploy from GitHub repo** → `miga-ahorra`.
2. Conecta un **Volume** al servicio montado en **`/data`**. Es indispensable para conservar SQLite tras reinicios y despliegues. **Un solo worker e instancia** (configurado en el `Dockerfile`).
3. Genera tu **dominio HTTPS** en Settings → Networking.
4. En Railway → Variables, configura las variables del archivo `.env.example` con datos reales. Sobre todo:
   - `MIGA_ENV=production`
   - `MIGA_PUBLIC_URL=https://TU-DOMINIO-REAL.up.railway.app` (la dirección exacta que obtengas)
   - `MIGA_DATA_DIR=/data`
   - `MIGA_CONTACT_EMAIL=miga.ahorra@gmail.com`
   - `MIGA_EMAIL_FROM=miga.ahorra@gmail.com` (remitente verificado en Brevo)
   - `MIGA_BREVO_API_KEY=` tu clave real de Brevo, **solo en Railway, jamás en GitHub**.
   - `MIGA_PUBLIC_REGISTRATION=1` y `MIGA_LEGAL_REVIEWED=0` **hasta terminar la revisión legal**.
5. Si quieres crear un administrador nuevo en el servicio público, añade temporalmente `MIGA_INITIAL_ADMIN_EMAIL` y `MIGA_INITIAL_ADMIN_PASSWORD` (de **16 caracteres o más**). Tras desplegar y comprobar el acceso, **borra esas variables**, no la cuenta. Si utilizas una base de datos anterior, consulta el apartado E.
6. Al terminar de revisar y personalizar las páginas `/privacidad` y `/aviso-legal`, introduce tu nombre real en `MIGA_LEGAL_NAME`, fija `MIGA_LEGAL_UPDATED` y solo entonces cambia `MIGA_LEGAL_REVIEWED=1`. Comprueba las condiciones legales en el apartado D.
7. Haz una **alta de prueba con una dirección distinta** de `miga.ahorra@gmail.com`: deberías recibir un enlace de confirmación. Hasta pulsarlo, MIGA no permitirá iniciar sesión. Comprueba también «¿Olvidaste tu contraseña?» y «Reenviar verificación».

## C. Qué hacer con miga.ahorra@gmail.com

Railway **Free, Trial y Hobby bloquean SMTP saliente**. Una contraseña de aplicación de Gmail no puede solucionar esa limitación. Para Railway recomendamos **Brevo con su API HTTPS**:

1. Entra en https://www.brevo.com/ y crea una cuenta (comprueba los límites y condiciones actuales de su plan gratuito).
2. En **Senders / Remitentes**, añade `miga.ahorra@gmail.com` y confirma el correo de verificación recibido en Gmail. Si Brevo requiere verificar un dominio propio, sigue sus instrucciones. Los correos enviados desde direcciones gratuitas pueden salir con un dominio técnico de Brevo y tener menor entregabilidad.
3. En **SMTP & API → API Keys**, crea una clave API v3.
4. Copia **tú mismo** esa clave en Railway → Variables, con nombre `MIGA_BREVO_API_KEY`. **Nunca la envíes al chat ni la incluyas en GitHub**.
5. En Railway configura `MIGA_EMAIL_FROM=miga.ahorra@gmail.com` y `MIGA_CONTACT_EMAIL=miga.ahorra@gmail.com`; usa un email verificado y autorizado para el remitente.
6. Haz una prueba de registro con otro correo y verifica que llega. Si el proveedor no acepta el envío, MIGA no creará la cuenta y mostrará un error, en vez de fingir que el correo se ha enviado.

Esto permite enviar confirmaciones, enlaces para recuperar contraseñas y avisos de contacto. **Tu Gmail es el correo de contacto**; el proveedor de correo transaccional realiza el envío automático. Más adelante conviene un dominio propio para una entrega más fiable.

**Documentación de referencia:**
- https://docs.railway.com/networking/outbound-networking
- https://developers.brevo.com/docs/send-a-transactional-email
- https://help.brevo.com/hc/es/articles/14925263522578-Cumplir-los-requisitos-de-Gmail-Yahoo-y-Microsoft-para-remitentes-de-email

## D. Privacidad y protección de usuarios, antes de abrir el registro

- Personaliza `/privacidad` y `/aviso-legal` con el **titular real**, el correo de contacto, proveedores, finalidades, bases jurídicas, destinatarios, transferencias y plazos de conservación. Los textos incluidos son una **base a revisar, NO documentación legal verificada**. NIF y dirección se publican solo cuando la normativa aplicable así lo exija; no inventes datos.
- Si usas Railway y Brevo, comprueba sus acuerdos de tratamiento, alojamiento, posible acceso o transferencias internacionales. Evalúa Google Fonts, vídeo externo, OCR y cookies antes de abrir la web a visitantes. No actives analítica/marketing sin valorar las obligaciones correspondientes.
- Las contraseñas usan PBKDF2, las sesiones en producción usan cookies HttpOnly y Secure, hay protección CSRF, límites de intentos y tokens de verificación/restablecimiento hasheados y de un único uso.
- La página de cuenta permite **exportar** los datos y **eliminar la cuenta** con contraseña. Antes de un lanzamiento amplio, revisa la moderación, las reglas de uso y la retención real de contactos y backups.
- **El indicador `MIGA_LEGAL_REVIEWED=1` es una declaración tuya**, no verifica automáticamente el cumplimiento legal. Mantén `0` hasta que todo esté preparado.

## E. Copias de seguridad y usuarios anteriores

- La base SQLite se guarda en `/data/miga.sqlite3` (el volumen de Railway). El proceso crea backups SQLite locales cada 24 horas en `/data/backups`, con rotación de 14 archivos.
- Configura en Railway **Service → Backups → Daily y Weekly**, prueba una restauración y conserva también una copia **externa** independiente: las copias del propio volumen no cubren todas las causas de pérdida. Doc.: https://docs.railway.com/volumes/backups
- **GitHub no almacena usuarios**. Al publicar el ZIP se crea una base vacía, y las cuentas de tu instalación local NO aparecerán solas. Si quieres conservarlas, deberás importar de forma privada una copia de `miga.sqlite3` al volumen `/data` antes de abrir el registro. Haz backup previamente, **no subas el archivo SQLite a GitHub**. Debe pararse el servicio al reemplazar la base.
- Una base anterior puede migrar: los usuarios existentes permanecen activos, y **las nuevas altas** requieren confirmación por email. No escales a varias réplicas con SQLite.

## F. Comprobación del registro

1. La persona rellena nombre, correo, contraseña (mínimo 12 caracteres), y acepta privacidad.
2. El sistema guarda la cuenta pendiente y envía el correo vía Brevo. Si el envío no es aceptado, revierte el alta.
3. La persona pulsa un enlace **válido por 24 horas**, que se consume una sola vez.
4. La persona inicia sesión y usa su nevera, menús, favoritos y reseñas.
5. Si no recibe el correo, entra en `/verificar` y solicita otro. La recuperación por contraseña desde `/recuperar` usa enlaces de 30 minutos.

Las entregas reales de correo y el despliegue público no pueden comprobarse hasta configurar Railway y Brevo con tus cuentas. Mantén tu repositorio privado y revisa los cargos del alojamiento y servicios.
