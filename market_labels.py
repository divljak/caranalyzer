"""Conservative research labels derived from ad titles; collector rows stay immutable."""
import re

ALIASES = {
    'Volkswagen': r'volkswagen|volksvagen|vw|wv|golf',
    'Mercedes-Benz': r'mercedes(?:[ -]?benz)?|merc(?:[ -]?benz)?',
    'BMW': r'bmw', 'Audi': r'audi', 'Volvo': r'volvo|olvo',
    'Peugeot': r'peugeot|peugoet|pezo|pežo|prugeot',
    'Škoda': r'škoda|skoda', 'Citroën': r'citroën|citroen',
    'Renault': r'renault|reno', 'Land Rover': r'land rover|range rover',
}
for _make in ['Ford','Opel','Toyota','Seat','Kia','Hyundai','Nissan','Mazda','Suzuki',
              'Fiat','Dacia','Honda','Jeep','Porsche','Mini','Mitsubishi','Tesla','Cupra',
              'Alfa Romeo','Chevrolet','Jaguar','Subaru','Lada','Geely','Changan','BYD',
              'Ssangyong','Smart','Ferrari','Maserati','MG','DS','SWM','Baic','KGM']:
    ALIASES[_make] = re.escape(_make)

SUV_MODELS = {
    'Volkswagen': {'Tiguan','Touareg','T-Roc','T-Cross','Taigo','Tayron'},
    'Volvo': {'XC40','XC60','XC90'}, 'Peugeot': {'2008','3008','5008'},
    'Audi': {'Q2','Q3','Q5','Q7','Q8','SQ5','SQ7','SQ8'},
    'BMW': {'X1','X2','X3','X4','X5','X6','X7'},
    'Škoda': {'Kodiaq','Karoq','Kamiq'}, 'Seat': {'Ateca','Arona','Tarraco'},
    'Ford': {'Kuga','Puma','Edge'}, 'Toyota': {'RAV4','C-HR'},
    'Kia': {'Sportage','Sorento','Stonic'}, 'Hyundai': {'Tucson','Santa Fe','Kona','IX35'},
    'Nissan': {'Qashqai','X-Trail','Juke'}, 'Dacia': {'Duster'},
    'Suzuki': {'Vitara','S-Cross'}, 'Mazda': {'CX-3','CX-30','CX-5','CX-60','CX-80'},
    'Mercedes-Benz': {'GLA','GLB','GLC','GLE','GLK','GLS'},
    'Porsche': {'Cayenne','Macan'}, 'Cupra': {'Formentor','Ateca','Terramar'},
}

def normalize_label(title, raw_make, raw_model):
    title = ' '.join((title or '').split())
    # Trust a recognized existing make first, otherwise only an early title match.
    make = next((m for m, pattern in ALIASES.items() if re.fullmatch(pattern, (raw_make or '').strip(), re.I)), None)
    match = None
    if make:
        match = re.search(r'\b(?:' + ALIASES[make] + r')\b', title, re.I)
    else:
        found = [(m, re.search(r'\b(?:' + p + r')\b', title, re.I)) for m,p in ALIASES.items()]
        found = [(m,v) for m,v in found if v and v.start() < 25]
        if found:
            make, match = min(found, key=lambda v:v[1].start())
    if not make:
        return {'make': None, 'model': None, 'category': None, 'category_basis': None}
    after = title[match.end():].strip(' *-_,') if match else ''
    # Golf is itself a model alias, not an actual brand token in the title.
    if match and match.group().casefold() == 'golf':
        after = 'Golf ' + after
    model = (raw_model or '').strip(' *,._-')
    patterns = {
        'Volvo': r'\b(XC[ -]?\d{2}|[SV][ -]?\d{2})\b',
        'Peugeot': r'\b(1007|2008|3008|4007|5008|107|108|206|207|208|301|307|308|407|408|508)\b',
        'BMW': r'\b(X[1-7]|[1-8](?:14|16|18|20|23|25|28|30|35|40|45|50|60)[dDiIeE]?|M[2-8]|i[3-8])\b',
        'Audi': r'\b(SQ[578]|Q[23578]|[AS][1-8]|TT)\b',
        'Volkswagen': r'\b(Golf|Passat|Tiguan|Touareg|Touran|Polo|Caddy|T[ -]?Roc|T[ -]?Cross|Taigo|Tayron|Arteon|Sharan|Jetta|Bora)\b',
        'Škoda': r'\b(Octavia|Superb|Fabia|Kodiaq|Karoq|Kamiq|Yeti|Scala|Rapid|Roomster)\b',
        'Mercedes-Benz': r'\b(GLA|GLB|GLC|GLE|GLK|GLS|CLA|CLS|[ABCEGS][ -]?(?:150|160|170|180|200|220|230|240|250|270|280|300|320|350|400|420|450|500|550|580|600))\b',
    }
    if make in patterns:
        model_match = re.search(patterns[make], after or model, re.I)
        if not model_match:
            # Ambiguous names such as Volvo XC are kept out of discovery.
            return {'make': make, 'model': None, 'category': None, 'category_basis': None}
        model = model_match.group().upper().replace(' ', '').replace('-', '')
        if make in {'Volkswagen','Škoda'}:
            model = model.title().replace('Troc','T-Roc').replace('Tcross','T-Cross')
        if make == 'Mercedes-Benz' and re.fullmatch(r'[ABCEGS]\d{3}', model):
            model = model[0] + ' ' + model[1:]
    else:
        # Only normalize known model tokens; preserve uncertain models for review.
        candidates = SUV_MODELS.get(make, set()) | {
            'Renault': {'Clio','Megane','Scenic','Captur','Kadjar'},
            'Opel': {'Astra','Insignia','Corsa','Mokka','Zafira','Grandland','Crossland'},
            'Ford': {'Focus','Fiesta','Mondeo'}, 'Toyota': {'Corolla','Yaris','Auris'},
            'Citroën': {'C3','C4','C5','Berlingo'}, 'Seat': {'Leon','Ibiza','Altea'},
            'Fiat': {'500','Panda','Punto','Tipo'}, 'Mini': {'Cooper','One'},
        }.get(make,set())
        named = next((v for v in sorted(candidates,key=len,reverse=True) if re.search(r'\b'+re.escape(v)+r'\b', after or model, re.I)), None)
        model = named or (model if re.fullmatch(r'[\w-]{1,25}',model) else None)
        if model and model.casefold() in {'drugi','vozilo','model','alfa','automobiles','renault','opel','jeep',
                                          'grand','land','c','range','rover','rent','grande','urban','space','atto'}:
            model = None
    category = basis = None
    if re.search(r'\b(avant|touring|variant|combi|karavan|sw|estate)\b', after, re.I):
        category, basis = 'estate', 'title_keyword'
    elif re.search(r'\b(sedan|limuzina)\b', after, re.I):
        category, basis = 'sedan', 'title_keyword'
    elif re.search(r'\b(hatchback|hečbek)\b', after, re.I):
        category, basis = 'hatchback', 'title_keyword'
    elif re.search(r'\bsuv\b', after, re.I):
        category, basis = 'suv', 'title_keyword'
    elif model and model.casefold() in {s.casefold() for s in SUV_MODELS.get(make,set())}:
        category, basis = 'suv', 'model_family'
    return {'make':make, 'model':model, 'category':category, 'category_basis':basis}

LABEL_DDL = """
CREATE TABLE IF NOT EXISTS dealer_vehicle_labels (
 listing_id VARCHAR(50) PRIMARY KEY REFERENCES car_listings(listing_id) ON DELETE RESTRICT,
 make VARCHAR(80), model VARCHAR(100), category VARCHAR(20), category_basis VARCHAR(30),
 source_title TEXT, source_make VARCHAR(80), source_model VARCHAR(100), version INTEGER NOT NULL DEFAULT 0
)
;
ALTER TABLE dealer_vehicle_labels ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 0
"""

def refresh_labels(connection):
    from sqlalchemy import text
    pending = connection.execute(text("""SELECT c.listing_id,c.description,c.make,c.model FROM car_listings c
      LEFT JOIN dealer_vehicle_labels n USING(listing_id) WHERE c.source='olx.ba' AND c.collection_method IN ('olx_api','olx_website')
      AND (n.listing_id IS NULL OR n.version<1 OR n.source_title IS DISTINCT FROM c.description
        OR n.source_make IS DISTINCT FROM c.make OR n.source_model IS DISTINCT FROM c.model)""")).mappings()
    data = [dict(id=r['listing_id'], source_title=r['description'], source_make=r['make'],source_model=r['model'],
                 **normalize_label(r['description'],r['make'],r['model'])) for r in pending]
    if data:
        connection.execute(text("""INSERT INTO dealer_vehicle_labels
          (listing_id,make,model,category,category_basis,source_title,source_make,source_model,version)
          VALUES (:id,:make,:model,:category,:category_basis,:source_title,:source_make,:source_model,1)
          ON CONFLICT(listing_id) DO UPDATE SET make=excluded.make,model=excluded.model,
            category=excluded.category,category_basis=excluded.category_basis,source_title=excluded.source_title,
            source_make=excluded.source_make,source_model=excluded.source_model,version=excluded.version"""), data)
