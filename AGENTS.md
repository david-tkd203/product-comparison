# AGENTS.md — Product Comparison

Flask + MySQL app for wholesale vs retail perfume price comparison. Docker Compose stack: `web`, `db` (MySQL 8), `tunnel` (Cloudflare).

## Run (local development)

```bash
docker compose up --build -d       # all services
docker compose up -d --no-deps web # web only after code changes
```

App at `http://localhost:5000`. MySQL takes ~10s to become healthy on first start.

## Production Deploy

### 1. Configure secrets

```bash
cp .env.production .env.production.local
# Editá .env.production.local con tus secretos reales
nano .env.production.local
```

**Cambiar obligatorio:**
- `FLASK_SECRET_KEY` — generar nueva: `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `ADMIN_PASSWORD` — contraseña fuerte para el panel
- `MYSQL_PASSWORD` — contraseña fuerte para la DB
- `MYSQL_ROOT_PASSWORD` — contraseña fuerte para root

### 2. Deploy

```bash
./deploy.sh
```

El script hace backup automático de la DB antes de deployear.

### 3. Dokploy

El `docker-compose.yml` principal ya tiene todo configurado para producción (healthchecks, límites de recursos, volumes nombrados). En Dokploy:

| Campo | Valor |
|-------|-------|
| Service Name | `web` |
| Host | `siyash.cl` (o `www.siyash.cl`) |
| Container Port | `5000` |
| HTTPS | ✅ Activado |

### 4. Cloudflare Tunnel (opcional)

El servicio `tunnel` genera automáticamente una URL pública via `trycloudflare.com`. En producción, considerá configurar un túnel permanente con un dominio propio.

Gunicorn: 1 worker, 4 threads, 300s timeout. The long timeout is intentional — Shopify syncs can take minutes. Don't lower it.

### Important: no test suite exists. Verify changes manually via the browser or `curl`.

## Database

MySQL 8 in Docker. DB `perfumes`, user `perfumes` / `perfumes`. Inits tables on first import (`init_db()` called at module load). Schema: `products`, `prices`, `imports`, `cosmetic_products`, `silk_products`, `multimarca_products`, `silk_matches`, `multimarca_matches`, `catalog_links`, `orders`.

## XLSX Upload (`/upload`)

`parse_excel()` auto-detects the header row (scans first 12 rows for `sku`/`nombre`/`precio`/`linea` keywords). Column matching is case-insensitive, accent-stripped, and accepts Spanish/English aliases (e.g. `Marca`→linea, `Codigo`→sku, `Valor Neto`→precio). When columns can't be matched, the error explicitly lists what was found vs expected.

Import date must be unique — existing date blocks the upload.

## Tunnel (Cloudflare)

The tunnel service uses `debian:bookworm-slim` (NOT `alpine` — cloudflared binary links glibc, musl crashes silently). `tunnel-entrypoint.sh` auto-installs wget + cloudflared, pipes stdout through grep for the `trycloudflare.com` URL, and writes it to the shared volume.

Volume sharing: `./tunnel_data` mounted at `/tmp/tunnel` (tunnel container, writes `url.txt`) and `/app/tunnel` (web container, reads it). Env: `TUNNEL_URL_FILE=/app/tunnel/url.txt`.

**Do not switch the tunnel image to `cloudflare/cloudflared`** — it's distroless (no shell), the entrypoint script can't run.

## Shopify Sync

Three stores, all use the public `/products.json` Shopify API (no auth needed):

| Store | Endpoint | Matching |
|-------|----------|----------|
| cosmetic.cl | `/sync-cosmetic` | Direct SKU match (COSxxxx format) |
| silkperfumes.cl | `/sync-silk` | Fuzzy name matching (~30% match rate) |
| multimarcasperfumes.cl | `/sync-multimarca` | Fuzzy name matching (~22% match rate) |

Sync scrapes 250 products/page. The fuzzy matcher (`_match_by_name`) uses token-based narrowing + `SequenceMatcher` with 0.70 threshold. First sync can take minutes. Aroma extraction (`_extract_aromas`) parses fragrance notes from product descriptions via regex.

## Key Routes

| Route | Purpose |
|-------|---------|
| `/` | Wholesale product list with search/filter |
| `/upload` | XLSX import (GET form, POST process) |
| `/compare` | Month-over-month price diff |
| `/retail` | Side-by-side wholesale vs 3 retailers, margin calc |
| `/catalogo` | Internal retail catalog (view only) |
| `/catalogo/config` | Generate public catalog link with margin % |
| `/c/<token>` | **Public catalog** — client browses with margin prices and places orders |
| `/pedidos` | View client orders (name, phone, items, status) |
| `/estudio` | Market study dashboard (brand stats, opportunities) |
| `/retail/export` | Export retail comparison to CSV |
| `/sync-cosmetic`, `/sync-silk`, `/sync-multimarca` | Trigger Shopify sync |
