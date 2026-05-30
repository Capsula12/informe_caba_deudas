# Informe CABA — Distribución geográfica de la deuda de consumo

Mapa interactivo de la morosidad y la incidencia de PNFCs (Proveedores No
Financieros de Crédito) en CABA, agregado por **código postal**, **barrio** o
**comuna**, con filtros por sexo, edad, tramo de deuda y tipo de acreedor.

Replica el canon del tablero principal (consumo puro = `prestamos − gar_pref_a
+ otros_conceptos`, PF vivas, `gar_pref_b = 0`, cartera ∈ `CONSUMO_VIV / PNFC`,
provincia ARCA = `'00'`).


## Cómo abrir el informe

**Windows (doble-click):**
```
servir.bat
```
Esto levanta `python -m http.server 8766` en esta carpeta y abre el browser
automáticamente. Cerrá la ventana de la consola para detener el servidor.

**Manual (cualquier OS):**
```
python -m http.server 8766
```
Y abrir [http://localhost:8766/](http://localhost:8766/).

> No funciona abrir `index.html` con `file://` — el browser bloquea `fetch()`
> de archivos locales por CORS.


## Pipeline de generación

```
                 ┌─ data/callejero_gcba.csv  (descarga GCBA)
                 ├─ data/calles.CSV          (CPA Correo Argentino vía OpenDataCordoba)
                 ├─ data/alturas.CSV          ┘
                 ├─ data/localidad.CSV        ┘
                 │
                 ▼
   armar_cp_barrio.py  →  data/cp_comuna.csv          ← mapeo CP4 → barrio → comuna
                          data/cp_barrio_detalle.csv   ← TODA la distribución (auditoría)
                 │
                 ▼
   armar_poligonos_cp.py → geo/cps.geojson             ← 255 polígonos por CP4 derivados
                 │                                         del callejero (buffer 60m + union)
                 ▼
   cubos_caba.py     → data/cubo_cp.parquet            ← cubo cruzado por dimensiones
                       data/cubo_comuna.parquet         (alimenta filtros DuckDB-WASM)
                       data/cp_metrics.{parquet,json}   ← 1 fila por CP, métricas pre-agregadas
                       data/barrio_metrics.{...}        ← 1 fila por barrio
                       data/comuna_metrics.{...}        ← 1 fila por comuna
                       data/pais_sin_caba_resumen.json  ← totales del resto del país (comparativa)
                       data/caba_metadata.json          ← totales, filtros, fuentes
                       data/deudores_caba.parquet       ← 1 fila por persona (con CUIL, USO LOCAL)
                 │
                 ▼
                index.html  ← consume todo lo de arriba + 2 geojson (barrios, comunas)
```

Para regenerar todo (~3 min):
```
python armar_cp_barrio.py        # mapeo CP→barrio
python armar_poligonos_cp.py     # polígonos CP4
python cubos_caba.py             # cubos + métricas + JSON país
```


## Datos generados

### Archivos publicables (anonimizados, sin CUILs)

| Archivo | Tamaño | Contenido |
|---|---|---|
| `data/cp_comuna.csv` | 8 KB | 284 CPs con `cp4, barrio, comuna, confianza_pct, n_segmentos, n_barrios_zona, fuente`. Editable. |
| `data/cp_barrio_detalle.csv` | 15 KB | Distribución completa CP × barrio (auditoría). |
| `data/cp_metrics.{parquet,json}` | 34 KB / 367 KB | 1 fila por CP con `n_personas, n_morosos, pct_mora_*, pct_personas_con_pnfc, deuda_*_miles`. |
| `data/barrio_metrics.{...}` | 5 KB / 16 KB | Idem agregado por barrio. |
| `data/comuna_metrics.{...}` | 4 KB / 7 KB | Idem agregado por comuna (16 filas: 15 comunas + Sin clasificar). |
| `data/cubo_cp.parquet` | 5.6 MB | Cubo cruzado `cp4 × rango_etario × sexo × tramo × peor_sit × flags`. Para filtros vía DuckDB-WASM en el browser. |
| `data/cubo_comuna.parquet` | 1.5 MB | Idem para comuna. |
| `data/pais_sin_caba_resumen.json` | 0.5 KB | Resumen país (resto, excluyendo CABA) para la comparativa de la sidebar. |
| `data/caba_metadata.json` | 47 KB | Período, fecha_ref, fuentes, listas para filtros, totales globales. |
| `geo/barrios.geojson` | 663 KB | 48 polígonos de barrios CABA (OpenDataCordoba). |
| `geo/comunas.geojson` | 502 KB | 15 polígonos de comunas (disolución de barrios). |
| `geo/cps.geojson` | 1.5 MB | 255 polígonos por CP4 derivados del callejero. |

### Archivos locales (con CUILs, NO publicar)

| Archivo | Tamaño | Contenido |
|---|---|---|
| `data/deudores_caba.parquet` | 37 MB | 1 fila por persona con CUIL + todas las dimensiones + métricas. Sólo uso local (Ley 25.326). |


## Fuentes oficiales

| Dataset | URL / fuente | Versión | Atribución |
|---|---|---|---|
| **BCRA Central de Deudores** | [bcra.gob.ar/BCRAyVos/Centrales_de_informacion](https://www.bcra.gob.ar/BCRAyVos/Centrales_de_informacion.asp) | mensual (período del informe) | BCRA |
| **Padrón ARCA** | indexado en `consolidado.duckdb` | mensual | ARCA |
| **Callejero CABA** | [data.buenosaires.gob.ar/dataset/calles](https://data.buenosaires.gob.ar/dataset/calles) (CSV con `LINESTRING` + `barrio_par`/`imp`) | 4 may 2026 | GCBA / Buenos Aires Data |
| **GeoJSON Barrios CABA** | [github.com/OpenDataCordoba/barrios](https://github.com/OpenDataCordoba/barrios) (`caba_barrios.geojson`) | actual | OpenDataCordoba (mirror del GCBA) |
| **GeoJSON Comunas CABA** | derivado por *union* de barrios por columna `COMUNA` | — | propio |
| **CPA Correo Argentino** | [github.com/OpenDataCordoba/codigo-postal-argentino](https://github.com/OpenDataCordoba/codigo-postal-argentino) (`cpa_argentina.zip` → `calles.CSV` + `alturas.CSV`) | snapshot 2010 | Correo Argentino vía OpenDataCordoba |

> **Nota sobre el CPA**: el dataset del Correo Argentino disponible públicamente
> es de 2010. Las calles nuevas posteriores y los CPs reasignados pueden tener
> información desactualizada. Sin embargo, el sistema CPA argentino es muy
> estable y la gran mayoría de las asignaciones siguen vigentes. El callejero
> GCBA sí está actualizado al 2026 y se usa para los polígonos.


## Universo y métricas

Misma definición que el tablero principal (`Scripts/6_tablero_base.py`):

```sql
SELECT *
FROM deudores d
LEFT JOIN padron p ON p.cuit = d.nro_id
WHERE LEFT(d.nro_id, 1) = '2'        -- personas físicas (prefijo CUIT 20/23/27)
  AND COALESCE(p.fecha_fallecimiento, '') = ''   -- vivas
  AND COALESCE(d.gar_pref_b, 0) = 0              -- sin garantía B (excluye hipotecas/prendas)
  AND d.cartera IN ('CONSUMO_VIV', 'PNFC')        -- cartera consumo o PNFC
  AND p.provincia = '00'                          -- CABA
```

**Métricas por persona:**
- `consumo = prestamos − gar_pref_a + otros_conceptos` (miles de $)
- `es_moroso = (MAX(situacion) ≥ 3) per persona`
- `tiene_pnfc = (al menos 1 vínculo con cod_entidad no de banco/financiera regulada)`

**Métricas por geografía** (cp / barrio / comuna):
- `n_personas`, `n_morosos`, `n_con_pnfc`
- `pct_mora_personas` = `n_morosos / n_personas`
- `pct_mora_deuda` = `Σ consumo_mora / Σ consumo`
- `pct_personas_con_pnfc` = `n_con_pnfc / n_personas`
- `pct_deuda_pnfc` = `Σ consumo_pnfc / Σ consumo`
- `deuda_total_miles`, `deuda_mora_miles`, `deuda_pnfc_miles`, etc.


## Mapeo CP → barrio → comuna (lo más delicado)

El código postal en CABA es una zona de cartero. **NO** calza 1:1 con barrios
oficiales: una avenida grande puede atravesar varios barrios pero todos sus
números tener un solo CP, y al revés.

### Algoritmo (`armar_cp_barrio.py`)

1. **Match de calles**. Para cada calle del CPA en CABA, se busca su
   equivalente en el callejero GCBA. La normalización maneja:
   - Tildes y mayúsculas/minúsculas.
   - Convención inversa: callejero usa `"ACEVEDO, EDUARDO"`, CPA `"EDUARDO
     ACEVEDO"`. Tras invertir y quitar coma queda `"EDUARDO ACEVEDO"` en ambos.
   - Prefijos de título (`DR.`, `GRAL.`, `INT.`, etc.) y sufijos de tipo de
     calle (`AV`, `PJE`, etc.).
   - **Match exacto**: 1.359 calles (66%); **fallback por apellido único**:
     217 (10%); **sin match**: 492 (24%, calles muy específicas o renombradas).

2. **Atribución de barrio por overlap de altura**. Para cada par
   `(calle, altura_desde, altura_hasta)` del CPA, intersectamos con los
   segmentos del callejero (que tienen sus propios rangos por barrio).
   - Si el callejero no informa altura (segmentos `0-0`), **se descarta** el
     voto. Esto evita atribuir incorrectamente barrios para avenidas largas
     (Av. del Libertador, Corrientes) cuyos segmentos anónimos cruzan muchos
     barrios.
   - El peso es la fracción del rango del callejero cubierta por el rango del
     CPA. No usamos longitud geométrica para no sesgar a favor de avenidas.
   - Si los lados par e impar del callejero pertenecen a barrios distintos,
     repartimos `0.5/0.5`.

3. **Resolución del barrio dominante**. Por cada CP4 sumamos los pesos por
   barrio. El barrio dominante es el de mayor peso; la `confianza_pct` es
   `peso_dominante / peso_total`. Para 2026-05:
   - **116 CPs** con confianza ≥ 95% (barrio totalmente claro).
   - **23 CPs** con confianza 80-95%.
   - **51 CPs** con confianza 60-80%.
   - **63 CPs** con confianza 40-60% (marcados `(AMBIGUO)`).
   - **7 CPs** con confianza < 40% (cubren múltiples barrios genuinamente).
   El detalle por CP está en `data/cp_barrio_detalle.csv` (todos los barrios
   candidatos por CP4 con porcentaje).

4. **Overrides manuales** (`data/cp_comuna_override.csv`). Los CPs que no
   aparecen en el dataset CPA (típicamente casillas postales de oficinas en el
   Microcentro) se completan con asignaciones manuales documentadas. Sólo se
   aplican cuando NO hay datos derivados; **los datos siempre ganan** sobre el
   override.

5. **Comuna**. Cada barrio CABA pertenece a una sola comuna. Joineamos contra
   `geo/barrios.geojson` (atribución fija oficial).

### Cobertura

- **284 CPs mapeados** (260 derivados de datos + 24 overrides manuales).
- **94.2% de las personas CABA** en el padrón BCRA tienen su CP mapeado a
  comuna. El 5.8% restante son CPs sin calles en el dataset (4.6% sin CP en
  el padrón + 1.2% con CP no mapeable).

### Confianza en el panel detalle

Al clickear un CP en el mapa, junto al título aparece un chip con la confianza:
- **Verde (≥80%)**: barrio dominante claro.
- **Naranja (60-80%)**: barrio dominante razonable, otros barrios contribuyen.
- **Rojo (<60%)**: ambiguo, el CP cubre múltiples barrios genuinamente. Tomar
  con cautela la atribución a un solo barrio/comuna.


## Polígonos por CP4 (`armar_poligonos_cp.py`)

No existe un GeoJSON oficial de polígonos por código postal en CABA. Los
construimos así:

1. Para cada CP4, identificar las LINESTRING del callejero que pertenecen a
   sus calles (usando el mismo mapeo del paso anterior).
2. Aplicar un buffer de **0.0006 grados** (~60 m, media cuadra urbana CABA).
3. Hacer `unary_union` de todos los buffers → polígono cohesivo.
4. Simplificar con `simplify(5e-5)` (~5 m) para reducir tamaño.

**Resultado**: 255 polígonos por CP4 que siguen las calles. Pueden solaparse
con CPs vecinos (es la realidad: una manzana puede tener CPs distintos según
el lado de la calle). No los recortamos para que el mapa refleje esa realidad.


## Comparativa CABA vs resto del país

`pais_sin_caba_resumen.json` se genera en `cubos_caba.py` (paso 7) corriendo
el mismo canon pero con `p.provincia <> '00'`. Da el agregado nacional
**sin** CABA, que el HTML usa para mostrar deltas en cada KPI.

Resultado típico (202603):
| Métrica | CABA | Resto país | Delta |
|---|---:|---:|---:|
| Personas con consumo > 0 | 2.001.382 | 18.276.136 | — |
| % personas en mora | 15,1% | 27,4% | **-12,3 pp** ✓ |
| % monto en mora | 17,2% | 21,7% | **-4,5 pp** ✓ |
| % personas con PNFC | 42,0% | 63,9% | **-21,9 pp** ✓ |

CABA tiene niveles de morosidad y dependencia de PNFCs significativamente
menores que el resto del país.


## Privacidad

Sigue las mismas reglas que el tablero principal:

- **`data/deudores_caba.parquet`** contiene CUILs y NO debe publicarse
  (Ley 25.326 / datos personales). Sólo para análisis local.
- Los cubos `cubo_cp.parquet` y `cubo_comuna.parquet` están agregados
  (sin CUIL), publicables.
- Los JSON pequeños (`*_metrics.json`, `caba_metadata.json`, etc.) son
  agregados y publicables.

Si este informe se publica como GitHub Pages, asegurarse de incluir el
`.gitignore` apropiado o filtrar manualmente los archivos.


## Cambios futuros / TODOs

- [ ] Agregar selector de período en la cabecera (hoy lee el período activo
      de `caba_metadata.json` fijado por la corrida del script).
- [ ] Refinar `cp_comuna_override.csv` con auditoría de los 7 CPs con
      confianza < 40%.
- [ ] Investigar fuentes alternativas para CPs sin match en CPA (catalogación
      reciente del Correo Argentino) y volverlos derivados en lugar de manual.
- [ ] Vista histórica del mapa (24 meses) si hay demanda.
