# Contexto de Negocio — Bot Comercial Maqui+

## Sobre Maqui+

**Maqui+** es una empresa de **fondo colectivo** en Peru.

### Que es un fondo colectivo?

Un grupo de personas se junta para financiar un bien (vehiculo o inmueble en el caso de Maqui+):

1. Cada miembro aporta una cantidad mensualmente
2. Cada mes, por sorteo, a uno del grupo le toca el bien
3. El que recibe el bien sigue pagando el resto de meses
4. Al final, todos terminan con su bien adjudicado
5. En el peor caso, a alguien le toca en el ultimo mes

**Beneficio principal**: Tasas de interes menores que las de financiacion bancaria tradicional.

Este modelo es mas conocido en Brasil y recientemente en Colombia, pero en Peru aun no es muy popular.

---

## Situacion Actual (Sin el Piloto)

```
Campana Meta Ads
      |
      v
Formulario Web (nombre, correo, telefono, producto, zona)
      |
      v
Asignacion ALEATORIA a un asesor/vendedor
      |
      v
Asesor contacta al lead por WhatsApp
      |
      v
Conversacion: informar sobre fondo colectivo, resolver dudas, agendar cita, etc.
      |
      v
Si cierra venta → registra cliente
```

### Problemas identificados

| Problema | Impacto |
|----------|---------|
| Asignacion aleatoria de asesores | No hay match optimo lead-asesor |
| No hay filtro de calidad de leads | Leads curiosos mezclados con leads reales |
| Formulario permite datos falsos | Leads con informacion sin sentido |
| Asesores con capacidad limitada | Pierden tiempo con leads frios |
| Sin datos de comportamiento | Solo datos declarativos del formulario |

---

## Propuesta: Estrategia Retadora (Piloto)

### Nuevo flujo

```
Campana Meta Ads
      |
      v
BOT DE WHATSAPP (en lugar de formulario)
      |
      v
Conversacion inteligente:
  - Captura datos explicitos (nombre, DNI, correo, producto de interes)
  - Captura datos implicitos (tiempo de respuesta, sentimiento, engagement)
  - Informa sobre fondo colectivo
  - Resuelve dudas basicas
      |
      v
MODELO DE SCORING
  - Analiza todos los datos capturados
  - Clasifica: lead CALIENTE / TIBIO / FRIO
  - Filtra leads realmente interesados
      |
      v
MODELO DE ROUTING
  - Usa data historica de asesores
  - Determina el MEJOR asesor para ESE lead especifico
  - Optimiza probabilidad de conversion
      |
      v
Asesor recibe lead:
  - Ya calificado (score)
  - Con contexto completo de la conversacion
  - Asignado especificamente a el/ella
      |
      v
Mayor probabilidad de conversion
```

### Componentes del piloto

| Componente | Funcion |
|------------|---------|
| **Bot Comercial** | Conversacion inicial, captura de datos, nutricion del lead |
| **Modelo de Scoring** | Clasificacion de leads (caliente/tibio/frio) basado en comportamiento |
| **Modelo de Routing** | Asignacion inteligente lead-asesor basado en data historica |
| **Base de Datos** | Almacena leads, scoring, historial de conversaciones |

---

## Alcance del Bot

El bot comercial termina su tarea cuando:

1. El lead ha sido **scoreado** (modelo de scoring ejecutado)
2. Su informacion esta **registrada en la base de datos**
3. La **conversacion ha terminado** (lead informado, dudas resueltas)

Nota: El routing posiblemente se ejecutara en un programa aparte, no directamente en el bot. Esto se definira al integrar el modelo de routing.

---

## Ventajas de la Estrategia Retadora

| Aspecto | Antes (Formulario) | Despues (Bot + ML) |
|---------|-------------------|-------------------|
| Datos capturados | Solo declarativos | Declarativos + comportamiento |
| Calidad de datos | Puede ser basura | Validados en conversacion |
| Filtro de leads | Ninguno | Scoring ML |
| Asignacion asesor | Aleatoria | Routing inteligente |
| Contexto para asesor | Minimo | Conversacion completa + score |
| Tiempo de asesores | Desperdiciado en leads frios | Enfocado en leads calientes |

---

## Tipos de Leads

El piloto trabaja con dos tipos de leads:

### Lead Campaña (nuevos)

- **Origen**: Meta Ads (campañas activas)
- **Caracteristica**: No tienen historial previo
- **Flujo**: Meta Ads → Bot WhatsApp → Score post conversacion
- **Scoring**: Solo score POST conversacion

### Lead Stock (base historica)

- **Origen**: Base de datos historica de leads no convertidos
- **Volumen**: ~120,000 leads
- **Caracteristica**: Fueron tratados antes pero no cerraron venta
- **Objetivo**: "Recalentamiento" — intentar convertir leads que quedaron frios
- **Flujo**: Score previo → Filtro → Bot WhatsApp → Score post conversacion
- **Scoring**: Score PREVIO + Score POST conversacion

---

## Sistema de Scoring Dual

Para optimizar costos (API ChatGPT + envios Meta Business), el sistema usa dos tipos de scoring:

### Score Previo Conversacion

| Aspecto | Detalle |
|---------|---------|
| **Cuando se aplica** | ANTES de que el lead hable con el bot |
| **Para quien** | Leads stock (base historica) |
| **Data que usa** | Data historica del CRM: estado prospecto, citas, motivo descarte, reasignaciones, etc. |
| **Proposito** | Filtrar leads frios de la base stock para no gastar dinero en ellos |
| **Modelo** | `scoring_ml.py` (LightGBM) — YA EXISTE |
| **Output** | VERDE (caliente) / AMARILLO (tibio) / ROJO (frio) |

### Score Post Conversacion

| Aspecto | Detalle |
|---------|---------|
| **Cuando se aplica** | DESPUES de que el lead conversa con el bot |
| **Para quien** | Todos los leads que pasan por el bot (campana + stock filtrados) |
| **Data que usa** | Data de la conversacion: sentimiento, nivel de interes, intencion de compra, tiempo de respuesta, engagement |
| **Proposito** | Determinar si el lead esta listo para derivar a un asesor |
| **Modelo** | `help_helpers.py` (formula ponderada, 5 variables) — IMPLEMENTADO |
| **Output** | Score 0-1 que determina derivacion a asesor |

### Flujo completo por tipo de lead

```
LEAD CAMPAÑA (nuevo de Meta Ads)
================================
Meta Ads
    |
    v
Bot WhatsApp (conversa, captura datos)
    |
    v
Score POST conversacion
    |
    v
Si score alto → Routing → Asesor


LEAD STOCK (base historica de 120k)
===================================
Base de 120k leads
    |
    v
Score PREVIO conversacion (modelo LightGBM)
    |
    +-- ROJO (frio) → NO se contacta (ahorro de costos)
    |
    +-- AMARILLO/VERDE → Bot WhatsApp (conversa)
                              |
                              v
                         Score POST conversacion
                              |
                              v
                         Si score alto → Routing → Asesor
```

### Por que dos scores?

1. **Optimizacion de costos**: No tiene sentido gastar en API + envios para 120k leads. Filtramos primero.
2. **Data disponible diferente**: Leads stock tienen historial CRM, leads campaña no.
3. **Momento diferente**: Previo es estatico (data historica), Post es dinamico (comportamiento en chat).

---

## Estado Actual de los Modelos

| Modelo | Archivo | Estado |
|--------|---------|--------|
| Score PREVIO conversacion | `archivos originales/scoring/scoring_ml.py` | Desarrollado (LightGBM) |
| Score POST conversacion | `help_helpers.py` | IMPLEMENTADO (formula ponderada) |
| Routing | `archivos originales/routing/` | Desarrollado (por revisar) |

---

## Glosario

| Termino | Definicion |
|---------|-----------|
| **Lead** | Persona interesada que aun no es cliente |
| **Lead caliente** | Alta probabilidad de convertirse en cliente |
| **Lead tibio** | Probabilidad media, necesita mas nurturing |
| **Lead frio** | Baja probabilidad, posiblemente solo curioso |
| **Asesor/Vendedor** | Personal humano que cierra la venta |
| **Scoring** | Proceso de asignar puntaje al lead |
| **Routing** | Proceso de asignar el mejor asesor al lead |
| **Conversion** | Cuando un lead se vuelve cliente (cierra la venta) |
| **Adjudicacion** | Cuando al miembro del fondo le toca su bien |
| **Lead campaña** | Lead nuevo que viene de Meta Ads |
| **Lead stock** | Lead de la base historica que no convirtio |
| **Base stock** | Base de datos de ~120k leads historicos no convertidos |
| **Recalentamiento** | Proceso de recontactar leads stock para intentar convertirlos |
| **Score previo** | Scoring ANTES de hablar con el bot (usa data CRM) |
| **Score post** | Scoring DESPUES de hablar con el bot (usa data conversacion) |

---

## Lineas de Producto Maqui+

1. **Vehiculos** — Fondo colectivo para adquirir vehiculos
2. **Inmuebles** — Fondo colectivo para adquirir inmuebles

---

*Documento creado para mantener contexto del caso de negocio durante el desarrollo del bot comercial.*
