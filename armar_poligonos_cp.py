# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARMAR POLÍGONOS POR CP4 — DERIVADOS DEL CALLEJERO OFICIAL GCBA             ║
║                                                                              ║
║  No existe GeoJSON oficial de polígonos por código postal CABA en data       ║
║  abierta. Lo construimos desde el callejero (que SÍ es oficial) + el        ║
║  dataset CPA del Correo Argentino que ya cruzamos en armar_cp_barrio.py.    ║
║                                                                              ║
║  ALGORITMO                                                                   ║
║    1. Para cada CP4 con datos en el padrón, juntar las LINESTRING del       ║
║       callejero asignadas a las calles que el CPA dice que tienen ese CP.   ║
║    2. Buffer 60m alrededor de las líneas → polígono "tubular" que sigue las ║
║       calles. (60m ≈ 1/2 cuadra → cubre toda la manzana adyacente.)         ║
║    3. unary_union de todos los buffers del CP → polígono final.             ║
║    4. Optional: simplify a 1e-4 grados (~10m) para reducir tamaño de salida.║
║                                                                              ║
║  El resultado son polígonos representativos del CP que SI pueden solaparse  ║
║  con CPs vecinos (es la realidad: una manzana puede tener dos CPs según el  ║
║  lado de la calle). Eso queda como una decisión consciente: NO recortamos   ║
║  para que el mapa muestre fielmente que los CPs en CABA no son particiones  ║
║  estrictas.                                                                  ║
║                                                                              ║
║  SALIDA                                                                      ║
║    geo/cps.geojson  — FeatureCollection, una Feature por CP4 con            ║
║                       properties {cp4, barrio_dom, comuna, confianza_pct,   ║
║                       n_segmentos, longitud_m_total, n_personas (si está)}. ║
║                                                                              ║
║  Pre-requisitos: data/cp_comuna.csv (mapeo derivado), data/callejero_gcba.csv║
║                  data/calles.CSV, data/alturas.CSV (todos generados en      ║
║                  armar_cp_barrio.py).                                       ║
║                                                                              ║
║  Tiempo estimado: 2-5 min (~280 CPs × buffer + union).                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys
import csv
import json
import re
import time
from pathlib import Path
from collections import defaultdict

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Resolución de dependencias
try:
    from shapely.geometry import LineString, mapping, Polygon, MultiPolygon
    from shapely.ops import unary_union, transform
except ImportError:
    print("Instalando shapely…", flush=True)
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "shapely"])
    from shapely.geometry import LineString, mapping, Polygon, MultiPolygon
    from shapely.ops import unary_union, transform

import math

SCRIPT_DIR  = Path(__file__).resolve().parent
DATA_DIR    = SCRIPT_DIR / "data"
GEO_DIR     = SCRIPT_DIR / "geo"

CALLEJERO   = DATA_DIR / "callejero_gcba.csv"
CALLES_CPA  = DATA_DIR / "calles.CSV"
ALTURAS     = DATA_DIR / "alturas.CSV"
CP_COMUNA   = DATA_DIR / "cp_comuna.csv"
OUT_GEO     = GEO_DIR  / "cps.geojson"

# Re-uso la normalización de armar_cp_barrio.py para consistencia
sys.path.insert(0, str(SCRIPT_DIR))
from armar_cp_barrio import (
    normalizar_calle, claves_match, cargar_callejero, cargar_calles_caba_cpa
)

# Buffer alrededor de cada LINESTRING (en grados WGS84).
# 1 grado ≈ 111 km en latitud; 0.0005 grados ≈ 55 m. CABA: 0.0006 ≈ 60 m.
BUFFER_DEG = 0.0006
# Tolerancia para simplify (en grados). 5e-5 ≈ 5.5 m.
SIMPLIFY_TOL = 5e-5


def parse_linestring_wkt(wkt: str) -> LineString | None:
    """Parsea 'LINESTRING (x y, x y, ...)' a un objeto shapely.
    Tolera espacios y minor formatting differences."""
    if not wkt or not wkt.startswith("LINESTRING"):
        return None
    m = re.search(r"\(([^)]+)\)", wkt)
    if not m:
        return None
    coords = []
    for pair in m.group(1).split(","):
        parts = pair.strip().split()
        if len(parts) < 2:
            continue
        try:
            x = float(parts[0]); y = float(parts[1])
        except ValueError:
            continue
        coords.append((x, y))
    if len(coords) < 2:
        return None
    try:
        return LineString(coords)
    except Exception:
        return None


def cargar_geom_callejero():
    """Para cada nombre normalizado del callejero, lista las geometrías LINESTRING."""
    geoms = defaultdict(list)  # full_clave -> [LineString, ...]
    with CALLEJERO.open(encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            nom = row.get("nomoficial", "")
            claves = claves_match(nom)
            if not claves:
                continue
            ls = parse_linestring_wkt(row.get("geometry", ""))
            if ls is None or ls.is_empty:
                continue
            geoms[claves[0]].append(ls)
    return geoms


def cargar_cp_a_calles():
    """
    Devuelve dict: cp4 -> set(codcalle).
    Sólo CABA (codloc=00005001) y CPs C... válidos.
    Reuso el iterador de armar_cp_barrio pero más simple acá.
    """
    out = defaultdict(set)
    with ALTURAS.open(encoding="latin-1") as f:
        r = csv.DictReader(f, delimiter=";")
        for row in r:
            cpa = (row.get("codpostal") or "").strip().strip('"')
            if len(cpa) < 5 or cpa[0] != "C":
                continue
            cp4 = cpa[1:5]
            if not cp4.isdigit():
                continue
            cc = row.get("codcalle")
            if cc:
                out[cp4].add(cc)
    return out


def cargar_cp_metadata():
    """cp_comuna.csv (generado por armar_cp_barrio.py) → dict cp4 → metadata fila."""
    out = {}
    with CP_COMUNA.open(encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            out[row["cp4"]] = row
    return out


def construir_poligono_cp(geoms_calles_cp, buffer_deg=BUFFER_DEG):
    """Toma una lista de LineString, las bufferea y une. Devuelve un polígono (o None)."""
    if not geoms_calles_cp:
        return None, 0
    # Buffer + union; usamos buffer "round" (default).
    buffers = [g.buffer(buffer_deg) for g in geoms_calles_cp if not g.is_empty]
    if not buffers:
        return None, 0
    poly = unary_union(buffers)
    if poly.is_empty:
        return None, 0
    # Simplify
    poly = poly.simplify(SIMPLIFY_TOL, preserve_topology=True)
    return poly, sum(g.length for g in geoms_calles_cp)  # length en grados, sólo informativa


def main():
    if not CALLEJERO.exists():
        sys.exit(f"ERROR: falta {CALLEJERO}")
    if not CALLES_CPA.exists() or not ALTURAS.exists():
        sys.exit(f"ERROR: faltan datasets CPA en {DATA_DIR}")
    if not CP_COMUNA.exists():
        sys.exit(f"ERROR: falta {CP_COMUNA} — correr antes armar_cp_barrio.py")

    print("=" * 76)
    print("  Construyendo polígonos por CP4 desde callejero GCBA")
    print("=" * 76)
    print()

    t0 = time.time()
    print("[1] Cargando callejero (geometrías)…", flush=True)
    geoms_cj = cargar_geom_callejero()
    print(f"     {len(geoms_cj):,} claves con geometría", flush=True)
    n_ls = sum(len(v) for v in geoms_cj.values())
    print(f"     {n_ls:,} segmentos LINESTRING totales", flush=True)

    print("\n[2] Cargando calles CABA del CPA + matcheo…", flush=True)
    calles_caba = cargar_calles_caba_cpa()
    print(f"     {len(calles_caba):,} codcalles únicos en CABA", flush=True)

    # Mapeo codcalle → clave full (reusando lógica de armar_cp_barrio)
    full_set = set(geoms_cj.keys())
    cc_to_full = {}
    for cc, info in calles_caba.items():
        nf = info["norm_full"]
        nl = info["norm_last"]
        if nf in full_set:
            cc_to_full[cc] = nf
        elif nl:
            # Por simplicidad: usamos full_set como anchor; el fallback last no aplica aquí
            # (los polígonos sólo necesitan estar lo más correctamente atribuidos posible;
            # el fallback last era para resolver el barrio, acá no hace falta).
            pass

    print("\n[3] Calculando CP4 → conjunto de calles…", flush=True)
    cp_a_calles = cargar_cp_a_calles()
    print(f"     {len(cp_a_calles):,} CPs con al menos 1 codcalle", flush=True)

    cp_metadata = cargar_cp_metadata()

    print("\n[4] Construyendo polígonos (buffer + union)…", flush=True)
    features = []
    cps_ordered = sorted(cp_a_calles.keys())
    ok = vacios = 0
    for i, cp4 in enumerate(cps_ordered):
        # Calcular conjunto de LineStrings de las calles del CP
        ls_list = []
        for cc in cp_a_calles[cp4]:
            if cc in cc_to_full:
                ls_list.extend(geoms_cj[cc_to_full[cc]])
        if not ls_list:
            vacios += 1
            continue
        poly, longitud_total = construir_poligono_cp(ls_list)
        if poly is None or poly.is_empty:
            vacios += 1
            continue
        meta = cp_metadata.get(cp4, {})
        props = {
            "cp4":            cp4,
            "barrio_dom":     meta.get("barrio", ""),
            "comuna":         int(meta.get("comuna") or 0),
            "confianza_pct":  (float(meta.get("confianza_pct")) if meta.get("confianza_pct") not in (None, "", "None") else None),
            "n_segmentos":    int(meta.get("n_segmentos") or 0) if meta.get("n_segmentos") else len(ls_list),
            "fuente":         meta.get("fuente", "GCBA+Correo Argentino"),
            "n_lineas":       len(ls_list),
        }
        features.append({
            "type":       "Feature",
            "properties": props,
            "geometry":   mapping(poly),
        })
        ok += 1
        if (i+1) % 50 == 0:
            print(f"     ... {i+1}/{len(cps_ordered)} CPs procesados", flush=True)
    print(f"     Polígonos generados: {ok:,}  |  CPs vacíos (sin calles): {vacios:,}", flush=True)

    print(f"\n[5] Escribiendo {OUT_GEO.name}…", flush=True)
    out = {
        "type":       "FeatureCollection",
        "metadata": {
            "fecha_generacion": time.strftime("%Y-%m-%d %H:%M:%S"),
            "generador":        "armar_poligonos_cp.py",
            "fuente_calles":    "Callejero oficial GCBA (data.buenosaires.gob.ar/dataset/calles)",
            "fuente_cps":       "CPA Correo Argentino (vía OpenDataCordoba)",
            "buffer_deg":       BUFFER_DEG,
            "simplify_tol":     SIMPLIFY_TOL,
            "nota": "Polígonos derivados por unión + buffer de las calles asignadas a cada CP4. PUEDEN solaparse con CPs vecinos (en CABA un mismo bloque puede tener dos CPs según el lado de la calle).",
        },
        "features": features,
    }
    OUT_GEO.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    sz = OUT_GEO.stat().st_size / 1e6
    el = time.time() - t0
    print(f"     {OUT_GEO.name} ({sz:.2f} MB, {ok} polígonos)  —  {el:.1f}s")
    print()


if __name__ == "__main__":
    main()
