#!/bin/bash
set -e

echo "=== Product Comparison — Setup ==="

# Install Docker
if ! command -v docker &>/dev/null; then
    echo "Instalando Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo "Docker instalado. Reiniciá la sesión si es la primera vez."
fi

# Install Docker Compose plugin
if ! docker compose version &>/dev/null; then
    echo "Instalando Docker Compose..."
    sudo apt-get update -qq && sudo apt-get install -y docker-compose-plugin
fi

# Build and start
echo ""
echo "Construyendo y levantando contenedores..."
docker compose up --build -d

# Wait for MySQL to be ready
echo "Esperando a que MySQL esté listo..."
sleep 10

# Seed initial data if Excel is present
if [ -f "lista mayorista junio julio.xlsx" ]; then
    echo "Importando Excel inicial..."
    docker compose exec -T web python -c "
from app import parse_excel, _query, get_db
from datetime import date
data = parse_excel('/app/lista mayorista junio julio.xlsx', 'seed')
db = get_db()
d = date(2025, 6, 1)
count = 0
for _, row in data.iterrows():
    _query(db, 'INSERT INTO products (sku, nombre, linea, ean, genero, formato) VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE nombre=VALUES(nombre), linea=VALUES(linea), ean=VALUES(ean), genero=VALUES(genero), formato=VALUES(formato)', [row['sku'], row['nombre'], row['linea'], row['ean'], row['genero'], row['formato']])
    _query(db, 'INSERT INTO prices (sku, import_date, precio) VALUES (%s,%s,%s)', [row['sku'], d, row['precio']])
    count += 1
_query(db, 'INSERT INTO imports (filename, import_date, product_count) VALUES (%s,%s,%s)', ['seed.xlsx', d, count])
db.close()
print(f'OK: {count}')
" || echo "Ya tenías datos importados."
fi

echo ""
echo "Sincronizando precios de tiendas (esto puede tardar unos minutos)..."
echo "  cosmetic.cl..."
curl -s http://localhost:5000/sync-cosmetic > /dev/null
echo "  silkperfumes.cl..."
curl -s http://localhost:5000/sync-silk > /dev/null
echo "  multimarcasperfumes.cl..."
curl -s http://localhost:5000/sync-multimarca > /dev/null

echo ""
echo "=== Listo ==="
echo "Abrí http://localhost:5000 en tu navegador"
docker compose logs tunnel 2>&1 | grep -o 'https://.*trycloudflare\.com' | head -1 | while read url; do
    echo "URL pública: $url"
done
