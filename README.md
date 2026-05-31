# Informe CABA — Distribución geográfica de la deuda de consumo

Mapa interactivo de la morosidad y la incidencia de PNFCs (Proveedores No
Financieros de Crédito) en CABA, agregado por **código postal**, **barrio** o
**comuna**, con filtros por sexo, edad, tramo de deuda y tipo de acreedor.

Replica el canon del tablero principal (consumo puro = `prestamos − gar_pref_a
+ otros_conceptos`, PF vivas, `gar_pref_b = 0`, cartera ∈ `CONSUMO_VIV / PNFC`,
provincia ARCA = `'00'`).

> **Período activo: `202604` (datos a abril 2026, fecha ref. edad `2026-04-30`).**
> El período no está hardcodeado: `cubos_caba.py` lo lee de la tabla `metadata`
> de `consolidado.duckdb` y el `index.html` lo toma de `caba_metadata.json`.
> Para actualizar a un mes nuevo basta reindexar el consolidado del repo padre y
> volver a correr `cubos_caba.py` (ver "Actualizar el período" abajo).


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
   armar_cp_barrio.py  →  data/cp_comuna.csv          ← mapeo CP4 → barrio dominante → comuna
                          data/cp_barrio_detalle.csv   ← TODA la distribución CP×barrio (pesos)
                 │
                 ▼
   armar_poligonos_cp.py → geo/cps.geojson             ← 255 polígonos por CP4 derivados
                 │                                         del callejero (buffer 60m + union)
                 ▼
   cubos_caba.py     → data/cubo_cp.parquet            ← cubo cruzado por dimensiones (atómico)
                       data/cp_barrio_weights.json      ← reparto CP4 → [(barrio, comuna, frac)]
                       data/cp_metrics.{parquet,json}   ← 1 fila por CP, métricas pre-agregadas
                       data/barrio_metrics.{...}        ← 1 fila por barrio (REPARTO proporcional)
                       data/comuna_metrics.{...}        ← 1 fila por comuna (REPARTO proporcional)
                       data/pais_sin_caba_resumen.json  ← totales del resto del país (comparativa)
                       data/caba_metadata.json          ← totales, filtros, fuentes
                       data/deudores_caba.parquet       ← 1 fila por persona (con CUIL, USO LOCAL)
                 │
                 ▼
                index.html  ← consume todo lo de arriba + 2 geojson (barrios, comunas)
                              · CP: usa cubo_cp directo (exacto)
                              · Barrio/Comuna: cubo_cp + cp_barrio_weights (reparto en el browser)
```

### Actualizar el período (refresh de datos, ~1 min)

El mapeo CP→barrio (`armar_cp_barrio.py`) y los polígonos (`armar_poligonos_cp.py`)
son **geográficos y no dependen del mes**. Para un cambio de período sólo hace
falta el último paso, que lee el período activo de `consolidado.duckdb`:
```
python Otras/Informe_CABA/cubos_caba.py    # corre desde el repo padre
```
Esto regenera todos los `data/*.parquet|json` (cubos, métricas, metadata y
`pais_sin_caba_resumen.json`). El `index.html` toma el período nuevo de
`caba_metadata.json` automáticamente — no hay que tocar el HTML.

### Regenerar todo desde cero (incluye geografía, ~3 min)
```
python armar_cp_barrio.py        # mapeo CP→barrio
python armar_poligonos_cp.py     # polígonos CP4
python cubos_caba.py             # cubos + métricas + JSON país
```


## Datos generados

### Archivos publicables (anonimizados, sin CUILs)

| Archivo | Tamaño | Contenido |
|---|---|---|
| `data/cp_comuna.csv` | 8 KB | 291 CPs con `cp4, barrio, comuna, confianza_pct, n_segmentos, n_barrios_zona, fuente`. `barrio` = dominante (referencia). Editable. |
| `data/cp_barrio_detalle.csv` | 15 KB | Distribución completa CP × barrio con `peso` y `pct`. **Fuente de los pesos de reparto.** |
| `data/cp_barrio_weights.json` | 17 KB | Reparto `cp4 → [[barrio, comuna, frac], …]` (frac normalizado a Σ=1). Lo consume el browser para repartir bajo filtros igual que el pipeline offline. |
| `data/cp_metrics.{parquet,json}` | 34 KB / 367 KB | 1 fila por CP con `n_personas, n_morosos, pct_mora_*, pct_personas_con_pnfc, deuda_*_miles`. **Exacto** (el CP es la unidad atómica). |
| `data/barrio_metrics.{...}` | 5 KB / 23 KB | Idem por barrio, **por reparto proporcional** (49 filas: 48 barrios + Sin clasificar). |
| `data/comuna_metrics.{...}` | 4 KB / 7 KB | Idem por comuna, **por reparto proporcional** (16 filas: 15 comunas + Sin clasificar). |
| `data/cubo_cp.parquet` | 5.6 MB | Cubo cruzado `cp4 × rango_etario × sexo × tramo × peor_sit × flags`. Para filtros vía DuckDB-WASM en el browser. Barrio y comuna se derivan de éste + `cp_barrio_weights.json` (no hay cubo_comuna). |
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
>
> **¿Por qué no scrapeamos el Correo para refrescar?** El buscador oficial de
> CPA (`correoargentino.com.ar/formularios/cpa`) está detrás de Google
> reCAPTCHA, por lo que el scraping masivo no es viable ni legítimo. Además, el
> límite real de precisión no es el CPA: el padrón BCRA guarda **sólo el CP de 4
> dígitos** por persona, así que ningún refresh permite ubicar a un individuo en
> un barrio dentro de su CP. La mejora efectiva fue (a) el **reparto
> proporcional** y (b) **mejorar el matching** calle↔callejero (24% → 11% sin
> match), que refina los pesos con los datos del Correo que ya tenemos.


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
oficiales: un mismo CP4 suele cubrir **varios** barrios (especialmente en el
oeste, donde el `1407` toca 11-12 barrios). Por eso **no** asignamos cada CP a
un único barrio: **repartimos** sus datos entre los barrios que cubre.

### Algoritmo (`armar_cp_barrio.py` → pesos; `cubos_caba.py` → reparto)

1. **Match de calles**. Para cada calle del CPA en CABA, se busca su
   equivalente en el callejero GCBA. La normalización maneja tildes,
   mayúsculas, la convención inversa (callejero `"ACEVEDO, EDUARDO"` ↔ CPA
   `"EDUARDO ACEVEDO"`), prefijos de título (`DR.`, `GRAL.`, …) y sufijos de
   tipo de calle (`AV`, `PJE`, …). Cascada de match (`resolver_match`):
   - **Match exacto** (nombre normalizado idéntico): 1.359 (66%).
   - **Por apellido** (el apellido identifica una sola calle; incluye calles
     del callejero de un solo token como `ANDONAEGUI`, `AGRELO`): 430 (21%).
   - **Por tokens** (apellido ambiguo pero un nombre de pila compartido lo
     desambigua, ej. `ANGEL CARRANZA` → `ANGEL JUSTINIANO CARRANZA`): 53 (3%).
   - **Sin match**: 226 (11%, variantes ortográficas, iniciales genuinamente
     ambiguas como `A/V ALSINA`, o calles ausentes del callejero).

2. **Pesos por barrio (overlap de altura)**. Para cada par
   `(calle, altura_desde, altura_hasta)` del CPA, intersectamos con los
   segmentos del callejero (que tienen rangos por barrio).
   - Si el callejero no informa altura (segmentos `0-0`), **se descarta** el
     voto. Evita atribuir mal barrios en avenidas largas (Libertador,
     Corrientes) cuyos segmentos anónimos cruzan muchos barrios.
   - El peso es la fracción del rango del callejero cubierta por el CPA (no
     longitud geométrica, para no sesgar a favor de avenidas).
   - Si los lados par/impar del callejero son barrios distintos, `0.5/0.5`.
   Resultado: por cada CP4, un vector de pesos por barrio
   (`data/cp_barrio_detalle.csv`).

3. **Reparto proporcional (areal interpolation, `cubos_caba.py`)**. Los
   agregados de cada CP (personas, deuda, mora, PNFC) se **distribuyen** entre
   sus barrios según los pesos normalizados (`frac`, Σ=1). En vez de asignar
   todo al barrio dominante (winner-take-all), cada barrio recibe su parte.
   - **Conserva los totales**: Σ barrios = Σ CPs = total global.
   - **Puebla los 48 barrios**: ningún barrio queda en cero por estar siempre
     "tapado" por un vecino más grande dentro del mismo CP.
   - Es insesgado si los pesos ≈ share real de direcciones por barrio.
   El reparto se exporta a `data/cp_barrio_weights.json` y el browser lo usa
   para repartir también bajo filtros (sexo/edad/tramo) — mismo criterio
   offline y online.

4. **Overrides manuales** (`data/cp_comuna_override.csv`). Los CPs sin calles
   en el CPA (casillas postales del Microcentro) se completan con asignaciones
   manuales (frac=1 a su único barrio). Sólo cuando NO hay datos derivados;
   **los datos siempre ganan**.

5. **Comuna**. Cada barrio pertenece a una sola comuna. El reparto a comuna se
   deriva sumando las `frac` por comuna (corrige CPs que cruzan límites de
   comuna, ej. `1430` reparte entre C12/C13/C15).

### Cobertura

- **291 CPs mapeados** (279 derivados de datos + 12 overrides manuales). El
  mejor matching recuperó ~270 calles antes sin match (24% → 11%) y subió los
  CPs derivados de 260 a 279.
- **94.3% de las personas CABA** en el padrón BCRA tienen su CP mapeado. El
  ~5.7% restante (Sin clasificar) son CPs sin CP en el padrón o no mapeables.

### Naturaleza de los valores y confianza

- Los valores por **CP4 son exactos** (el CP es la unidad del dato).
- Los valores por **barrio y comuna son estimaciones por reparto**: el padrón
  BCRA guarda sólo el CP de 4 dígitos por persona, así que es imposible ubicar
  a cada individuo en un barrio exacto. El reparto proporcional es la mejor
  estimación posible y conserva los totales.

Al clickear un CP en el mapa, el chip de confianza indica cuán concentrado está
ese CP en un solo barrio:
- **Verde (≥80%)**: el CP es casi todo un barrio → reparto ~directo.
- **Naranja (60-80%)**: un barrio domina pero otros contribuyen.
- **Rojo (<60%)**: el CP cubre genuinamente varios barrios → el reparto
  distribuye entre ellos según peso.


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

Resultado a **202604** (abril 2026):
| Métrica | CABA | Resto país | Delta |
|---|---:|---:|---:|
| Personas con consumo > 0 | 2.005.886 | 18.345.821 | — |
| % personas en mora | 15,6% | 28,2% | **-12,6 pp** ✓ |
| % monto en mora | 18,3% | 22,8% | **-4,5 pp** ✓ |
| % personas con PNFC | 42,7% | 64,2% | **-21,5 pp** ✓ |

CABA tiene niveles de morosidad y dependencia de PNFCs significativamente
menores que el resto del país.


## Git, branches y deploy

Este informe vive en su **propio repositorio** (no es parte del repo padre
`deudas_analisis`), con remoto en
[Capsula12/informe_caba_deudas](https://github.com/Capsula12/informe_caba_deudas).

Dos branches que **se mantienen en espejo**:

| Branch  | Uso |
|---|---|
| `main`  | Rama de trabajo / referencia. |
| `index` | Rama que sirve la **GitHub Page**. En general coincide 1:1 con `main`. |

**Flujo de actualización de período** (después de correr `cubos_caba.py`):

```bash
# desde Otras/Informe_CABA, parado en main
git add -A
git commit -m "Update datos CABA a <PERIODO>"
git push origin main

# espejar index a main y publicar
git checkout index
git merge --ff-only main      # index suele ser ancestro de main → fast-forward
git push origin index
git checkout main             # volver a la rama de trabajo
```

> `data/deudores_caba.parquet` (CUILs) está en `.gitignore` y **no se commitea**.
> Todo lo demás bajo `data/` (cubos, métricas, metadata) sí se versiona para que
> la GitHub Page tenga los datos sin pipeline. Si `index` y `main` divergen
> (p. ej. un fix sólo en una), resolver con `git merge` en vez de `--ff-only`.


## Privacidad

Sigue las mismas reglas que el tablero principal:

- **`data/deudores_caba.parquet`** contiene CUILs y NO debe publicarse
  (Ley 25.326 / datos personales). Sólo para análisis local.
- El cubo `cubo_cp.parquet` y los pesos `cp_barrio_weights.json` están
  agregados (sin CUIL), publicables.
- Los JSON pequeños (`*_metrics.json`, `caba_metadata.json`, etc.) son
  agregados y publicables.

Si este informe se publica como GitHub Pages, asegurarse de incluir el
`.gitignore` apropiado o filtrar manualmente los archivos.


## Cambios futuros / TODOs

- [x] **Reparto proporcional** de los datos por CP entre sus barrios (areal
      interpolation) en vez de winner-take-all. Puebla los 48 barrios y conserva
      totales. Offline (`cubos_caba.py`) y online (`cp_barrio_weights.json`).
- [x] **Mejorar el matching** calle CPA ↔ callejero GCBA (índice de apellido
      para calles de un solo token + desambiguación por tokens): 24% → 11% sin
      match, 260 → 279 CPs derivados.
- [ ] Agregar selector de período en la cabecera (hoy lee el período activo
      de `caba_metadata.json` fijado por la corrida del script).
- [ ] Ponderar el reparto por cantidad de direcciones (altura) o densidad
      poblacional por barrio, en vez de por fracción de rango de calle, para
      afinar el share dentro de cada CP.
- [ ] Recuperar parte del 11% de calles aún sin match (variantes ortográficas
      tipo `BERUTI`/`BERUTTI`); las iniciales ambiguas (`A/V ALSINA`) no son
      resolubles con el dato disponible.
- [ ] Vista histórica del mapa (24 meses) si hay demanda.
