# AGENTS.md — Siyash Perfumería

Flask + MySQL app: **public ecommerce at siyash.cl** plus an internal admin/backoffice for wholesale price comparison. Single-file app (`app.py`) with Jinja2 templates and Tailwind CSS CDN. Monochrome Siyash brand (black/white/gray); logos in `static/img/`.

## Production Deploy (Dokploy)

**Push to main redeploys automatically.** Dokploy on the Contabo VPS (213.136.67.132) reads `docker-compose.yml`, builds, and routes via Traefik.

- `siyash.cl` / `www.siyash.cl` DNS A records already point to `213.136.67.132`
- The domain is added in the Dokploy UI (Domains tab)
- Dokploy handles SSL automatically (no manual nginx or Certbot needed)
- **Do not run nginx on the host** — Dokploy has its own reverse proxy

`docker-compose.yml` is the file Dokploy uses. It intentionally uses `expose: ["80"]` (no host bind) so Traefik routes internally.

### Fallback manual deploy

If you need to deploy manually (bypass Dokploy):

```bash
./deploy.sh          # uses docker-compose.yml + .env.production
```

The script auto-backs up the DB before deploying and uses `--no-cache` builds intentionally.

## Local Development

```bash
docker compose up --build -d       # all services (web, db)
docker compose up -d --no-deps web # web only after code changes
```

MySQL takes ~10s to become healthy on first start.

## Database

MySQL 8 in Docker. DB `perfumes`, user `perfumes` / `perfumes`. Tables are created on first module import (`init_db()` runs when `app.py` is loaded). Schema: `products`, `prices`, `imports`, `cosmetic_products`, `silk_products`, `multimarca_products`, `silk_matches`, `multimarca_matches`, `catalog_links` (legacy, unused), `orders`, `users`, `settings`.

**Storefront model:** products appear on the public ecommerce only when `products.is_active = 1 AND products.stock > 0` (set from `/admin/inventario`; stock is never shown to clients). Prices shown = `wholesale price × (1 + settings.margen_pct/100)` rounded to nearest 10, computed server-side (`_tienda_sql()`). The global margin lives in the `settings` table (`margen_pct`, editable at `/admin/inventario`). Order prices are always recomputed server-side in `/api/pedido` — client-sent prices are ignored, stock is checked and decremented atomically. Web orders use `link_token = 'WEB'`.

**Gender normalization:** raw `genero` values from XLSX are messy; `_norm_genero()` maps them to `Hombre` / `Mujer` / `Unisex` (also normalized at import time).

**Note:** `DB_CONFIG` in `app.py` hardcodes `user='perfumes'` and `password='perfumes'`; only `host` is read from `MYSQL_HOST`. Changing `.env.production` credentials does not affect the app's DB login unless `app.py` is also modified.

## XLSX Upload (`/upload`)

`parse_excel()` auto-detects the header row (scans first 12 rows for `sku`/`nombre`/`precio`/`linea` keywords). Column matching is case-insensitive, accent-stripped, and accepts Spanish/English aliases (e.g. `Marca`→`linea`, `Codigo`→`sku`, `Valor Neto`→`precio`). When columns cannot be matched, the error explicitly lists what was found vs expected.

Import date must be unique — an existing date blocks the upload.

## Shopify Sync

Three stores, all using the public `/products.json` Shopify API (no auth):

| Store | Endpoint | Matching |
|-------|----------|----------|
| cosmetic.cl | `/sync-cosmetic` | Direct SKU match (`COSxxxx` format) |
| silkperfumes.cl | `/sync-silk` | Fuzzy name matching (~30% match) |
| multimarcasperfumes.cl | `/sync-multimarca` | Fuzzy name (~22% match) |

Sync scrapes 250 products/page. Fuzzy matcher (`_match_by_name`) uses token narrowing + `SequenceMatcher` with 0.70 threshold. First sync can take minutes.

Gunicorn: 1 worker, 4 threads, **300s timeout**. The long timeout is intentional — Shopify syncs can take minutes. Do not lower it.

## Important: No Test Suite

There is no automated test suite. Verify changes manually via browser or `curl`. If adding logic, test against local Docker before deploying.

## Key Routes

### Storefront (public, monochrome Siyash brand — templates in `templates/tienda/`)

| Route | Purpose |
|-------|---------|
| `/` | Home: hero, Para Él/Ella split, destacados, familias, marcas |
| `/perfumes` | Full catalog with filters: search, gender, brand, olfactory family, price range, sort |
| `/hombre`, `/mujer` | Catalog preset to Hombre / Mujer |
| `/producto/<sku>` | Product detail: image, olfactory pyramid, related items |
| `/api/pedido` | POST checkout — server-side pricing + atomic stock decrement |
| `/login` | Admin login (footer link "Acceso administración") |

### Admin/backoffice (login required)

| Route | Purpose |
|-------|---------|
| `/admin` → `/admin/productos` | Wholesale product list with search/filter |
| `/admin/inventario` | **Store inventory**: activate checkbox + stock qty + aroma family + store margin config |
| `/admin/config` | POST — updates global `margen_pct` |
| `/upload` | XLSX import (GET form, POST process) |
| `/compare` | Month-over-month price diff |
| `/retail` | Side-by-side wholesale vs 3 retailers, margin calc |
| `/catalogo` | Internal retail catalog (view only) |
| `/pedidos` | View client orders (name, phone, items, status) |
| `/estudio` | Market study dashboard (brand stats, opportunities) |
| `/retail/export` | Export retail comparison to CSV |
| `/sync-cosmetic`, `/sync-silk`, `/sync-multimarca` | Trigger Shopify sync |

**Removed:** the dynamic catalog link feature (`/catalogo/config`, `/c/<token>`) was replaced by the storefront itself at siyash.cl.
