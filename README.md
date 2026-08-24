# Siyash Perfumería

Plataforma de e-commerce y panel administrativo (Flask + MySQL) para Siyash Perfumería (siyash.cl). 
Incluye una tienda pública orientada al cliente y un robusto backoffice para la gestión de inventario, importación de precios y análisis de mercado.

## Características Principales

### Tienda Pública (E-commerce)
- Catálogo de +13.000 fragancias con filtros por marca, género y familia olfativa.
- Visualización elegante con marcas dinámicas.
- Motor de cálculo de precio de venta automático basado en el costo mayorista y el margen global.
- Seguridad anti-DDoS, protección CSRF y rate-limiting en rutas sensibles.

### Backoffice (Administrador)
- **Inventario**: Control absoluto de qué productos aparecen en la tienda y su stock disponible.
- **Importación masiva**: Carga de catálogos mayoristas vía archivos Excel.
- **Comparativa y Estudio de Mercado**: Análisis automatizado de competencia frente a grandes retailers (Cosmetic, Silk, Multimarca).
- **Control de pedidos**: Gestión centralizada de las órdenes de los clientes.

## Arquitectura y Stack
- **Backend**: Python 3.11, Flask, Gunicorn
- **Base de Datos**: MySQL 8.0 (con persistencia en volúmenes Docker)
- **Frontend**: Jinja2 + Tailwind CSS (monocromático estilo Siyash)
- **Despliegue**: Docker Compose optimizado para Dokploy con routing dinámico vía Traefik.

## Despliegue en Producción
El proyecto está diseñado para desplegarse automáticamente a través de **Dokploy** en el VPS.
Simplemente haz un push a la rama `main` y Dokploy se encargará de levantar los contenedores `web` y `db`, asignar los certificados SSL y enrutar el dominio `siyash.cl`.

## Desarrollo Local
```bash
# Levantar los contenedores
docker compose up --build -d

# Acceder a la tienda
http://localhost:80

# Acceder al administrador
http://localhost:80/login
```
