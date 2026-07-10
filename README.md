# Product Comparison

Comparativa de precios mayoristas vs retail para perfumería. Aplicación web Flask + MySQL que permite importar listas de precios desde Excel y comparar contra precios de tiendas online (cosmetic.cl, silkperfumes.cl, multimarcasperfumes.cl).

## Funcionalidades

- **Precios**: búsqueda y filtrado de la lista mayorista con exportación XLSX
- **Subir Excel**: importación mensual de listas de precios
- **Comparar Meses**: diferencias de precio entre dos meses
- **Comparativa de Mercado**: precios lado a lado vs 4 tiendas con precio ideal y margen objetivo
- **Estudio de Mercado**: análisis financiero, márgenes por marca, top oportunidades
- **Cloudflare Tunnel**: URL pública temporal para compartir

## Stack

- Python 3.11 + Flask + Gunicorn
- MySQL 8.0
- Tailwind CSS (CDN)
- Docker + Docker Compose

## Instalación

### Linux / Mac

```bash
chmod +x setup.sh
./setup.sh
```

El script instala Docker, construye los contenedores, importa el Excel (si existe) y sincroniza las 3 tiendas.

### Windows

1. Instalá [Docker Desktop](https://www.docker.com/products/docker-desktop/)
2. Abrí PowerShell en la carpeta del proyecto:

```powershell
docker compose up --build -d
```

3. Importá el Excel desde la web (`http://localhost:5000/upload`)
4. Sincronizá las tiendas desde **Comparativa de Mercado**

### Manual

```bash
# Construir y levantar
docker compose up --build -d

# Esperar a que MySQL esté listo (~10s)
# Abrir http://localhost:5000

# Sincronizar tiendas (desde el navegador o CLI)
curl http://localhost:5000/sync-cosmetic
curl http://localhost:5000/sync-silk
curl http://localhost:5000/sync-multimarca
```

## Uso

| Pestaña | Descripción |
|---------|-------------|
| Precios | Buscar, filtrar, seleccionar y exportar a XLSX |
| Subir Excel | Cargar lista mensual (formato: Linea, Nombre, SKU, EAN, Género, Formato, Precio) |
| Comparar Meses | Ver qué productos subieron/bajaron entre dos meses |
| Comparativa | Precios vs 4 tiendas con margen objetivo configurable |
| Estudio | Análisis de mercado, rendimiento por marca, oportunidades |

## Sincronización

Las tiendas usan Shopify — la API `/products.json` es pública:

| Tienda | Método | Matches |
|--------|--------|---------|
| cosmetic.cl | SKU directo (COSxxxx) | ~97% |
| silkperfumes.cl | Fuzzy por nombre | ~30% |
| multimarcasperfumes.cl | Fuzzy por nombre | ~22% |

## Estructura

```
.
├── app.py                 # Flask app (MySQL)
├── docker-compose.yml     # Servicios: web, db (MySQL 8), tunnel (Cloudflare)
├── Dockerfile             # Python 3.11 + Gunicorn
├── setup.sh               # Instalación automatizada Linux/Mac
├── templates/             # Jinja2 + Tailwind CSS
└── requirements.txt       # Dependencias Python
```
