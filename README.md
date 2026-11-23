# SkillGrow

Aplicación de orientación para postulantes a puestos de tecnología. Angular presenta los perfiles y las rutas formativas. FastAPI valida las evidencias y calcula las brechas. PostgreSQL conserva cuentas, consentimientos, intentos, diagnósticos y avances. La arquitectura editable está en [docs/Arquitectura-SkillGrow.drawio](docs/Arquitectura-SkillGrow.drawio).

## Recorrido de un postulante

1. Crea una cuenta y registra tu consentimiento. En **Mi perfil** puedes retirarlo y cerrar todas tus sesiones.
2. Elige el puesto. Sube un CV con texto seleccionable y confirma las menciones que reconoces. La carga es opcional. El sistema procesa el documento en memoria y guarda el nombre del archivo, las coincidencias y su confirmación.
3. Declara tus niveles entre 0 y 5 y responde el cuestionario del perfil. Un campo vacío indica evidencia ausente. Una respuesta incorrecta sí forma parte de la calificación.
4. Genera el diagnóstico. Con ambas fuentes disponibles, el nivel equivale al 30 % de la autodeclaración y al 70 % del cuestionario. Con autodeclaración sola, el nivel es provisional. Una mención de CV aporta cobertura documental. Una competencia sin nivel queda fuera del promedio.
5. En **Ruta formativa**, selecciona hasta tres sugerencias ordenadas por peso de la competencia × brecha relativa. Registra tu avance en **Seguimiento**. Para medir cambios de nivel, responde otro cuestionario.

## Desarrollo local

Se necesita Python 3.13 o 3.14 y Node.js 22.12 o superior compatible con Angular 21. Desde la carpeta del repositorio, abre dos terminales. En PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

```powershell
cd frontend
npm ci
npm start
```

Abre `http://localhost:4200`. La API publica su contrato en `http://localhost:8000/docs`. En una base de desarrollo nueva existen las cuentas locales `postulante@skillgrow.pe` (clave `Demo123!`) y `admin@skillgrow.pe` (clave `Admin123!`). La compilación de producción utiliza el mismo dominio para la interfaz y la API. Una base nueva en modo producción crea únicamente el catálogo inicial.

## Despliegue

El Dockerfile compila Angular e inicia FastAPI como usuario sin privilegios. La instancia sirve la interfaz en `/`, los datos en `/api` y la comprobación de base de datos en `/api/health`. `render.yaml` define una base PostgreSQL independiente y un servicio web gratuitos en la misma región, con una clave JWT generada por Render. Comprueba los límites y el vencimiento del plan gratuito en el panel del proveedor.

Para Docker Compose crea un archivo local `.env` con `POSTGRES_PASSWORD` y `JWT_SECRET`. Genera dos valores hexadecimales independientes con `python -c "import secrets; print(secrets.token_hex(32))"`. Ejecuta `docker compose up --build -d` y abre `http://localhost:8000`. Los datos de PostgreSQL se conservan en el volumen `skillgrow_data`.

El administrador se crea desde una consola autorizada del servicio con `python -m app.admin` o con `docker compose exec app python -m app.admin`. La contraseña se solicita en la consola. Desde **Administración** puedes editar requisitos, recursos, competencias y umbral semántico, archivar o restaurar fichas y revisar las publicaciones.

## Extracción y validación

El modo predeterminado busca nombres y sinónimos del catálogo. El adaptador opcional BETO carga un directorio local mediante `BETO_MODEL_PATH`, con pesos `safetensors`, tokenizador de BETO base, PyTorch y Transformers instalados. En PostgreSQL, un administrador debe habilitar previamente `CREATE EXTENSION IF NOT EXISTS vector`. Los vectores sirven para sugerir menciones. El umbral inicial de 0,92 exige calibración con el corpus del equipo.

Desde `backend`, ejecuta `python -m pytest -q` con el entorno virtual activo. Desde `frontend`, ejecuta `npm run build`. Las pruebas de software usan datos sintéticos. La validez del instrumento, F1, relevancia de las recomendaciones y usabilidad requieren resultados del estudio con participantes.
