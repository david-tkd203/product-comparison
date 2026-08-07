#!/bin/bash
# ============================================================
#  deploy.sh — Deploy a producción
#  Uso: ./deploy.sh
# ============================================================

set -e

PROJECT_NAME="perfumeria"
COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env.production"
BACKUP_DIR="./backups"

echo "=========================================="
echo "  Deploy: $PROJECT_NAME"
echo "=========================================="

# 1. Verificar que .env.production existe
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: No existe $ENV_FILE"
    echo "Copiá .env.production y configurá los secretos."
    exit 1
fi

# 2. Crear directorios necesarios
mkdir -p "$BACKUP_DIR" uploads tunnel_data

# 3. Backup de la base de datos (si está corriendo)
if docker compose -f "$COMPOSE_FILE" ps db | grep -q "running\|Up"; then
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_FILE="$BACKUP_DIR/perfumes_${TIMESTAMP}.sql"
    echo "[1/5] Haciendo backup de la DB → $BACKUP_FILE"
    docker compose -f "$COMPOSE_FILE" exec -T db mysqldump -u root -p"${MYSQL_ROOT_PASSWORD:-rootpass}" --single-transaction --routines --triggers "$PROJECT_NAME" > "$BACKUP_FILE" || echo "Advertencia: no se pudo hacer backup"
else
    echo "[1/5] DB no está corriendo, saltando backup"
fi

# 4. Pull/build de imágenes
echo "[2/5] Build de imágenes..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build --no-cache

# 5. Levantar servicios
echo "[3/5] Levantando servicios..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d

# 6. Esperar a que la DB esté healthy
echo "[4/5] Esperando healthchecks..."
sleep 5
for i in {1..30}; do
    if docker compose -f "$COMPOSE_FILE" ps db | grep -q "healthy"; then
        echo "  DB: healthy ✓"
        break
    fi
    echo "  DB: esperando... ($i/30)"
    sleep 2
done

for i in {1..30}; do
    if docker compose -f "$COMPOSE_FILE" ps web | grep -q "healthy"; then
        echo "  Web: healthy ✓"
        break
    fi
    echo "  Web: esperando... ($i/30)"
    sleep 2
done

# 7. Verificar que la app responde
echo "[5/5] Verificando app..."
if curl -sf http://localhost:80/ > /dev/null 2>&1; then
    echo "  App responde OK ✓"
else
    echo "  Advertencia: app no responde en localhost:80"
fi

echo ""
echo "=========================================="
echo "  Deploy completado ✓"
echo "=========================================="
echo ""
echo "Servicios:"
docker compose -f "$COMPOSE_FILE" ps
