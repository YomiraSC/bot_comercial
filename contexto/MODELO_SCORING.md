# Modelo de Scoring — Documentacion Tecnica
# Bot Comercial Maqui+

## Resumen

El sistema usa **dos modelos de scoring** para diferentes momentos del funnel:

| Modelo | Momento | Data | Output | Archivo |
|--------|---------|------|--------|---------|
| Score PREVIO | Antes del bot | CRM historico | VERDE/AMARILLO/ROJO | `scoring_ml.py` |
| Score POST | Durante/despues del bot | Conversacion | 0-1 continuo | `help_helpers.py` |

---

## Score PREVIO Conversacion (LightGBM)

### Proposito
Filtrar la base stock (~120k leads) antes de gastardinero en API + WhatsApp.

### Archivo
`archivos originales/scoring/scoring_ml.py`

### Variables del modelo

| Tipo | Variable | Descripcion |
|------|----------|-------------|
| Categorica | `Linea_key` | Vehiculos o Inmuebles |
| Categorica | `Origen_key` | De donde vino el lead |
| Categorica | `SubOrigen_key` | Detalle del origen |
| Categorica | `Sede_key` | Sede asignada |
| Numerica | `dias_desde_captura` | Antiguedad del lead |
| Numerica | `delay_asignacion_h` | Horas entre captura y asignacion |
| Ordinal | `estado_prospecto_score` | Avance en embudo (0-85) |
| Ordinal | `cita_info_score` | Resultado cita informativa |
| Binaria | `tiene_cita_cierre` | Tiene cita de cierre? |
| Binaria | `motivo_descarte_definitivo` | Fuera de zona, no interesado, etc. |
| Binaria | `motivo_descarte_temporal` | No contesta, numero apagado |
| Binaria | `es_reasignado` | Fue reasignado? |
| Binaria | `has_prev_sale` | Ya compro en fondos antes? |

### Output
- Score 0-1000
- Buckets: VERDE (>=700), AMARILLO (>=450), ROJO (<450)

### Uso
Solo para leads stock. Los leads campana no tienen historial CRM.

---

## Score POST Conversacion (Formula Ponderada)

### Proposito
Determinar si el lead esta listo para derivar a un asesor basandose en su comportamiento durante la conversacion con el bot.

### Archivo
`help_helpers.py` (implementado)

### Variables del modelo (5)

| # | Variable | Valores | Peso |
|---|----------|---------|------|
| 1 | Sentimiento | negativo=0, neutral=0.5, positivo=1 | 20% |
| 2 | Nivel de interes | bajo=0, medio=0.5, alto=1 | 25% |
| 3 | Tiempo de respuesta | >60min=0, 10-60min=0.3, 2-10min=0.7, <2min=1 | 15% |
| 4 | Intencion de compra | baja=0, media=0.5, alta=1 | 30% |
| 5 | Datos capturados | 0-1 (% de DNI, nombre, correo) | 10% |

### Detalle de cada variable

#### 1. Sentimiento
Detectado por GPT-4 en cada mensaje del lead.

| Valor | Descripcion | Score |
|-------|-------------|-------|
| Negativo | Molestia, desconfianza, objecion marcada | 0.0 |
| Neutral | Informativo, sin emocion clara | 0.5 |
| Positivo | Tono colaborativo, apertura a avanzar | 1.0 |

#### 2. Nivel de Interes
Detectado por GPT-4 segun el contenido del mensaje.

| Valor | Senales | Score |
|-------|---------|-------|
| Alto | Pregunta cotizacion, precio, cuota, tasa, requisitos | 1.0 |
| Medio | Pregunta beneficios, compara opciones, tiempos de entrega | 0.5 |
| Bajo | Pregunta general/"info", solo dice "ok" y sigue | 0.0 |

#### 3. Tiempo de Respuesta
Calculado automaticamente: `hora(mensaje_lead) - hora(pregunta_bot)`

| Tiempo | Interpretacion | Score |
|--------|----------------|-------|
| < 2 min | Alto engagement | 1.0 |
| 2-10 min | Engagement medio | 0.7 |
| 10-60 min | Engagement bajo | 0.3 |
| > 60 min | Penalizacion fuerte / abandono | 0.0 |
| Primer mensaje | Neutral (sin referencia) | 0.5 |

#### 4. Intencion de Compra
Detectado por GPT-4 segun senales explicitas.

| Valor | Senales | Score |
|-------|---------|-------|
| Alta | Declara "quiero comprar", pide asesor/llamada/cita, define preferencias concretas | 1.0 |
| Media | Cerca de agendar pero no confirma | 0.5 |
| Baja | Evita avanzar, no da datos, rechazo, abandono | 0.0 |

#### 5. Datos Capturados
Calculado automaticamente segun datos del lead en BD.

| Datos | Score |
|-------|-------|
| 0 de 3 (nada) | 0.0 |
| 1 de 3 (ej: solo nombre) | 0.33 |
| 2 de 3 (ej: nombre + DNI) | 0.67 |
| 3 de 3 (DNI + nombre + correo) | 1.0 |

### Formula

```python
# 1. Calcular raw_score ponderado
raw_score = (
    0.20 * sentimiento +
    0.25 * interes +
    0.15 * tiempo_respuesta +
    0.30 * intencion +
    0.10 * datos_capturados
)

# 2. Suavizar con EMA (mantiene historial)
score_nuevo = score_anterior * 0.6 + raw_score * 0.4
```

### Output y Decisiones

| Score | Accion |
|-------|--------|
| >= 0.75 | DERIVAR a asesor (pasa a routing) |
| 0.30 - 0.74 | Seguir nutriendo con bot |
| < 0.30 + intencion baja + sentimiento negativo | DESCARTAR |

### Configuracion

Los pesos y umbrales se pueden ajustar desde la tabla `config_modelo` en PostgreSQL sin modificar codigo:

```sql
-- Ejemplo de config_modelo
INSERT INTO comercial.config_modelo (parametro, valor) VALUES
('w_sentimiento', '0.20'),
('w_interes', '0.25'),
('w_tiempo', '0.15'),
('w_intencion', '0.30'),
('w_datos', '0.10'),
('alpha_ema', '0.40'),
('umbral_derivacion', '0.75'),
('umbral_descarte', '0.30');
```

---

## Comparacion de Modelos

| Aspecto | Score PREVIO | Score POST |
|---------|--------------|------------|
| Tipo | ML (LightGBM) | Formula ponderada |
| Entrenamiento | Requiere datos historicos | No requiere entrenamiento |
| Momento | Batch (antes del bot) | Real-time (cada mensaje) |
| Variables | 13 (CRM) | 5 (conversacion) |
| Output | Categorico (3 buckets) | Continuo (0-1) |
| Ajuste | Re-entrenar modelo | Cambiar pesos en BD |

---

## Evolucion Futura

### Corto plazo (piloto)
- Usar formula ponderada actual
- Recolectar datos de conversaciones reales
- Ajustar pesos segun resultados

### Mediano plazo (3-6 meses post-piloto)
- Entrenar modelo ML para Score POST con datos reales
- Features adicionales: tendencia de sentimiento, keywords, patrones
- Posible unificacion de ambos scores en un solo modelo

---

## Archivos Relacionados

| Archivo | Contenido |
|---------|-----------|
| `help_helpers.py` | Funciones de scoring post conversacion |
| `app_comercial.py` | Bot principal, usa las funciones de scoring |
| `archivos originales/scoring/scoring_ml.py` | Modelo LightGBM para score previo |
| `comercial_schema_final.sql` | Esquema BD con tablas de scoring |
| `config_modelo` (tabla BD) | Pesos y umbrales configurables |

---

*Documento tecnico del modelo de scoring para el Bot Comercial Maqui+*
