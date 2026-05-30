# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARMAR MAPEO CP → BARRIO → COMUNA — DERIVADO DE DATOS OFICIALES             ║
║                                                                              ║
║  Combina dos datasets oficiales para producir, sin invención manual,        ║
║  un mapeo empírico CP4 → barrio CABA con porcentaje de confianza:           ║
║                                                                              ║
║    1. Callejero oficial GCBA  (data.buenosaires.gob.ar)                     ║
║         calle + altura inicial/final + barrio_par + barrio_imp + geometry   ║
║         · Fecha de la última actualización del dataset: 4 may 2026         ║
║         · 31.961 segmentos de calle (~16.000 calles únicas)                 ║
║         · Atribución: GCBA / Buenos Aires Data, licencia abierta.           ║
║                                                                              ║
║    2. CPA Correo Argentino  (extraído del repo OpenDataCordoba)             ║
║         calle + altura desde/hasta + CPA (formato CXXXXAAA)                 ║
║         · 2.116.009 tramos de altura (todo el país)                         ║
║         · Para CABA usamos los que tienen codloc=00005001 (CIUDAD AUTONOMA).║
║         · Atribución: Correo Argentino vía OpenDataCordoba.                 ║
║                                                                              ║
║  ESTRATEGIA                                                                  ║
║                                                                              ║
║    Paso 1. Normalizar nombre de calle en ambos datasets (uppercase, sin     ║
║            tildes, sin "AV.", "PJE.", "PASAJE", "DR.", etc.).               ║
║    Paso 2. Match por nombre normalizado: para cada calle CABA del CPA       ║
║            buscar la calle equivalente en el callejero GCBA. Match único    ║
║            cuando el nombre es idéntico tras normalización; multi-match     ║
║            cuando varias coinciden (raro en CABA, se descarta).             ║
║    Paso 3. Por cada (calle, rango de altura) del CPA cruzar con el rango   ║
║            del callejero GCBA. El barrio se asigna según el lado par/impar  ║
║            del callejero (que sabe si la altura cae en uno o varios         ║
║            barrios). Si los dos barrios (par/impar) coinciden, peso=1;      ║
║            si difieren, peso=0.5 a cada uno.                                ║
║    Paso 4. Agregar por CP4 → distribución de barrios (peso total por        ║
║            barrio). El barrio dominante es el de mayor peso; el porcentaje  ║
║            de confianza es peso_dominante / peso_total.                     ║
║    Paso 5. Cada barrio CABA pertenece a exactamente UNA comuna (relación    ║
║            fija, viene del GeoJSON barrios). Joineamos para obtener         ║
║            CP4 → barrio → comuna.                                           ║
║                                                                              ║
║  SALIDA                                                                      ║
║    data/cp_comuna.csv  — cp4, barrio, comuna, confianza_pct, n_segmentos,   ║
║                          fuente. Si confianza<60% se marca *AMBIGUO* en     ║
║                          fuente. Si no hay match en el callejero queda      ║
║                          como 'Sin clasificar' con comuna=0.                ║
║                                                                              ║
║  Uso:                                                                        ║
║    python Otras/Informe_CABA/armar_cp_barrio.py                             ║
║                                                                              ║
║  Pre-requisitos: data/callejero_gcba.csv  y  data/alturas.CSV  +            ║
║                  data/calles.CSV  +  data/localidad.CSV  presentes.         ║
║                  geo/barrios.geojson presente (para barrio→comuna).         ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys
import csv
import json
import unicodedata
import re
from pathlib import Path
from collections import defaultdict, Counter

# UTF-8 stdout
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR  = Path(__file__).resolve().parent
DATA_DIR    = SCRIPT_DIR / "data"
GEO_DIR     = SCRIPT_DIR / "geo"

CALLEJERO   = DATA_DIR / "callejero_gcba.csv"
ALTURAS     = DATA_DIR / "alturas.CSV"
CALLES_CPA  = DATA_DIR / "calles.CSV"
LOCALIDAD   = DATA_DIR / "localidad.CSV"
BARRIOS_GEO = GEO_DIR / "barrios.geojson"

OVERRIDE_CSV = DATA_DIR / "cp_comuna_override.csv"  # overrides manuales (input)
OUT_CSV     = DATA_DIR / "cp_comuna.csv"
OUT_DETAIL  = DATA_DIR / "cp_barrio_detalle.csv"  # auditoría: TODA la dist
CONFIANZA_BAJA_PCT = 60  # debajo de esto se marca *AMBIGUO* en la columna fuente


# ─── NORMALIZACIÓN DE NOMBRES DE CALLE ────────────────────────────────────────

# Prefijos / sufijos / títulos que se sacan al normalizar
_PREFIJOS_QUITAR = [
    "AV.", "AV", "AVDA.", "AVDA", "AVENIDA",
    "PJE.", "PJE", "PASAJE",
    "DR.", "DR", "DRA.", "DRA", "DOCTOR", "DOCTORA",
    "GRAL.", "GRAL", "GENERAL",
    "TTE.", "TTE", "TENIENTE",
    "CNEL.", "CNEL", "CORONEL",
    "ALTE.", "ALTE", "ALMIRANTE",
    "BRIG.", "BRIG", "BRIGADIER",
    "PRES.", "PRES", "PRESIDENTE",
    "CMTE.", "CMTE", "COMANDANTE",
    "INT.", "INT", "INTENDENTE",
    "PROF.", "PROF", "PROFESOR", "PROFESORA",
    "SAN", "SANTA", "SANTO",
    "ING.", "ING", "INGENIERO",
    "ARQ.", "ARQ", "ARQUITECTO",
    "P.", "PADRE", "PBRO.", "PBRO",
]
_SUFIJOS_QUITAR = [
    "(P)", "(I)", "(PAR)", "(IMPAR)",
]

def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))

# Palabras "ruido" que se quitan tras normalizar (típicos sufijos del callejero)
_SUFIJOS_TIPO_CALLE = {"AV","AVENIDA","CALLE","PJE","PASAJE","BV","BOULEVARD","DIAG","DIAGONAL"}

def _limpia_tokens(s: str) -> list[str]:
    s = _strip_accents(s.upper()).strip()
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
    s = s.replace(".", " ").replace("-", " ")
    toks = [t for t in s.split() if t and not t.isdigit() or len(t) >= 3]
    return toks

def _quitar_titulos(toks: list[str]) -> list[str]:
    """Quita títulos (DR., GRAL., etc.) que aparecen al principio."""
    prefijos = set(p.replace(".", "") for p in _PREFIJOS_QUITAR)
    out = toks[:]
    # Quitar títulos al principio
    while out and out[0] in prefijos and len(out) > 1:
        out = out[1:]
    # Quitar sufijos de tipo de calle al final
    while out and out[-1] in _SUFIJOS_TIPO_CALLE and len(out) > 1:
        out = out[:-1]
    return out


def normalizar_calle(nombre: str) -> str:
    """
    Genera la clave canónica para joinear callejero GCBA con calles del CPA.

    El callejero GCBA usa convención inversa al CPA:
      callejero: "ACEVEDO, EDUARDO"   "ALBERDI, JUAN BAUTISTA AV"
      CPA:        "EDUARDO ACEVEDO"    "JB ALBERDI"
    Por eso reordenamos cuando aparece coma y removemos sufijos de tipo de calle.

    Estrategia:
      1. uppercase, sin tildes, sin puntuación, sin números cortos
      2. si hay coma: invertir partes → "APELLIDO, NOMBRE TIPO" → "NOMBRE APELLIDO"
      3. quitar títulos (DR., GRAL.) y sufijos (AV, PJE)
      4. en caso de strings vacíos, devolver ""
    """
    if not nombre:
        return ""
    s = nombre.strip()
    # Si tiene coma, invertir (typical callejero notation)
    if "," in s:
        parts = [p.strip() for p in s.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            # "APELLIDO, NOMBRE TIPO" → "NOMBRE APELLIDO" (sin tipo)
            apellido, resto = parts
            resto_toks = _limpia_tokens(resto)
            resto_toks = _quitar_titulos(resto_toks)
            apellido_toks = _limpia_tokens(apellido)
            apellido_toks = _quitar_titulos(apellido_toks)
            toks = resto_toks + apellido_toks
        else:
            toks = _limpia_tokens(s)
            toks = _quitar_titulos(toks)
    else:
        toks = _limpia_tokens(s)
        toks = _quitar_titulos(toks)
    # Filtrar tokens triviales (iniciales de 1-2 letras tipo "J", "M", "N")
    toks = [t for t in toks if len(t) >= 2 or t.isdigit()]
    if not toks:
        return ""
    return " ".join(toks).strip()


def claves_match(nombre: str) -> list[str]:
    """
    Devuelve la lista de claves usables para hacer match:
      · clave 1: la normalización completa
      · clave 2: el último token (apellido principal) si tiene >1 token
    Esto permite matchear "EDUARDO ACEVEDO" (CPA) con "ACEVEDO" (callejero) si lo único
    en común es el apellido.
    """
    full = normalizar_calle(nombre)
    if not full:
        return []
    toks = full.split()
    if len(toks) == 1:
        return [full]
    return [full, toks[-1]]


# ─── CARGA DE DATASETS ────────────────────────────────────────────────────────

def cargar_callejero():
    """
    Devuelve (full_idx, last_idx) donde:
      · full_idx[clave_completa]   -> lista de segmentos
      · last_idx[apellido_solo]    -> lista de [(clave_completa, segmento)] candidatos
    Permite primero match por nombre completo, y caer al apellido sólo cuando
    es 1-a-1.

    Cada segmento: {alt_min, alt_max, longitud_m, barrio_par, barrio_imp, barrio, comuna}.
    longitud_m = longitud geométrica del segmento en metros (col 'long' del CSV).
    """
    full_idx = defaultdict(list)
    last_idx = defaultdict(list)
    with CALLEJERO.open(encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            nom = row.get("nomoficial", "")
            claves = claves_match(nom)
            if not claves:
                continue
            full = claves[0]
            last = claves[1] if len(claves) > 1 else None
            def _i(x):
                try: return int(x)
                except: return 0
            def _f(x):
                try: return float(x)
                except: return 0.0
            lo_p = _i(row.get("alt_izqini") or 0)
            hi_p = _i(row.get("alt_izqfin") or 0)
            lo_i = _i(row.get("alt_derini") or 0)
            hi_i = _i(row.get("alt_derfin") or 0)
            lo_all = min([x for x in [lo_p, lo_i] if x > 0], default=0)
            hi_all = max([hi_p, hi_i, lo_p, lo_i], default=0)
            bp  = (row.get("barrio_par") or "").strip()
            bi  = (row.get("barrio_imp") or "").strip()
            b0  = (row.get("barrio")     or "").strip()
            try:
                com = int(float(row.get("comuna") or 0))
            except Exception:
                com = 0
            seg = {
                "alt_min": lo_all, "alt_max": hi_all,
                "longitud_m": _f(row.get("long") or 0),
                "barrio_par": bp.upper() if bp else "",
                "barrio_imp": bi.upper() if bi else "",
                "barrio":     b0.upper() if b0 else "",
                "comuna":     com,
            }
            full_idx[full].append(seg)
            if last and last != full:
                last_idx[last].append((full, seg))
    return full_idx, last_idx


def cargar_calles_caba_cpa():
    """
    Devuelve dict: codcalle -> {nombre, norm_full, norm_last}
    Sólo las calles cuyo codloc = 00005001 (CABA). norm_full y norm_last se usan
    como claves de match contra el callejero GCBA.
    """
    out = {}
    with CALLES_CPA.open(encoding="latin-1") as f:
        r = csv.DictReader(f, delimiter=";")
        for row in r:
            if row.get("codloc") != "00005001":
                continue
            nombre = (row.get("nombrecalle") or "").strip().strip('"').strip()
            cc = row.get("codcalle")
            if not cc or not nombre or nombre == "*" or nombre == "TAB":
                continue
            claves = claves_match(nombre)
            if not claves:
                continue
            out[cc] = {
                "nombre":    nombre,
                "norm_full": claves[0],
                "norm_last": claves[1] if len(claves) > 1 else None,
            }
    return out


def cargar_alturas_caba(codcalles_caba):
    """
    Yields rows: (codcalle, alt_min, alt_max, cp4) sólo para CABA y rangos válidos.
    """
    with ALTURAS.open(encoding="latin-1") as f:
        r = csv.DictReader(f, delimiter=";")
        for row in r:
            cc = row.get("codcalle")
            if cc not in codcalles_caba:
                continue
            try:
                lo = int(row.get("desde") or 0)
                hi = int(row.get("hasta") or 0)
            except Exception:
                continue
            cpa = (row.get("codpostal") or "").strip().strip('"')
            if len(cpa) < 5 or cpa[0] != "C":
                continue
            cp4 = cpa[1:5]
            if not cp4.isdigit():
                continue
            yield (cc, lo, hi, cp4)


def cargar_barrio_a_comuna():
    """Lee geo/barrios.geojson y devuelve dict BARRIO_NORM (upper sin tildes) -> comuna.

    Maneja alias comunes entre el callejero ("LA BOCA") y el geojson ("BOCA"),
    y artículos ("LA") que el callejero a veces antepone.
    """
    out = {}
    with BARRIOS_GEO.open(encoding="utf-8") as f:
        g = json.load(f)
    aliases = {
        "BOCA":             ["LA BOCA"],
        "NUNEZ":            ["NUÑEZ", "NUNEZ"],
        "VILLA GRAL MITRE": ["VILLA GRAL. MITRE", "VILLA GENERAL MITRE", "VILLA GRAL MITRE"],
    }
    for feat in g["features"]:
        b = feat["properties"]["BARRIO"]
        c = int(feat["properties"]["COMUNA"])
        b_up    = b.upper()
        b_noacc = _strip_accents(b_up)
        out[b_up] = c
        out[b_noacc] = c
        # Aliases conocidos
        for k, alts in aliases.items():
            if k == b_noacc:
                for alt in alts:
                    out[alt.upper()] = c
                    out[_strip_accents(alt.upper())] = c
    return out


# ─── ALGORITMO PRINCIPAL ──────────────────────────────────────────────────────

def derivar_cp_a_barrio():
    print("[1] Cargando callejero GCBA…", flush=True)
    full_idx, last_idx = cargar_callejero()
    print(f"     {len(full_idx):,} claves principales", flush=True)
    print(f"     {len(last_idx):,} claves alternativas (apellido)", flush=True)
    n_seg = sum(len(v) for v in full_idx.values())
    print(f"     {n_seg:,} segmentos totales", flush=True)

    print("[2] Cargando calles CPA de CABA…", flush=True)
    calles_caba = cargar_calles_caba_cpa()
    print(f"     {len(calles_caba):,} codcalles únicos en CABA", flush=True)

    # Match calles CPA contra callejero GCBA
    print("[3] Matcheando calles CPA → callejero GCBA…", flush=True)
    matched_full = matched_last = unmatched = 0
    # Para cada codcalle, decidir qué clave usar (full primero, last como fallback)
    match_key = {}  # codcalle -> (key, kind)  kind ∈ {"full","last"}
    for cc, info in calles_caba.items():
        if info["norm_full"] in full_idx:
            match_key[cc] = (info["norm_full"], "full")
            matched_full += 1
        elif info["norm_last"] and info["norm_last"] in last_idx:
            # Sólo usar fallback si la calle del CPA NO es ambigua y last_idx tiene
            # una única calle "completa" para ese apellido.
            candidatos = last_idx[info["norm_last"]]
            unique_fulls = {c[0] for c in candidatos}
            if len(unique_fulls) == 1:
                match_key[cc] = (next(iter(unique_fulls)), "last")
                matched_last += 1
            else:
                unmatched += 1
        else:
            unmatched += 1
    total = matched_full + matched_last + unmatched
    pct = 100.0*(matched_full + matched_last)/total if total else 0
    print(f"     Match exacto: {matched_full:,} | por apellido: {matched_last:,} | "
          f"sin match: {unmatched:,} ({pct:.1f}% total matcheado)", flush=True)

    # Iterar alturas y acumular: (cp4, barrio) -> peso
    print("[4] Iterando alturas y atribuyendo barrios por CP4 (rango por rango)…", flush=True)
    pesos = defaultdict(lambda: Counter())
    nsegs = defaultdict(int)
    sin_seg_callejero = 0
    contados = 0
    for cc, lo, hi, cp4 in cargar_alturas_caba(set(calles_caba.keys())):
        if cc not in match_key:
            continue
        norm, _kind = match_key[cc]
        for seg in full_idx[norm]:
            s_lo, s_hi = seg["alt_min"], seg["alt_max"]
            # 1) Verificar overlap entre rango altura del CPA y rango del callejero.
            #    Si el callejero no tiene altura informada (0-0), SKIPEAMOS el voto:
            #    no podemos verificar a qué tramo de la calle pertenece el segmento.
            #    Esto descarta votos espurios en avenidas largas (DEL LIBERTADOR, etc.)
            #    cuyos segmentos "anónimos" (sin altura) cruzan múltiples barrios.
            if s_hi <= 0 and s_lo <= 0:
                continue
            ov_lo = max(lo, s_lo)
            ov_hi = min(hi, s_hi)
            if ov_hi < ov_lo:
                continue   # no hay overlap
            # Fracción del segmento del callejero cubierta por el rango del CPA
            rango_seg = max(1, s_hi - s_lo + 1)
            frac = min(1.0, (ov_hi - ov_lo + 1) / rango_seg)
            # 2) Peso = fracción del rango del callejero cubierta por el CPA.
            #    No pesamos por longitud geométrica porque eso empodera avenidas
            #    largas (Corrientes, Rivadavia, etc.) y diluye el ranking del CP.
            #    Lo que importa es: cuántas "porciones de calle" del barrio toca
            #    este CP.
            peso = frac
            if peso <= 0:
                continue
            # 3) Atribución par/impar al barrio
            bp = seg["barrio_par"]
            bi = seg["barrio_imp"]
            b0 = seg["barrio"]
            if bp and bi and bp != bi:
                pesos[cp4][bp] += peso * 0.5
                pesos[cp4][bi] += peso * 0.5
            elif bp:
                pesos[cp4][bp] += peso
            elif bi:
                pesos[cp4][bi] += peso
            elif b0:
                pesos[cp4][b0] += peso
            else:
                sin_seg_callejero += 1
                continue
            nsegs[cp4] += 1
            contados += 1
    print(f"     Atribuciones realizadas: {contados:,}", flush=True)
    print(f"     CPs únicos con datos: {len(pesos):,}", flush=True)
    if sin_seg_callejero:
        print(f"     Segmentos sin barrio en callejero: {sin_seg_callejero:,} (ignorados)", flush=True)

    # Resultado: barrio dominante por CP4
    print("[5] Resolviendo barrio dominante + confidence por CP4…", flush=True)
    barrio_a_comuna = cargar_barrio_a_comuna()
    resultados = []
    detalle = []
    for cp4 in sorted(pesos.keys()):
        c = pesos[cp4]
        total = sum(c.values())
        if total <= 0:
            continue
        # Barrio dominante
        b_dom, w_dom = c.most_common(1)[0]
        conf = 100.0 * w_dom / total
        # Buscar comuna: callejero usa barrios en Title Case con tildes ("Nuñez"),
        # geojson en upper con tildes ("NUÑEZ"). Normalizamos.
        b_up = b_dom.upper()
        com  = barrio_a_comuna.get(b_up) or barrio_a_comuna.get(_strip_accents(b_up)) or 0
        # Fuente
        fuente = "GCBA+Correo Argentino"
        if conf < CONFIANZA_BAJA_PCT:
            fuente += " (AMBIGUO)"
        resultados.append({
            "cp4":              cp4,
            "barrio":           b_dom.upper(),
            "comuna":           com,
            "confianza_pct":    round(conf, 1),
            "n_segmentos":      int(nsegs[cp4]),
            "n_barrios_zona":   len(c),
            "fuente":           fuente,
        })
        # Tabla de detalle: TODA la distribución barrio×cp4
        for b, w in c.most_common():
            b_up = b.upper()
            com_b = barrio_a_comuna.get(b_up) or barrio_a_comuna.get(_strip_accents(b_up)) or 0
            detalle.append({
                "cp4":     cp4,
                "barrio":  b_up,
                "comuna":  com_b,
                "peso":    round(w, 1),
                "pct":     round(100.0 * w / total, 2),
            })
    return resultados, detalle


def main():
    if not CALLEJERO.exists():
        sys.exit(f"ERROR: falta {CALLEJERO}")
    if not ALTURAS.exists() or not CALLES_CPA.exists():
        sys.exit(f"ERROR: falta {ALTURAS} / {CALLES_CPA}")
    if not BARRIOS_GEO.exists():
        sys.exit(f"ERROR: falta {BARRIOS_GEO}")

    print("=" * 76)
    print("  Derivando mapeo CP → barrio → comuna desde callejero oficial GCBA")
    print("    + dataset CPA Correo Argentino")
    print("=" * 76)
    print()

    resultados, detalle = derivar_cp_a_barrio()

    # Merge con overrides manuales (CPs que no aparecen en CPA pero sabemos que existen
    # como casillas postales / oficinas en CABA y queremos atribuirlos a una comuna).
    cps_derivados = {r["cp4"] for r in resultados}
    n_overrides = 0
    if OVERRIDE_CSV.exists():
        print(f"\n[5.5] Aplicando overrides manuales desde {OVERRIDE_CSV.name}…", flush=True)
        barrio_a_comuna = cargar_barrio_a_comuna()
        with OVERRIDE_CSV.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cp = (row.get("cp4") or "").strip()
                if not cp or cp.startswith("#") or not cp.isdigit() or len(cp) != 4:
                    continue
                if cp in cps_derivados:
                    continue   # el derivado de datos manda
                barrio = (row.get("barrio") or "").strip().upper()
                try:
                    com = int(row.get("comuna") or 0)
                except Exception:
                    com = 0
                # Validar comuna vs barrio si está disponible
                com_check = barrio_a_comuna.get(barrio) or barrio_a_comuna.get(_strip_accents(barrio))
                if com_check and com_check != com:
                    print(f"   ⚠ override CP {cp}: comuna declarada {com} ≠ comuna real del barrio {barrio} ({com_check}). Se usa {com_check}.")
                    com = com_check
                resultados.append({
                    "cp4":              cp,
                    "barrio":           barrio,
                    "comuna":           com,
                    "confianza_pct":    None,
                    "n_segmentos":      0,
                    "n_barrios_zona":   1,
                    "fuente":           "Override manual (sin calles en CPA)",
                })
                n_overrides += 1
        resultados.sort(key=lambda r: r["cp4"])
        print(f"     {n_overrides} overrides aplicados.", flush=True)

    # Reordenar y escribir CSV principal
    print(f"\n[6] Escribiendo {OUT_CSV.name}…", flush=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["cp4","barrio","comuna","confianza_pct","n_segmentos","n_barrios_zona","fuente"])
        w.writeheader()
        for r in resultados:
            w.writerow(r)
    print(f"     {len(resultados):,} CPs escritos ({n_overrides} overrides aplicados sobre datos derivados)", flush=True)

    # Tabla detalle (para auditoría manual / display en el HTML)
    print(f"[7] Escribiendo {OUT_DETAIL.name}…", flush=True)
    with OUT_DETAIL.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["cp4","barrio","comuna","peso","pct"])
        w.writeheader()
        for r in detalle:
            w.writerow(r)
    print(f"     {len(detalle):,} pares (cp,barrio) escritos", flush=True)

    # Resumen
    print()
    print("=" * 76)
    print("  RESUMEN")
    print("=" * 76)
    n_ambiguos = sum(1 for r in resultados if r["confianza_pct"] is not None and r["confianza_pct"] < CONFIANZA_BAJA_PCT)
    n_overrides_r = sum(1 for r in resultados if r["confianza_pct"] is None)
    print(f"  CPs únicos en mapeo:       {len(resultados):,}")
    print(f"  CPs derivados de datos:    {len(resultados) - n_overrides_r:,}")
    print(f"  CPs por override manual:   {n_overrides_r:,}")
    print(f"  CPs con confianza ≥ {CONFIANZA_BAJA_PCT}%:  {len(resultados) - n_ambiguos - n_overrides_r:,}")
    print(f"  CPs ambiguos (<{CONFIANZA_BAJA_PCT}%):     {n_ambiguos:,}  (se les asigna el barrio dominante; ver columna confianza_pct)")
    # Top 10 más ambiguos
    ambiguous = sorted([r for r in resultados if r["confianza_pct"] is not None], key=lambda r: r["confianza_pct"])[:10]
    print()
    print(f"  10 CPs más ambiguos:")
    for r in ambiguous:
        print(f"    CP {r['cp4']:>4} → {r['barrio']:>20} (comuna {r['comuna']:>2}) — conf {r['confianza_pct']:>5.1f}%  ({r['n_barrios_zona']} barrios candidatos)")
    print()
    # Confidence distribution
    from collections import Counter as _C
    buckets = _C()
    for r in resultados:
        c = r["confianza_pct"]
        if c is None: continue
        if c >= 95: buckets["95-100%"] += 1
        elif c >= 80: buckets["80-95%"] += 1
        elif c >= 60: buckets["60-80%"] += 1
        elif c >= 40: buckets["40-60%"] += 1
        else: buckets["<40%"] += 1
    print(f"  Distribución de confianza (sólo CPs derivados):")
    for k in ["95-100%","80-95%","60-80%","40-60%","<40%"]:
        print(f"    {k:>8}: {buckets.get(k, 0):>3}")
    print()


if __name__ == "__main__":
    main()
