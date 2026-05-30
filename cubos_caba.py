# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  INFORME CABA — Cubos por CÓDIGO POSTAL y por COMUNA                        ║
║                                                                              ║
║  Réplica de 6_tablero_base.py acotada a CABA (provincia ARCA '00') con la   ║
║  geografía como dimensión principal:                                         ║
║    · CP4 (los 4 primeros dígitos del cod_postal del padrón ARCA)            ║
║    · Comuna (1..15) — vía mapeo data/cp_comuna.csv                          ║
║                                                                              ║
║  Universo y métricas siguen EXACTAMENTE el canon del repo:                  ║
║    · Personas humanas (prefijo CUIT '2') vivas                              ║
║    · gar_pref_b = 0 en cada línea                                           ║
║    · cartera ∈ ('CONSUMO_VIV', 'PNFC')                                      ║
║    · consumo = prestamos - gar_pref_a + otros_conceptos                     ║
║    · es_moroso = (MAX situacion ≥ 3) por persona                            ║
║                                                                              ║
║  PNFC (Proveedores No Financieros de Crédito):                              ║
║    es_pnfc = (tiene al menos un vínculo con tipo_entidad='pnfc')            ║
║    deuda_pnfc, deuda_mora_pnfc, n_personas_con_pnfc — se exponen por CP     ║
║    para construir la incidencia de PNFCs por barrio.                        ║
║                                                                              ║
║  SALIDAS (Otras/Informe_CABA/data/):                                        ║
║    USO LOCAL (con CUILs, no publicar):                                      ║
║      deudores_caba.parquet         1 fila por persona CABA                  ║
║    PUBLICABLES (anonimizados):                                              ║
║      cubo_cp.parquet               cubo agregado por CP × dimensiones       ║
║      cubo_comuna.parquet           cubo agregado por comuna × dimensiones   ║
║      cp_metrics.parquet            métricas resumidas por CP (1 fila/CP)    ║
║      comuna_metrics.parquet        métricas resumidas por comuna (1/comuna) ║
║      caba_metadata.json            período, totales, listas para filtros    ║
║                                                                              ║
║  Uso:                                                                        ║
║    python Otras/Informe_CABA/cubos_caba.py                                  ║
║                                                                              ║
║  Tiempo estimado: 5-10 min                                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys
import time
import json
import csv
import calendar
from pathlib import Path
from datetime import datetime

import duckdb

# Windows default stdout es cp1252 → rompe con caracteres acentuados; reconfigurar.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ─── CONFIG ───────────────────────────────────────────────────────────────────

SCRIPT_DIR        = Path(__file__).resolve().parent.parent.parent  # repo root
BASE              = SCRIPT_DIR / "consolidado.duckdb"
OUT_DIR           = SCRIPT_DIR / "Otras" / "Informe_CABA"
DATA_DIR          = OUT_DIR / "data"
ENT_FIN_FILE      = SCRIPT_DIR / "entidades_financieras.txt"
CP_COMUNA_CSV     = DATA_DIR / "cp_comuna.csv"

EDAD_MAX_VALIDA   = 119

# Tramos de consumo en miles de $ (mismos que 6_tablero_base.py)
TRAMOS = [
    ("< $50K",        0,      50),
    ("$50K-$100K",   50,     100),
    ("$100K-$200K", 100,     200),
    ("$200K-$300K", 200,     300),
    ("$300K-$600K", 300,     600),
    ("$600K-$1M",   600,   1_000),
    ("$1M-$2M",   1_000,   2_000),
    ("$2M-$4M",   2_000,   4_000),
    ("$4M-$7M",   4_000,   7_000),
    ("$7M-$10M",  7_000,  10_000),
    ("$10M-$15M",10_000,  15_000),
    ("$15M-$20M",15_000,  20_000),
    ("> $20M",   20_000,    None),
]
TRAMOS_ORDEN = [t[0] for t in TRAMOS]

RANGOS_ETARIOS_ORDEN = [
    "Menor 18", "18-19", "20-24", "25-29", "30-34",
    "35-39", "40-44", "45-49", "50-54", "55-59",
    "60-64", "65-69", "70+", "Sin dato", "Fecha inválida",
]


# ─── UTILS ────────────────────────────────────────────────────────────────────

def paso(n, texto):
    print(f"\n[{n}] {texto}...", flush=True)

def tick(t0, texto=""):
    el = time.time() - t0
    print(f"     {el:.0f}s{' — ' + texto if texto else ''}", flush=True)
    return time.time()

def cargar_financieras():
    if not ENT_FIN_FILE.exists():
        sys.exit(f"ERROR: No se encontró {ENT_FIN_FILE}")
    codigos = []
    for raw in ENT_FIN_FILE.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.isdigit():
            codigos.append(s.zfill(5))
    if not codigos:
        sys.exit(f"ERROR: {ENT_FIN_FILE} no contiene códigos numéricos.")
    return codigos

def cargar_cp_comuna():
    """Lee data/cp_comuna.csv → lista de dicts {cp4, barrio, comuna}."""
    if not CP_COMUNA_CSV.exists():
        sys.exit(f"ERROR: No se encontró {CP_COMUNA_CSV}")
    rows = []
    with CP_COMUNA_CSV.open(encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            cp = row["cp4"].strip()
            if not cp or len(cp) != 4 or not cp.isdigit():
                continue
            rows.append({
                "cp4":    cp,
                "barrio": row["barrio"].strip(),
                "comuna": int(row["comuna"]),
            })
    return rows

def get_fecha_ref(periodo):
    yyyy = int(periodo[:4])
    mm   = int(periodo[4:6])
    last = calendar.monthrange(yyyy, mm)[1]
    return f"{yyyy:04d}-{mm:02d}-{last:02d}"


# ─── SQL TEMPLATES ────────────────────────────────────────────────────────────

def _sql_rango_etario(fecha_ref):
    return f"""CASE
        WHEN COALESCE(p.fecha_nac, '') = '' OR TRY_CAST(p.fecha_nac AS DATE) IS NULL
            THEN 'Sin dato'
        WHEN DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}') > {EDAD_MAX_VALIDA}
            THEN 'Fecha inválida'
        WHEN DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}') < 18
            THEN 'Menor 18'
        WHEN DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}') < 20
            THEN '18-19'
        WHEN DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}') < 25
            THEN '20-24'
        WHEN DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}') < 30
            THEN '25-29'
        WHEN DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}') < 35
            THEN '30-34'
        WHEN DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}') < 40
            THEN '35-39'
        WHEN DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}') < 45
            THEN '40-44'
        WHEN DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}') < 50
            THEN '45-49'
        WHEN DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}') < 55
            THEN '50-54'
        WHEN DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}') < 60
            THEN '55-59'
        WHEN DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}') < 65
            THEN '60-64'
        WHEN DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}') < 70
            THEN '65-69'
        ELSE '70+'
    END"""

def _sql_edad(fecha_ref):
    return f"""CASE
        WHEN COALESCE(p.fecha_nac, '') = '' OR TRY_CAST(p.fecha_nac AS DATE) IS NULL
            THEN NULL
        WHEN DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}') > {EDAD_MAX_VALIDA}
            THEN NULL
        ELSE DATE_DIFF('year', TRY_CAST(p.fecha_nac AS DATE), DATE '{fecha_ref}')
    END"""

def _sql_sexo():
    return """CASE
        WHEN COALESCE(p.sexo, '') = 'M' THEN 'Varón'
        WHEN COALESCE(p.sexo, '') = 'F' THEN 'Mujer'
        WHEN LEFT(d.nro_id, 2) = '20'   THEN 'Varón'
        WHEN LEFT(d.nro_id, 2) = '27'   THEN 'Mujer'
        ELSE 'Otro/SD'
    END"""

def _sql_tramo(col):
    parts = ["    CASE"]
    for nombre, lo, hi in TRAMOS:
        if hi is None:
            parts.append(f"        ELSE '{nombre}'")
        else:
            parts.append(f"        WHEN {col} < {hi} THEN '{nombre}'")
    parts.append("    END")
    return "\n".join(parts)

def _sql_tipo_entidad(financieras_sql):
    return f"""CASE
        WHEN LEFT(d.cod_entidad, 2) = '00'           THEN 'banco'
        WHEN d.cod_entidad IN {financieras_sql}      THEN 'financiera'
        ELSE 'pnfc'
    END"""


def sql_t_base(financieras_sql, fecha_ref, solo_caba=True):
    """
    t_base: 1 fila por (CUIL × entidad). Filtros canon del repo:
      PF vivas + gar_pref_b=0 + cartera ∈ CONSUMO_VIV/PNFC.
    Si solo_caba=True acota a provincia='00' (CABA); si False trae todo el país.
    """
    where_geo = "AND p.provincia = '00'" if solo_caba else "AND p.provincia <> '00' AND p.provincia IS NOT NULL AND p.provincia <> ''"
    return f"""
    CREATE TEMP TABLE t_base AS
    SELECT
        d.nro_id                                                   AS cuil,
        d.cod_entidad,
        d.situacion,
        d.dias_atraso,
        d.prestamos - d.gar_pref_a + d.otros_conceptos             AS consumo,
        CASE WHEN d.situacion >= 3 THEN 1 ELSE 0 END               AS es_mora,
        {_sql_tipo_entidad(financieras_sql)}                        AS tipo_entidad,
        TRIM(COALESCE(LEFT(p.cod_postal, 4), ''))                   AS cp4,
        {_sql_sexo()}                                               AS sexo,
        {_sql_edad(fecha_ref)}                                      AS edad,
        {_sql_rango_etario(fecha_ref)}                              AS rango_etario
    FROM deudores d
    LEFT JOIN padron p ON p.cuit = d.nro_id
    WHERE LEFT(d.nro_id, 1) = '2'
      AND COALESCE(p.fecha_fallecimiento, '') = ''
      AND COALESCE(d.gar_pref_b, 0) = 0
      AND d.cartera IN ('CONSUMO_VIV', 'PNFC')
      {where_geo}
    """


SQL_T_PERSONAS_TPL = """
CREATE TEMP TABLE t_personas AS
SELECT
    b.cuil,
    -- Geografía
    MAX(b.cp4)              AS cp4,
    COALESCE(MAX(m.barrio), 'Sin clasificar')   AS barrio,
    COALESCE(MAX(m.comuna), 0)                  AS comuna,
    -- Demografía
    MAX(b.sexo)             AS sexo,
    MAX(b.edad)             AS edad,
    MAX(b.rango_etario)     AS rango_etario,
    -- Métricas de consumo
    SUM(b.consumo)                                                   AS consumo_total,
    MAX(b.situacion)                                                 AS peor_sit,
    MAX(b.es_mora)                                                   AS es_moroso,
    SUM(CASE WHEN b.situacion >= 3 THEN b.consumo ELSE 0 END)        AS consumo_mora,
    SUM(CASE WHEN b.tipo_entidad='banco'      THEN b.consumo ELSE 0 END) AS consumo_banco,
    SUM(CASE WHEN b.tipo_entidad='financiera' THEN b.consumo ELSE 0 END) AS consumo_financiera,
    SUM(CASE WHEN b.tipo_entidad='pnfc'       THEN b.consumo ELSE 0 END) AS consumo_pnfc,
    SUM(CASE WHEN b.tipo_entidad='banco'      AND b.situacion>=3 THEN b.consumo ELSE 0 END) AS consumo_mora_banco,
    SUM(CASE WHEN b.tipo_entidad='financiera' AND b.situacion>=3 THEN b.consumo ELSE 0 END) AS consumo_mora_financiera,
    SUM(CASE WHEN b.tipo_entidad='pnfc'       AND b.situacion>=3 THEN b.consumo ELSE 0 END) AS consumo_mora_pnfc,
    MAX(CASE WHEN b.tipo_entidad='banco'      THEN 1 ELSE 0 END)     AS tiene_banco,
    MAX(CASE WHEN b.tipo_entidad='financiera' THEN 1 ELSE 0 END)     AS tiene_financiera,
    MAX(CASE WHEN b.tipo_entidad='pnfc'       THEN 1 ELSE 0 END)     AS tiene_pnfc,
    COUNT(*)                                                          AS n_entidades,
    {TRAMO_SQL} AS tramo_consumo
FROM t_base b
LEFT JOIN t_cp_mapping m ON m.cp4 = b.cp4
GROUP BY b.cuil
HAVING SUM(b.consumo) > 0
"""


# ─── EXPORTS ──────────────────────────────────────────────────────────────────

def export_local_personas(con, out_dir):
    """deudores_caba.parquet — 1 fila por persona con CUIL (USO LOCAL)."""
    f = out_dir / "deudores_caba.parquet"
    con.execute(f"""
        COPY (
            SELECT
                cuil,
                cp4, barrio, CAST(comuna AS SMALLINT) AS comuna,
                rango_etario, edad, sexo,
                ROUND(consumo_total, 1)        AS consumo_total,
                ROUND(consumo_mora,  1)        AS consumo_mora,
                ROUND(consumo_banco,      1)   AS consumo_banco,
                ROUND(consumo_financiera, 1)   AS consumo_financiera,
                ROUND(consumo_pnfc,       1)   AS consumo_pnfc,
                ROUND(consumo_mora_banco,      1) AS consumo_mora_banco,
                ROUND(consumo_mora_financiera, 1) AS consumo_mora_financiera,
                ROUND(consumo_mora_pnfc,       1) AS consumo_mora_pnfc,
                CAST(peor_sit          AS SMALLINT) AS peor_sit,
                CAST(es_moroso         AS TINYINT)  AS es_moroso,
                CAST(tiene_banco       AS TINYINT)  AS tiene_banco,
                CAST(tiene_financiera  AS TINYINT)  AS tiene_financiera,
                CAST(tiene_pnfc        AS TINYINT)  AS tiene_pnfc,
                CAST(n_entidades       AS SMALLINT) AS n_entidades,
                tramo_consumo
            FROM t_personas
        ) TO '{f.as_posix()}' (FORMAT 'parquet', COMPRESSION 'zstd')
    """)
    n = con.execute("SELECT COUNT(*) FROM t_personas").fetchone()[0]
    return f, n


def export_cubo(con, out_dir, fname, geo_col):
    """
    Cubo cruzado por la dimensión geográfica + dimensiones estándar del tablero.
    Métricas (sumables / contables, ver __NA__ guard del partido_pba):
      n_deudores, n_morosos, n_con_pnfc, n_con_banco, n_con_financiera
      deuda_total_miles, deuda_mora_miles
      deuda_banco_miles, deuda_financiera_miles, deuda_pnfc_miles
      deuda_mora_{banco,financiera,pnfc}_miles
    """
    f = out_dir / fname
    con.execute(f"""
        COPY (
            SELECT
                {geo_col},
                rango_etario,
                sexo,
                tramo_consumo,
                CAST(peor_sit AS SMALLINT)                            AS peor_sit,
                CAST(tiene_banco      AS TINYINT)                     AS tiene_banco,
                CAST(tiene_financiera AS TINYINT)                     AS tiene_financiera,
                CAST(tiene_pnfc       AS TINYINT)                     AS tiene_pnfc,
                COUNT(*)                                              AS n_deudores,
                SUM(es_moroso)                                        AS n_morosos,
                SUM(tiene_pnfc)                                       AS n_con_pnfc,
                SUM(tiene_banco)                                      AS n_con_banco,
                SUM(tiene_financiera)                                 AS n_con_financiera,
                ROUND(SUM(consumo_total), 1)                          AS deuda_total_miles,
                ROUND(SUM(consumo_mora),  1)                          AS deuda_mora_miles,
                ROUND(SUM(consumo_banco),      1)                     AS deuda_banco_miles,
                ROUND(SUM(consumo_financiera), 1)                     AS deuda_financiera_miles,
                ROUND(SUM(consumo_pnfc),       1)                     AS deuda_pnfc_miles,
                ROUND(SUM(consumo_mora_banco),      1)                AS deuda_mora_banco_miles,
                ROUND(SUM(consumo_mora_financiera), 1)                AS deuda_mora_financiera_miles,
                ROUND(SUM(consumo_mora_pnfc),       1)                AS deuda_mora_pnfc_miles
            FROM t_personas
            GROUP BY ALL
        ) TO '{f.as_posix()}' (FORMAT 'parquet', COMPRESSION 'zstd')
    """)
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{f.as_posix()}')").fetchone()[0]
    sz = f.stat().st_size / 1e6
    return f, n, sz


def export_metrics_resumen(con, out_dir, fname, geo_cols):
    """
    1 fila por unidad geográfica con métricas resumidas (ready-to-display).
    geo_cols: lista de columnas geográficas a incluir en GROUP BY (ej ['cp4'] o ['comuna']).

    Exporta tanto .parquet como .json (este último para consumo directo del HTML).
    """
    geo_sel = ", ".join(geo_cols)
    f_pq   = out_dir / fname
    f_json = out_dir / (fname.replace(".parquet", ".json"))
    sql = f"""
        SELECT
            {geo_sel},
            COUNT(*)                                              AS n_personas,
            SUM(es_moroso)                                        AS n_morosos,
            SUM(tiene_pnfc)                                       AS n_con_pnfc,
            SUM(tiene_banco)                                      AS n_con_banco,
            SUM(tiene_financiera)                                 AS n_con_financiera,
            ROUND(SUM(consumo_total), 1)                          AS deuda_total_miles,
            ROUND(SUM(consumo_mora),  1)                          AS deuda_mora_miles,
            ROUND(SUM(consumo_banco),      1)                     AS deuda_banco_miles,
            ROUND(SUM(consumo_financiera), 1)                     AS deuda_financiera_miles,
            ROUND(SUM(consumo_pnfc),       1)                     AS deuda_pnfc_miles,
            ROUND(SUM(consumo_mora_pnfc),       1)                AS deuda_mora_pnfc_miles,
            -- Ratios calculados (lo hacemos acá para que el frontend no tenga que)
            ROUND(100.0 * SUM(es_moroso) / COUNT(*), 2)           AS pct_mora_personas,
            ROUND(
                100.0 * SUM(consumo_mora) / NULLIF(SUM(consumo_total),0), 2
            )                                                     AS pct_mora_deuda,
            ROUND(100.0 * SUM(tiene_pnfc) / COUNT(*), 2)          AS pct_personas_con_pnfc,
            ROUND(
                100.0 * SUM(consumo_pnfc) / NULLIF(SUM(consumo_total),0), 2
            )                                                     AS pct_deuda_pnfc
        FROM t_personas
        GROUP BY {geo_sel}
    """
    # Parquet
    con.execute(f"COPY ({sql}) TO '{f_pq.as_posix()}' (FORMAT 'parquet', COMPRESSION 'zstd')")
    # JSON (array de objetos) — render-friendly para el HTML
    rows_df = con.execute(sql).df()
    # Convertir NaN→None y tipos numpy a tipos nativos JSON-safe
    payload = []
    for rec in rows_df.to_dict(orient="records"):
        out = {}
        for k, v in rec.items():
            if v is None or (isinstance(v, float) and (v != v)):  # NaN
                out[k] = None
            elif hasattr(v, "item"):
                out[k] = v.item()
            else:
                out[k] = v
        payload.append(out)
    f_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    n  = con.execute(f"SELECT COUNT(*) FROM read_parquet('{f_pq.as_posix()}')").fetchone()[0]
    sz = f_pq.stat().st_size / 1e3
    return f_pq, n, sz


def export_metadata(con, out_dir, periodo, fecha_ref, t_total, cp_mapping_rows,
                    sin_clasificar_count):
    """JSON con totales globales y listas auxiliares para llenar selectores."""
    tot = con.execute("""
        SELECT
            COUNT(*)                            AS n_deudores,
            SUM(es_moroso)                      AS n_morosos,
            SUM(tiene_pnfc)                     AS n_con_pnfc,
            ROUND(SUM(consumo_total)/1e3, 2)    AS deuda_total_mm,
            ROUND(SUM(consumo_mora)/1e3,  2)    AS deuda_mora_mm,
            ROUND(SUM(consumo_pnfc)/1e3,  2)    AS deuda_pnfc_mm,
            ROUND(SUM(consumo_mora_pnfc)/1e3,2) AS deuda_mora_pnfc_mm,
            ROUND(SUM(consumo_banco)/1e3, 2)    AS deuda_banco_mm,
            ROUND(SUM(consumo_financiera)/1e3,2) AS deuda_financiera_mm
        FROM t_personas
    """).fetchone()

    # CPs presentes en los datos
    cps_observados = [r[0] for r in con.execute("""
        SELECT cp4 FROM t_personas
        WHERE cp4 IS NOT NULL AND cp4 <> ''
        GROUP BY cp4 ORDER BY COUNT(*) DESC
    """).fetchall()]

    # Comunas presentes
    comunas_observadas = [int(r[0]) for r in con.execute("""
        SELECT comuna FROM t_personas
        WHERE comuna > 0
        GROUP BY comuna ORDER BY comuna
    """).fetchall()]

    # Tabla CP → barrio → comuna (la del mapping cargado, para que el frontend
    # sepa qué barrio asociar a cada CP sin tener que parsear el CSV).
    cp_to_meta = [
        {"cp4": row["cp4"], "barrio": row["barrio"], "comuna": row["comuna"]}
        for row in cp_mapping_rows
    ]

    metadata = {
        "periodo":              periodo,
        "fecha_ref":            fecha_ref,
        "fecha_generacion":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tiempo_generacion_seg": round(t_total, 1),
        "geografia": {
            "cps_observados":      cps_observados,
            "cps_mapeados":        sorted({r["cp4"] for r in cp_mapping_rows}),
            "cp_to_barrio_comuna": cp_to_meta,
            "comunas_observadas":  comunas_observadas,
            "cps_sin_clasificar":  sin_clasificar_count,  # CPs en datos no mapeados
        },
        "filtros": {
            "rangos_etarios": RANGOS_ETARIOS_ORDEN,
            "sexos":          ["Varón", "Mujer", "Otro/SD"],
            "tramos":         TRAMOS_ORDEN,
            "situaciones":    [1, 2, 3, 4, 5],
            "tipos_entidad":  ["banco", "financiera", "pnfc"],
        },
        "totales": {
            "n_deudores":              int(tot[0]),
            "n_morosos":               int(tot[1]),
            "n_con_pnfc":              int(tot[2]),
            "deuda_total_mm":          float(tot[3]),
            "deuda_mora_mm":           float(tot[4]),
            "deuda_pnfc_mm":           float(tot[5]),
            "deuda_mora_pnfc_mm":      float(tot[6]),
            "deuda_banco_mm":          float(tot[7]),
            "deuda_financiera_mm":     float(tot[8]),
            "pct_mora_personas":       round(100.0 * tot[1] / tot[0], 2) if tot[0] else 0,
            "pct_mora_deuda":          round(100.0 * tot[4] / tot[3], 2) if tot[3] else 0,
            "pct_personas_con_pnfc":   round(100.0 * tot[2] / tot[0], 2) if tot[0] else 0,
            "pct_deuda_pnfc":          round(100.0 * tot[5] / tot[3], 2) if tot[3] else 0,
        },
        "notas_metodologicas": {
            "universo":  "Personas humanas (CUIT prefijo 2) vivas con consumo > 0, residentes en CABA (padron.provincia='00'). Se excluyen líneas con gar_pref_b > 0 (hipotecas/prendas/derechos reales) y carteras COMERCIAL.",
            "consumo":   "prestamos - gar_pref_a + otros_conceptos (miles de $).",
            "mora":      "situacion >= 3, criterio del peor clasificador a nivel persona.",
            "pnfc":      "Proveedor No Financiero de Crédito = entidad que no es banco (cod 00...) ni financiera regulada. Una persona 'tiene PNFC' si tiene ≥1 vínculo con tipo_entidad='pnfc'.",
            "cp_a_comuna": (
                "El mapeo CP4 → barrio → comuna se basa en zonas postales estándar de CABA "
                "(data/cp_comuna.csv). Es una aproximación: en CABA un mismo CP4 puede cubrir "
                "varios barrios, pero suele tener UNO predominante. Las personas con CP no "
                "mapeado quedan agrupadas en comuna=0 (Sin clasificar)."
            ),
            "geo_match": f"{tot[0] - sin_clasificar_count:,} de {tot[0]:,} personas matchearon un CP del mapping ({100.0*(tot[0]-sin_clasificar_count)/tot[0]:.1f}%).",
        },
    }

    f = out_dir / "caba_metadata.json"
    f.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return f, metadata


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    if not BASE.exists():
        sys.exit(f"\nERROR: No se encontró {BASE}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 76)
    print("  INFORME CABA — Cubos por CÓDIGO POSTAL y por COMUNA")
    print("=" * 76)
    print(f"\n  Base:  {BASE}")
    print(f"  Salida: {OUT_DIR.relative_to(SCRIPT_DIR)}/data/")

    t_inicio = time.time()
    con = duckdb.connect(str(BASE), read_only=False)

    _tmp = SCRIPT_DIR / ".duckdb_tmp"
    _tmp.mkdir(exist_ok=True)
    con.execute(f"SET temp_directory='{str(_tmp).replace(chr(92), '/')}'")
    con.execute("SET preserve_insertion_order=false")

    periodo   = (con.execute("SELECT valor FROM metadata WHERE clave='periodo'").fetchone()
                 or ("desconocido",))[0]
    fecha_ref = get_fecha_ref(periodo)
    print(f"  Período: {periodo}  |  Fecha ref edad: {fecha_ref}")
    print()

    codigos_fin = cargar_financieras()
    financieras_sql = "(" + ", ".join(f"'{c}'" for c in codigos_fin) + ")"
    print(f"  Financieras reguladas: {len(codigos_fin)} códigos cargados")

    cp_rows = cargar_cp_comuna()
    print(f"  Mapeo CP4 → comuna:    {len(cp_rows)} CPs en {CP_COMUNA_CSV.name}")

    # ── 1. t_base ────────────────────────────────────────────────────────────
    paso(1, "Construyendo t_base (JOIN deudores × padron, CABA, PF vivos)")
    t0 = time.time()
    con.execute("DROP TABLE IF EXISTS t_base")
    con.execute(sql_t_base(financieras_sql, fecha_ref))
    n_base = con.execute("SELECT COUNT(*) FROM t_base").fetchone()[0]
    n_cuils_base = con.execute("SELECT COUNT(DISTINCT cuil) FROM t_base").fetchone()[0]
    t0 = tick(t0, f"{n_base:,} filas (entidad×persona) — {n_cuils_base:,} CUILs únicos")

    # ── 2. Cargar mapeo CP→comuna en una temp table ─────────────────────────
    paso(2, "Cargando mapeo CP → barrio → comuna a t_cp_mapping")
    con.execute("DROP TABLE IF EXISTS t_cp_mapping")
    con.execute("CREATE TEMP TABLE t_cp_mapping (cp4 VARCHAR(4), barrio VARCHAR, comuna SMALLINT)")
    con.executemany(
        "INSERT INTO t_cp_mapping VALUES (?, ?, ?)",
        [(r["cp4"], r["barrio"], r["comuna"]) for r in cp_rows],
    )
    n_map = con.execute("SELECT COUNT(*) FROM t_cp_mapping").fetchone()[0]
    t0 = tick(t0, f"{n_map} CPs en mapping")

    # ── 3. t_personas ────────────────────────────────────────────────────────
    paso(3, "Agregando a nivel persona → t_personas (HAVING consumo>0)")
    con.execute("DROP TABLE IF EXISTS t_personas")
    sql_personas = SQL_T_PERSONAS_TPL.format(
        TRAMO_SQL=_sql_tramo("SUM(b.consumo)").replace("    CASE", "CASE")
    )
    con.execute(sql_personas)
    n_pers = con.execute("SELECT COUNT(*) FROM t_personas").fetchone()[0]
    n_mor  = con.execute("SELECT SUM(es_moroso) FROM t_personas").fetchone()[0]
    n_pnfc = con.execute("SELECT SUM(tiene_pnfc) FROM t_personas").fetchone()[0]
    deuda  = con.execute("SELECT ROUND(SUM(consumo_total)/1e3, 1) FROM t_personas").fetchone()[0]
    mora   = con.execute("SELECT ROUND(SUM(consumo_mora)/1e3,  1) FROM t_personas").fetchone()[0]
    n_sin  = con.execute("SELECT COUNT(*) FROM t_personas WHERE comuna = 0").fetchone()[0]
    print(f"     {n_pers:,} personas con consumo > 0 en CABA")
    print(f"     {n_mor:,} morosos ({100.0*n_mor/n_pers:.1f}%)")
    print(f"     {n_pnfc:,} con PNFC ({100.0*n_pnfc/n_pers:.1f}%)")
    print(f"     Deuda total: {deuda:,} M$  |  En mora: {mora:,} M$")
    print(f"     Sin clasificar (CP no mapeado): {n_sin:,} ({100.0*n_sin/n_pers:.1f}%)")
    t0 = tick(t0, "t_personas listo")

    # ── 4. Export local con CUILs ────────────────────────────────────────────
    paso(4, "Exportando parquet local con CUILs (USO LOCAL — no publicar)")
    t0 = time.time()
    f_local, n_local = export_local_personas(con, DATA_DIR)
    sz = f_local.stat().st_size / 1e6
    t0 = tick(t0, f"{f_local.name} ({n_local:,} filas, {sz:.1f} MB)")

    # ── 5. Cubos publicables ────────────────────────────────────────────────
    paso(5, "Generando cubos publicables (sin CUILs)")
    t0 = time.time()
    f_cp,   n_cp,   sz_cp   = export_cubo(con, DATA_DIR, "cubo_cp.parquet",    "cp4")
    t0 = tick(t0, f"cubo_cp.parquet ({n_cp:,} filas, {sz_cp:.2f} MB)")
    f_com,  n_com,  sz_com  = export_cubo(con, DATA_DIR, "cubo_comuna.parquet","comuna")
    t0 = tick(t0, f"cubo_comuna.parquet ({n_com:,} filas, {sz_com:.2f} MB)")

    # ── 6. Métricas resumidas (1 fila por geo) ──────────────────────────────
    paso(6, "Generando métricas resumidas por CP, barrio y comuna (1 fila por unidad)")
    t0 = time.time()
    f_cm_cp,  n_cm_cp,  sz_cm_cp  = export_metrics_resumen(
        con, DATA_DIR, "cp_metrics.parquet", ["cp4", "barrio", "comuna"]
    )
    t0 = tick(t0, f"cp_metrics.{{parquet,json}} ({n_cm_cp} CPs, {sz_cm_cp:.1f} KB)")
    f_cm_bar, n_cm_bar, sz_cm_bar = export_metrics_resumen(
        con, DATA_DIR, "barrio_metrics.parquet", ["barrio", "comuna"]
    )
    t0 = tick(t0, f"barrio_metrics.{{parquet,json}} ({n_cm_bar} barrios, {sz_cm_bar:.1f} KB)")
    f_cm_com, n_cm_com, sz_cm_com = export_metrics_resumen(
        con, DATA_DIR, "comuna_metrics.parquet", ["comuna"]
    )
    t0 = tick(t0, f"comuna_metrics.{{parquet,json}} ({n_cm_com} comunas, {sz_cm_com:.1f} KB)")

    # ── 7. Cubo "país sin CABA" (para comparativa) ───────────────────────────
    paso(7, "Generando cubo país sin CABA (para comparativa en el HTML)")
    t0 = time.time()
    con.execute("DROP TABLE IF EXISTS t_base_pais")
    con.execute(sql_t_base(financieras_sql, fecha_ref, solo_caba=False).replace("t_base", "t_base_pais"))
    n_base_pais = con.execute("SELECT COUNT(*) FROM t_base_pais").fetchone()[0]
    t0 = tick(t0, f"t_base_pais: {n_base_pais:,} filas")

    # Agregamos por persona (HAVING consumo>0) y luego sumamos en un solo resumen
    f_pais = DATA_DIR / "pais_sin_caba_resumen.json"
    pais_row = con.execute("""
        WITH t_personas_pais AS (
            SELECT
                cuil,
                MAX(sexo) AS sexo,
                MAX(rango_etario) AS rango_etario,
                SUM(consumo) AS consumo_total,
                MAX(es_mora) AS es_moroso,
                SUM(CASE WHEN situacion >= 3 THEN consumo ELSE 0 END) AS consumo_mora,
                MAX(CASE WHEN tipo_entidad='pnfc' THEN 1 ELSE 0 END) AS tiene_pnfc,
                MAX(CASE WHEN tipo_entidad='banco' THEN 1 ELSE 0 END) AS tiene_banco,
                MAX(CASE WHEN tipo_entidad='financiera' THEN 1 ELSE 0 END) AS tiene_financiera
            FROM t_base_pais
            GROUP BY cuil
            HAVING SUM(consumo) > 0
        )
        SELECT
            COUNT(*)::BIGINT                  AS n_personas,
            SUM(es_moroso)::BIGINT            AS n_morosos,
            SUM(tiene_pnfc)::BIGINT           AS n_con_pnfc,
            SUM(tiene_banco)::BIGINT          AS n_con_banco,
            SUM(tiene_financiera)::BIGINT     AS n_con_financiera,
            ROUND(SUM(consumo_total), 1)      AS deuda_total_miles,
            ROUND(SUM(consumo_mora),  1)      AS deuda_mora_miles
        FROM t_personas_pais
    """).fetchone()
    pais_data = {
        "n_personas":          int(pais_row[0]),
        "n_morosos":           int(pais_row[1]),
        "n_con_pnfc":          int(pais_row[2]),
        "n_con_banco":         int(pais_row[3]),
        "n_con_financiera":    int(pais_row[4]),
        "deuda_total_miles":   float(pais_row[5]),
        "deuda_mora_miles":    float(pais_row[6]),
        "deuda_total_mm":      round(float(pais_row[5])/1e3, 2),
        "deuda_mora_mm":       round(float(pais_row[6])/1e3, 2),
        "pct_mora_personas":   round(100.0 * pais_row[1] / pais_row[0], 2) if pais_row[0] else 0,
        "pct_mora_deuda":      round(100.0 * pais_row[6] / pais_row[5], 2) if pais_row[5] else 0,
        "pct_personas_con_pnfc": round(100.0 * pais_row[2] / pais_row[0], 2) if pais_row[0] else 0,
        "nota": "Personas físicas vivas con consumo > 0 en provincia ARCA != '00' (resto del país, excluye CABA). Mismo canon que el cubo CABA.",
    }
    f_pais.write_text(json.dumps(pais_data, indent=2, ensure_ascii=False), encoding="utf-8")
    t0 = tick(t0, f"pais_sin_caba_resumen.json ({pais_data['n_personas']:,} personas, {pais_data['pct_mora_personas']:.2f}% mora)")

    # ── 8. Metadata ─────────────────────────────────────────────────────────
    paso(8, "Generando caba_metadata.json")
    t0 = time.time()
    t_total = time.time() - t_inicio
    f_meta, meta = export_metadata(con, DATA_DIR, periodo, fecha_ref, t_total, cp_rows, n_sin)
    t0 = tick(t0, f"{f_meta.name} ({f_meta.stat().st_size/1e3:.1f} KB)")

    # ── Resumen ─────────────────────────────────────────────────────────────
    el = (time.time() - t_inicio) / 60
    print()
    print("=" * 76)
    print(f"  LISTO en {el:.1f} minutos")
    print("=" * 76)
    print(f"\n  Archivos en {OUT_DIR.relative_to(SCRIPT_DIR)}/data/")
    print()
    print("  USO LOCAL (con CUILs, NO publicar):")
    for fn in ["deudores_caba.parquet"]:
        f = DATA_DIR / fn
        if f.exists():
            print(f"    {fn:<32s} {f.stat().st_size/1e6:>8.1f} MB")
    print()
    print("  PUBLICABLES (anonimizados):")
    for fn in ["cubo_cp.parquet", "cubo_comuna.parquet",
               "cp_metrics.parquet", "cp_metrics.json",
               "barrio_metrics.parquet", "barrio_metrics.json",
               "comuna_metrics.parquet", "comuna_metrics.json",
               "pais_sin_caba_resumen.json",
               "caba_metadata.json"]:
        f = DATA_DIR / fn
        if f.exists():
            sz = f.stat().st_size
            unidad = "MB" if sz > 1e6 else "KB"
            divisor = 1e6 if sz > 1e6 else 1e3
            print(f"    {fn:<32s} {sz/divisor:>8.1f} {unidad}")
    t = meta["totales"]
    print()
    print(f"  Resumen del universo CABA:")
    print(f"     {t['n_deudores']:>12,} personas físicas vivas con consumo > 0")
    print(f"     {t['n_morosos']:>12,} morosos       ({t['pct_mora_personas']:.1f}%)")
    print(f"     {t['n_con_pnfc']:>12,} con PNFC      ({t['pct_personas_con_pnfc']:.1f}%)")
    print(f"     {t['deuda_total_mm']:>12,.0f} M$ deuda total")
    print(f"     {t['deuda_mora_mm']:>12,.0f} M$ en mora    ({t['pct_mora_deuda']:.1f}%)")
    print(f"     {t['deuda_pnfc_mm']:>12,.0f} M$ con PNFC   ({t['pct_deuda_pnfc']:.1f}%)")
    print()


if __name__ == "__main__":
    main()
