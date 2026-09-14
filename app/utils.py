import re
import json
import pandas as pd
import unicodedata
from functools import wraps
from flask import session, flash, redirect, url_for

# Define HEADER_ROW config used in parsing
HEADER_ROW = 6

DEFAULT_SETTINGS = {'margen_pct': '30'}

# Gender values in the wholesale XLSX are messy
GENERO_FEMALE = ('mujer', 'femen', 'dama', 'female', 'women', 'señora', 'senora')
GENERO_MALE = ('hombre', 'mascul', 'caball', 'male', 'men', 'señor', 'senor', 'él', 'el hombre')

def _norm_genero(g):
    if not g:
        return 'Unisex'
    g = str(g).strip().lower()
    if any(k in g for k in GENERO_FEMALE):
        return 'Mujer'
    if any(k in g for k in GENERO_MALE):
        return 'Hombre'
    return 'Unisex'

def _genero_sql_conds(genero_norm):
    """Parametrized LIKE conditions matching raw genero values to a normalized bucket."""
    if genero_norm == 'Mujer':
        pats = [f'%{k}%' for k in GENERO_FEMALE]
    elif genero_norm == 'Hombre':
        pats = [f'%{k}%' for k in GENERO_MALE]
    else:
        return [], []
    conds = ['LOWER(p.genero) LIKE %s'] * len(pats)
    return conds, pats

def require_login(f):
    """Decorator to protect admin routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Iniciá sesión para continuar', 'error')
            return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated

def parse_excel(filepath):
    df = pd.read_excel(filepath, engine='openpyxl', header=None)

    TARGET_KEYWORDS = ['sku', 'nombre', 'precio', 'linea']
    best_row = HEADER_ROW
    best_score = 0
    for r in range(min(12, len(df))):
        row_vals = [str(v).strip().lower().replace('\ufffd', '') for v in df.iloc[r].tolist() if isinstance(v, str) or not pd.isna(v)]
        row_text = ' '.join(row_vals)
        score = sum(1 for kw in TARGET_KEYWORDS if kw in row_text)
        if score > best_score:
            best_score = score
            best_row = r

    headers = [str(h).strip() for h in df.iloc[best_row].tolist()]
    data = df.iloc[best_row + 1:].copy()
    data.columns = headers

    def _norm(s):
        s = str(s).strip()
        s = unicodedata.normalize('NFKD', s)
        s = s.lower()
        s = ''.join(c for c in s if not unicodedata.combining(c))
        s = s.replace('\ufffd', '')
        return re.sub(r'[^a-z0-9 ]', '', s)
    data.columns = [_norm(c) for c in data.columns]

    COLUMN_ALIASES = {
        'linea':   ['linea', 'marca', 'brand', 'marcas', 'line'],
        'nombre':  ['nombre', 'name', 'producto', 'product', 'descripcion', 'description', 'desc', 'titulo', 'title', 'item'],
        'sku':     ['sku', 'codigo', 'code', 'cod', 'id', 'referencia', 'ref', 'reference'],
        'ean':     ['ean', 'upc', 'barcode', 'codigo de barras', 'codbarra', 'cod barras', 'gtin', 'codigobarras'],
        'genero':  ['genero', 'gender', 'sexo', 'gener', 'gnero'],
        'formato': ['formato', 'format', 'presentacion', 'tamano', 'tamanio', 'tam', 'tipo', 'talla', 'size', 'volumen', 'ml'],
        'precio':  ['precio', 'price', 'precio mayorista', 'costo', 'cost', 'valor', 'precio unitario',
                     'p mayorista', 'precio_mayorista', 'p unitario', 'p neto', 'neto', 'importe',
                     'total', 'valorneto', 'precioneto'],
    }

    rename_map = {}
    found = set()
    for target, aliases in COLUMN_ALIASES.items():
        for col in data.columns:
            if col in aliases or any(a in col for a in aliases):
                rename_map[col] = target
                found.add(target)
                break

    missing = set(COLUMN_ALIASES.keys()) - found
    if missing:
        raise ValueError(
            f'No se encontraron las columnas: {", ".join(missing)}. '
            f'Columnas detectadas: {", ".join(data.columns.tolist())}. '
            f'El archivo debe tener columnas como: SKU, Nombre, Precio, Linea.'
        )

    data = data.rename(columns=rename_map)
    data = data[['linea', 'nombre', 'sku', 'ean', 'genero', 'formato', 'precio']]
    data = data.dropna(subset=['sku'])
    data['sku'] = data['sku'].astype(str).str.strip()
    data['precio'] = pd.to_numeric(data['precio'], errors='coerce').fillna(0).astype(int)
    for col in ['nombre', 'linea', 'ean', 'genero', 'formato']:
        data[col] = data[col].astype(str).str.strip()
    return data

AROMA_FAMILIES = {
    'Cítrica': ['limón', 'lima', 'naranja', 'pomelo', 'bergamota', 'mandarina', 'toronja',
                'cítrico', 'citrus', 'cidra', 'yuzu', 'neroli', 'petitgrain', 'verbena',
                'tangerina', 'clementina', 'kumquat', 'limoncello', 'citronela', 'calamansí',
                'naranja sanguina'],
    
    'Floral': ['rosa', 'jazmín', 'violeta', 'lirio', 'flor', 'gardenia', 'magnolia',
               'peonía', 'peonia', 'azahar', 'geranio', 'ylang', 'azucena', 'fresia',
               'narciso', 'jacinto', 'mimosa', 'camelia', 'loto', 'orquídea', 'tuberosa',
               'lavanda', 'madreselva', 'iris', 'clavel', 'crisantemo', 'dalia',
               'floral', 'caléndula', 'margarita', 'heliotropo', 'frangipani', 'nardos',
               'flor de loto', 'flor de almendro', 'flor de cerezo', 'osmanthus', 'pétalo',
               'amapola', 'flor de manzano', 'flor del peral'],
               
    'Amaderada': ['cedro', 'sándalo', 'pino', 'pachulí', 'patchouli', 'madera', 'vetiver',
                  'roble', 'abedul', 'caoba', 'secuoya', 'palo', 'sándal', 'guayaco',
                  'ébano', 'nogal', 'teca', 'cachemira', 'oud', 'agarwood', 'akigalawood',
                  'abeto', 'cálamo', 'ciprés', 'musgo', 'encina', 'haya', 'arce', 'bambú',
                  'maderas', 'woody', 'bois', 'bosque'],
                  
    'Oriental / Especiada': ['vainilla', 'ámbar', 'incienso', 'mirra', 'benjuí', 'canela', 'clavo',
                 'nuez moscada', 'cardamomo', 'pimienta', 'almizcle', 'resina', 'opoponax',
                 'bálsamo', 'tolú', 'estoraque', 'azafrán', 'comino', 'cúrcuma',
                 'especia', 'especiado', 'ambarado', 'oriental', 'almizcl', 'ambreta',
                 'anís', 'regaliz', 'inciens', 'tabaco', 'cumarina', 'haba tonka',
                 'cachemir', 'jengibre', 'cilantro', 'pimentón', 'masala', 'clavo de olor'],
                 
    'Fougère / Aromática': ['fougère', 'fougere', 'helecho', 'musgo de roble', 'salvia',
                'hierba', 'herbal', 'tomillo', 'romero', 'albahaca', 'aromática', 'aromático',
                'artemisa', 'abrótano', 'ajenjo', 'absenta', 'eucalipto', 'menta', 'yerbabuena',
                'laurel', 'hoja', 'té', 'mate', 'orégano', 'estragón', 'angélica', 'hinojo'],
                
    'Chipre / Cuero': ['chipre', 'chypre', 'cuero', 'gamuz', 'gamuza', 'brea', 'alquitrán',
               'ahumado', 'phenol', 'castóreo', 'civet', 'algalia', 'coriáceo',
               'labdanum', 'jara', 'musgo de encina', 'abedul', 'leather', 'cuir', 'humo',
               'tabaco rubio', 'incienso ahumado'],
               
    'Gourmand / Frutal': ['caramelo', 'chocolate', 'café', 'miel', 'azúcar', 'praliné', 'avellana',
                 'cacao', 'dulce', 'goloso', 'gourmand', 'almendra', 'coco', 'leche',
                 'nata', 'crema', 'caramel', 'toffee', 'mazapán', 'turrón', 'chicle',
                 'algodón de azúcar', 'galleta', 'ron', 'whisky', 'licor',
                 'cereza', 'frutilla', 'fresa', 'frambuesa', 'arándano', 'mora',
                 'manzana', 'pera', 'melocotón', 'durazno', 'ciruela', 'cassis',
                 'grosella', 'piña', 'maracuyá', 'mango', 'higo', 'sandía', 'melón',
                 'fruta', 'frutal', 'kiwi', 'papaya', 'guayaba', 'lichi', 'zarzamora',
                 'champaña', 'cognac', 'amaretto'],
                 
    'Acuática / Fresca': ['mar', 'marino', 'agua', 'acuática', 'acuático', 'brisa', 'ozono',
                 'salado', 'sal', 'algas', 'calone', 'cascalone', 'hielo', 'glaciar',
                 'lluvia', 'rocío', 'fresco', 'acuoso', 'loto de agua', 'nenúfar',
                 'bambú de agua']
}

def _classify_family(notas_dict):
    families = set()
    all_notes = []
    for key in ('salida', 'corazon', 'fondo', 'general'):
        all_notes.extend(notas_dict.get(key, []))
    all_text = ' '.join(all_notes).lower()
    for family, keywords in AROMA_FAMILIES.items():
        for kw in keywords:
            if kw in all_text:
                families.add(family)
                break
    return sorted(families)

def _parse_notas_list(raw):
    raw = raw.strip().rstrip('.,;:')
    raw = re.sub(r'\s+(?:y|e)\s*$', '', raw)
    items = [x.strip().lower() for x in re.split(r'\s*,\s*|\s+y\s+|\s+e\s+', raw) if x.strip()]
    return [i for i in items if len(i) > 2 and not i.startswith('nota')]

def _extract_aromas(db, table='cosmetic_products'):
    from .db import _query
    rows = _query(db, f"SELECT sku, body_html, nombre FROM {table} WHERE body_html IS NOT NULL AND body_html != '' AND aromas IS NULL").fetchall()
    extracted = 0
    for r in rows:
        html = r['body_html']
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()
        
        notas = {'salida': [], 'corazon': [], 'fondo': [], 'general': []}
        
        block_start = re.search(r'(?:las?\s+)?(?:contiene\s+)?(?:con\s+)?notas?\s+de\s+salida', text, re.IGNORECASE)
        if block_start:
            block = text[block_start.start():]
            m_salida = re.search(r'notas?\s+de\s+salida\s*(?:son|de|:)?\s*(.+?)(?=\s*(?:las?\s+)?(?:la\s+)?(?:con\s+)?(?:y\s+)?notas?\s+d[ee]l?\s+coraz|\s*(?:las?\s+)?(?:con\s+)?(?:y\s+)?notas?\s+de\s+fondo|\s*$)', block, re.IGNORECASE)
            if m_salida: notas['salida'] = _parse_notas_list(m_salida.group(1))
            m_corazon = re.search(r'notas?\s+d[ee]l?\s+coraz[oó]n\s*(?:son|de|:|es)?\s*(.+?)(?=\s*(?:las?\s+)?(?:con\s+)?(?:y\s+)?notas?\s+de\s+fondo|\s*$)', block, re.IGNORECASE)
            if m_corazon: notas['corazon'] = _parse_notas_list(m_corazon.group(1))
            m_fondo = re.search(r'notas?\s+de\s+fondo\s*(?:son|de|:)?\s*(.+?)(?=\s*(?:<|\.\s*[A-ZÁÉÍÓÚ]|\s*\n\s*\n|\s*$))', block, re.IGNORECASE)
            if m_fondo: notas['fondo'] = _parse_notas_list(m_fondo.group(1))
        
        if not notas['salida'] and not notas['corazon'] and not notas['fondo']:
            m_familia = re.search(r'familia olfativa[\s:]*(.+?)(?:\.|\n|$)', text, re.IGNORECASE)
            if m_familia:
                notas['general'].extend(_parse_notas_list(m_familia.group(1)))
            m_notas = re.search(r'notas principales[\s:]*(.+?)(?:\.|\n|$)', text, re.IGNORECASE)
            if m_notas:
                notas['general'].extend(_parse_notas_list(m_notas.group(1)))
        
        if not any(notas.values()):
            found = set()
            text_lower = text.lower() + " " + r['nombre'].lower()
            for fam, keywords in AROMA_FAMILIES.items():
                for kw in keywords:
                    if kw in text_lower:
                        found.add(kw)
            if found:
                notas['general'] = list(found)

        if any(notas.values()):
            _query(db, f'UPDATE {table} SET aromas = %s WHERE sku = %s',
                   [json.dumps(notas, ensure_ascii=False), r['sku']])
            extracted += 1

    return extracted

def _paginate(sql, params, page, per_page):
    page = max(1, page)
    offset = (page - 1) * per_page
    data_sql = f'{sql} LIMIT %s OFFSET %s'
    data_params = list(params) + [per_page, offset]
    return data_sql, data_params, page, per_page, offset
