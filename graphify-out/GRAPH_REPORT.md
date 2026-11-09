# Graph Report - camino-al-703  (2026-11-09)

## Corpus Check
- Corpus is ~28,971 words - fits in a single context window. You may not need a graph.

## Summary
- 264 nodes · 491 edges · 11 communities (10 shown, 1 thin omitted)
- Extraction: 93% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 31 edges (avg confidence: 0.88)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Plan de entreno y nutrición (UI)
- Resumen de forma Garmin
- Informe diario (report.py)
- Cliente API Garmin (gapi.py)
- Cambios de plan con Claude
- Sincronización y CLI diario
- Sesiones: bici, carrera, cadera
- App Camino al 70.3 y registro
- Archivos FIT y métricas
- Exportación panel (export_dash)

## God Nodes (most connected - your core abstractions)
1. `Garmin` - 20 edges
2. `main()` - 15 edges
3. `build()` - 12 edges
4. `show() (navegación de pestañas)` - 11 edges
5. `GarminError` - 10 edges
6. `main()` - 8 edges
7. `r()` - 8 edges
8. `cargas()` - 8 edges
9. `WEEKS (plan semana a semana)` - 8 edges
10. `renderHoy()` - 8 edges

## Surprising Connections (you probably didn't know these)
- `renderSemana()` --semantically_similar_to--> `drawSemanas()`  [INFERRED] [semantically similar]
  index.html → resumen-diario/panel/index.html
- `formaCardHTML()` --semantically_similar_to--> `drawHeader()`  [INFERRED] [semantically similar]
  index.html → resumen-diario/panel/index.html
- `Colección db 'forma'` --shares_data_with--> `Fuente 'informes'`  [AMBIGUOUS]
  index.html → resumen-diario/panel/index.html
- `resumen-diario/ (resumen diario Garmin)` --references--> `Resumen diario de forma (panel)`  [INFERRED]
  README.md → resumen-diario/panel/index.html
- `Fuente 'informes'` --conceptually_related_to--> `resumen-diario/ (resumen diario Garmin)`  [INFERRED]
  resumen-diario/panel/index.html → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Plan change request flow (prompt → Claude → normalize → db)** — index_askchange, index_chgprompt, index_claude_sample_capability, index_normsession, index_applychange, index_db_cambios, index_applyoverrides [EXTRACTED 1.00]
- **Session fueling pipeline** — index_fuelof, index_fuelfor, index_fuelcore, index_addplan, index_fuelhtml [EXTRACTED 1.00]
- **Daily report panel render** — resumen_diario_panel_index_draw, resumen_diario_panel_index_drawheader, resumen_diario_panel_index_drawactividades, resumen_diario_panel_index_drawestado, resumen_diario_panel_index_drawcarga, resumen_diario_panel_index_drawsemanas [EXTRACTED 1.00]

## Communities (11 total, 1 thin omitted)

### Community 0 - "Plan de entreno y nutrición (UI)"
Cohesion: 0.07
Nodes (32): dayGroup(), EX (ejercicios de cadera), fillBoc(), fillCalc(), fillCarreras(), fillComida(), fillNext(), FP (preferencias de gel y bocadillitos) (+24 more)

### Community 1 - "Resumen de forma Garmin"
Cohesion: 0.08
Nodes (35): Colección db 'forma', FORMA (resumen diario de forma), formaCardHTML(), PANEL (enlace al informe con gráficas), Garmin Connect Personal API, resumen-diario/ (resumen diario Garmin), Fuente 'actividades', axisStyle() (+27 more)

### Community 2 - "Informe diario (report.py)"
Cohesion: 0.13
Nodes (20): alertas(), bienestar(), build(), cargas(), fmt_val(), _hour(), informes_previos(), load() (+12 more)

### Community 3 - "Cliente API Garmin (gapi.py)"
Cohesion: 0.10
Nodes (3): Garmin, GarminError, _jwt_exp()

### Community 4 - "Cambios de plan con Claude"
Cohesion: 0.10
Nodes (25): addPlan(), applyChange(), askChange(), askFood(), CHG (estado de petición de cambio), chgPrompt(), window.claude.use('db') (base de datos claude.ai), window.claude.use('sample') (preguntar a Claude) (+17 more)

### Community 5 - "Sincronización y CLI diario"
Cohesion: 0.22
Nodes (11): main(), connect(), days(), fetch_range(), load_cfg(), main(), now(), sync_activities() (+3 more)

### Community 6 - "Sesiones: bici, carrera, cadera"
Cohesion: 0.12
Nodes (17): applyOverrides(), Parámetros del atleta (FTP, FTP_TEST), bici(), BIKE (entrenamientos Zwift .zwo), cadera(), CADF (cadencia por fases), cadPhase(), cadRun() (+9 more)

### Community 7 - "App Camino al 70.3 y registro"
Cohesion: 0.12
Nodes (13): Camino al 70.3 app (index.html), Colección db 'log', LOG (registro de sesiones), lsSave(), persist(), RACES (objetivos de carrera), renderTemporada(), Pestañas Hoy/Semana/Comida/Cambios/Más (+5 more)

### Community 8 - "Archivos FIT y métricas"
Cohesion: 0.19
Nodes (8): crc16(), reparar(), _best(), fmt_pace(), _np(), parse(), _rolling(), _zone_times()

### Community 9 - "Exportación panel (export_dash)"
Cohesion: 0.36
Nodes (10): actividades(), carga(), diario(), dtxt(), dur_txt(), forma_web(), met(), informes() (+2 more)

## Ambiguous Edges - Review These
- `Colección db 'forma'` → `Fuente 'informes'`  [AMBIGUOUS]
  index.html · relation: shares_data_with

## Knowledge Gaps
- **17 isolated node(s):** `Medio Ironman 70.3 (mayo 2027)`, `Garmin Connect Personal API`, `EX (ejercicios de cadera)`, `SESS / DAYMAP (índice de sesiones)`, `FOOD (comida de hoy)` (+12 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 66 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `show() (navegación de pestañas)` connect `Plan de entreno y nutrición (UI)` to `Sesiones: bici, carrera, cadera`, `App Camino al 70.3 y registro`?**
  _High betweenness centrality (0.107) - this node is a cross-community bridge._
- **What connects `Medio Ironman 70.3 (mayo 2027)`, `Garmin Connect Personal API`, `EX (ejercicios de cadera)` to the rest of the system?**
  _17 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Plan de entreno y nutrición (UI)` be split into smaller, more focused modules?**
  _Cohesion score 0.06951219512195123 - nodes in this community are weakly interconnected._
- **Why does `Camino al 70.3 app (index.html)` connect `App Camino al 70.3 y registro` to `Plan de entreno y nutrición (UI)`, `Sesiones: bici, carrera, cadera`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Should `Resumen de forma Garmin` be split into smaller, more focused modules?**
  _Cohesion score 0.08392603129445235 - nodes in this community are weakly interconnected._
- **Should `Informe diario (report.py)` be split into smaller, more focused modules?**
  _Cohesion score 0.1268939393939394 - nodes in this community are weakly interconnected._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `Colección db 'forma'` and `Fuente 'informes'`?**
  _Edge tagged AMBIGUOUS (relation: shares_data_with) - confidence is low._