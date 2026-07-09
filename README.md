# Product Comparison

Comparativa de precios mayoristas vs retail para perfumería. Aplicación web Flask que permite importar listas de precios desde Excel y comparar contra precios de tiendas online (cosmetic.cl, silkperfumes.cl).

## Funcionalidades

- **Precios**: búsqueda y filtrado de la lista mayorista
- **Subir Excel**: importación mensual de listas de precios
- **Comparar Meses**: diferencias de precio entre dos meses
- **Comparativa de Mercado**: precios lado a lado vs cosmetic.cl y silkperfumes.cl
- **Estudio de Mercado**: análisis financiero, márgenes por marca, oportunidades

## Stack

- Python 3.11 + Flask
- SQLite (WAL mode)
- Tailwind CSS (CDN)
- Gunicorn
- Docker

## Uso

```bash
# Desarrollo local
pip install -r requirements.txt
python app.py

# Docker
docker compose up --build -d
```

## Sincronización

Las tiendas usan Shopify — la API `/products.json` es pública. Los SKU de cosmetic.cl coinciden directamente (COSxxxx). silkperfumes.cl requiere matching por nombre.
