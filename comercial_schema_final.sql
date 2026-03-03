--
-- PostgreSQL database dump
--

\restrict YhOIw5vnCWymUkM2j2f4T2tcDoNsnFei96kEVGvDxM7dhzfcVJbdYMqDfEtZIuW

-- Dumped from database version 16.11
-- Dumped by pg_dump version 16.12 (Ubuntu 16.12-1.pgdg24.04+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: comercial; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA comercial;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: bd_asesores; Type: TABLE; Schema: comercial; Owner: -
--

CREATE TABLE comercial.bd_asesores (
    id_asesor uuid DEFAULT gen_random_uuid() NOT NULL,
    cod_asesor character varying(20),
    nombre_asesor character varying(100),
    antiguedad integer,
    especialidad character varying(50),
    edad integer,
    ventas_netas numeric(12,2),
    persistencia_promedio_propios numeric(5,2),
    persistencia_promedio_mqs numeric(5,2),
    ratio_conversion_de_venta numeric(5,4),
    leads_en_cola integer DEFAULT 0,
    disponibilidad character varying(20),
    fecha_creacion timestamp with time zone DEFAULT now() NOT NULL,
    fecha_actualizacion timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_disponibilidad CHECK ((((disponibilidad)::text = ANY ((ARRAY['disponible'::character varying, 'no disponible'::character varying])::text[])) OR (disponibilidad IS NULL)))
);


--
-- Name: bd_leads; Type: TABLE; Schema: comercial; Owner: -
--

CREATE TABLE comercial.bd_leads (
    id_lead uuid DEFAULT gen_random_uuid() NOT NULL,
    dni character varying(20),
    numero character varying(15),
    correo character varying(100),
    nombre character varying(100),
    apellido character varying(100),
    producto character varying(50),
    zona character varying(50),
    origen_lead character varying(50),
    suborigen_lead character varying(50),
    linea character varying(50),
    scoring numeric(5,4),
    cluster integer,
    contactabilidad numeric(3,2),
    tasa_de_conversacion numeric(5,4),
    ultimo_scoring numeric(5,4),
    probabilidad_de_conversion numeric(5,4),
    segmento_de_scoring character varying(20),
    ultimo_asesor_asignado uuid,
    estado_de_lead character varying(30) DEFAULT 'lead'::character varying,
    origen_de_venta character varying(50),
    motivo_de_descarte character varying(100),
    ultima_fecha_gestion_bot timestamp with time zone,
    ultima_fecha_gestion_convencional timestamp with time zone,
    cantidad_contactos_lead_bot integer DEFAULT 0,
    nivel_interes_actual numeric(3,2),
    sentimiento_actual character varying(20),
    nivel_intencion_de_compra numeric(3,2),
    tiempo_respuesta_promedio integer,
    ultimo_tiempo_de_contacto integer,
    tiempo_sin_gestion integer,
    id_ultima_campana character varying(50),
    ultimo_estado_asesor character varying(30),
    ultimo_estado_de_contacto_asesor character varying(30),
    antiguedad_lead integer,
    score_sentinel numeric(5,4),
    ratio_de_deuda numeric(5,4),
    tipo_persona character varying(20),
    situacion_laboral character varying(30),
    fecha_creacion timestamp with time zone DEFAULT now() NOT NULL,
    fecha_actualizacion timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_estado_lead CHECK (((estado_de_lead)::text = ANY ((ARRAY['lead'::character varying, 'prospecto'::character varying, 'venta'::character varying, 'descartado'::character varying])::text[]))),
    CONSTRAINT chk_sentimiento CHECK ((((sentimiento_actual)::text = ANY ((ARRAY['positivo'::character varying, 'neutral'::character varying, 'negativo'::character varying])::text[])) OR (sentimiento_actual IS NULL)))
);


--
-- Name: config_modelo; Type: TABLE; Schema: comercial; Owner: -
--

CREATE TABLE comercial.config_modelo (
    id integer NOT NULL,
    parametro character varying(50) NOT NULL,
    valor character varying(20) NOT NULL,
    tipo_dato character varying(10),
    categoria character varying(20),
    descripcion text,
    fecha_actualizacion timestamp with time zone DEFAULT now()
);


--
-- Name: config_modelo_id_seq; Type: SEQUENCE; Schema: comercial; Owner: -
--

CREATE SEQUENCE comercial.config_modelo_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: config_modelo_id_seq; Type: SEQUENCE OWNED BY; Schema: comercial; Owner: -
--

ALTER SEQUENCE comercial.config_modelo_id_seq OWNED BY comercial.config_modelo.id;


--
-- Name: hist_asignaciones; Type: TABLE; Schema: comercial; Owner: -
--

CREATE TABLE comercial.hist_asignaciones (
    id_asignacion uuid DEFAULT gen_random_uuid() NOT NULL,
    id_lead uuid NOT NULL,
    id_asesor uuid NOT NULL,
    id_matching uuid,
    score_total_al_asignar numeric(5,4),
    score_k_al_asignar numeric(5,4),
    score_c_al_asignar numeric(5,4),
    score_v_al_asignar numeric(5,4),
    score_p_al_asignar numeric(5,4),
    estado_gestion character varying(30) DEFAULT 'en_espera'::character varying,
    motivo_descarte character varying(100),
    fecha_asignacion timestamp with time zone DEFAULT now() NOT NULL,
    fecha_primer_contacto timestamp with time zone,
    tiempo_espera_horas numeric(6,2),
    fecha_cambio_estado timestamp with time zone,
    cerro_venta boolean DEFAULT false,
    monto_venta numeric(12,2),
    producto_vendido character varying(50),
    reasignado boolean DEFAULT false,
    id_asesor_anterior uuid,
    motivo_reasignacion character varying(100),
    reciclado_a_bot boolean DEFAULT false,
    CONSTRAINT chk_estado_gestion CHECK (((estado_gestion)::text = ANY ((ARRAY['en_espera'::character varying, 'prospecto'::character varying, 'descartado'::character varying, 'fuerza_ventas'::character varying])::text[])))
);


--
-- Name: hist_clientes; Type: TABLE; Schema: comercial; Owner: -
--

CREATE TABLE comercial.hist_clientes (
    id_cliente uuid DEFAULT gen_random_uuid() NOT NULL,
    id_lead uuid NOT NULL,
    id_asesor_cierre uuid NOT NULL,
    fecha_compra timestamp with time zone NOT NULL,
    producto_comprado character varying(50),
    monto_venta numeric(12,2),
    estado_cliente character varying(20) DEFAULT 'activo'::character varying,
    meses_activo integer DEFAULT 0,
    cantidad_pagos integer DEFAULT 0,
    pagos_atrasados integer DEFAULT 0,
    monto_total_pagado numeric(12,2) DEFAULT 0,
    riesgo_churn numeric(5,4),
    clv_real numeric(12,2),
    clv_predicho numeric(12,2),
    fecha_cancelacion timestamp with time zone,
    motivo_cancelacion character varying(100),
    fecha_actualizacion timestamp with time zone DEFAULT now(),
    CONSTRAINT chk_estado_cliente CHECK (((estado_cliente)::text = ANY ((ARRAY['activo'::character varying, 'inactivo'::character varying, 'cancelado'::character varying])::text[])))
);


--
-- Name: hist_conversaciones; Type: TABLE; Schema: comercial; Owner: -
--

CREATE TABLE comercial.hist_conversaciones (
    id_mensaje uuid DEFAULT gen_random_uuid() NOT NULL,
    id_lead uuid NOT NULL,
    id_campana character varying(50),
    direccion character varying(10),
    contenido text,
    tipo_mensaje character varying(30),
    canal character varying(20),
    entregado boolean,
    leido boolean,
    respondio boolean,
    tiempo_respuesta_seg integer,
    sentimiento character varying(20),
    intencion character varying(30),
    keywords jsonb,
    score_antes numeric(5,4),
    score_despues numeric(5,4),
    delta_score numeric(5,4),
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_direccion CHECK (((direccion)::text = ANY ((ARRAY['outbound'::character varying, 'inbound'::character varying])::text[])))
);


--
-- Name: hist_scoring; Type: TABLE; Schema: comercial; Owner: -
--

CREATE TABLE comercial.hist_scoring (
    id_hist uuid DEFAULT gen_random_uuid() NOT NULL,
    id_lead uuid NOT NULL,
    scoring_anterior numeric(5,4),
    scoring_nuevo numeric(5,4),
    delta_scoring numeric(5,4),
    evento_trigger character varying(50),
    nivel_interes numeric(3,2),
    sentimiento character varying(20),
    contactabilidad numeric(3,2),
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_evento CHECK (((evento_trigger)::text = ANY ((ARRAY['respuesta_bot'::character varying, 'decaimiento'::character varying, 'nlp_update'::character varying, 'campana'::character varying, 'manual'::character varying, 'reciclaje'::character varying])::text[])))
);


--
-- Name: matching; Type: TABLE; Schema: comercial; Owner: -
--

CREATE TABLE comercial.matching (
    id_matching uuid DEFAULT gen_random_uuid() NOT NULL,
    id_lead uuid NOT NULL,
    id_asesor uuid NOT NULL,
    score_k numeric(5,4),
    score_c numeric(5,4),
    score_v numeric(5,4),
    score_p numeric(5,4),
    score_total numeric(5,4),
    peso_w1 numeric(3,2),
    peso_w2 numeric(3,2),
    peso_w3 numeric(3,2),
    peso_w4 numeric(3,2),
    asignado boolean DEFAULT false,
    fecha_evaluacion timestamp with time zone DEFAULT now() NOT NULL,
    fecha_asignacion timestamp with time zone
);


--
-- Name: config_modelo id; Type: DEFAULT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.config_modelo ALTER COLUMN id SET DEFAULT nextval('comercial.config_modelo_id_seq'::regclass);


--
-- Name: bd_asesores bd_asesores_cod_asesor_key; Type: CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.bd_asesores
    ADD CONSTRAINT bd_asesores_cod_asesor_key UNIQUE (cod_asesor);


--
-- Name: bd_asesores bd_asesores_pkey; Type: CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.bd_asesores
    ADD CONSTRAINT bd_asesores_pkey PRIMARY KEY (id_asesor);


--
-- Name: bd_leads bd_leads_pkey; Type: CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.bd_leads
    ADD CONSTRAINT bd_leads_pkey PRIMARY KEY (id_lead);


--
-- Name: config_modelo config_modelo_parametro_key; Type: CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.config_modelo
    ADD CONSTRAINT config_modelo_parametro_key UNIQUE (parametro);


--
-- Name: config_modelo config_modelo_pkey; Type: CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.config_modelo
    ADD CONSTRAINT config_modelo_pkey PRIMARY KEY (id);


--
-- Name: hist_asignaciones hist_asignaciones_pkey; Type: CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.hist_asignaciones
    ADD CONSTRAINT hist_asignaciones_pkey PRIMARY KEY (id_asignacion);


--
-- Name: hist_clientes hist_clientes_id_lead_key; Type: CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.hist_clientes
    ADD CONSTRAINT hist_clientes_id_lead_key UNIQUE (id_lead);


--
-- Name: hist_clientes hist_clientes_pkey; Type: CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.hist_clientes
    ADD CONSTRAINT hist_clientes_pkey PRIMARY KEY (id_cliente);


--
-- Name: hist_conversaciones hist_conversaciones_pkey; Type: CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.hist_conversaciones
    ADD CONSTRAINT hist_conversaciones_pkey PRIMARY KEY (id_mensaje);


--
-- Name: hist_scoring hist_scoring_pkey; Type: CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.hist_scoring
    ADD CONSTRAINT hist_scoring_pkey PRIMARY KEY (id_hist);


--
-- Name: matching matching_pkey; Type: CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.matching
    ADD CONSTRAINT matching_pkey PRIMARY KEY (id_matching);


--
-- Name: matching uq_matching_lead_asesor_fecha; Type: CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.matching
    ADD CONSTRAINT uq_matching_lead_asesor_fecha UNIQUE (id_lead, id_asesor, fecha_evaluacion);


--
-- Name: idx_asesores_cod; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_asesores_cod ON comercial.bd_asesores USING btree (cod_asesor);


--
-- Name: idx_asesores_disponibilidad; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_asesores_disponibilidad ON comercial.bd_asesores USING btree (disponibilidad);


--
-- Name: idx_asig_asesor; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_asig_asesor ON comercial.hist_asignaciones USING btree (id_asesor);


--
-- Name: idx_asig_cerro; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_asig_cerro ON comercial.hist_asignaciones USING btree (cerro_venta) WHERE (cerro_venta = true);


--
-- Name: idx_asig_estado; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_asig_estado ON comercial.hist_asignaciones USING btree (estado_gestion);


--
-- Name: idx_asig_fecha; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_asig_fecha ON comercial.hist_asignaciones USING btree (fecha_asignacion DESC);


--
-- Name: idx_asig_lead; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_asig_lead ON comercial.hist_asignaciones USING btree (id_lead);


--
-- Name: idx_clientes_asesor; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_clientes_asesor ON comercial.hist_clientes USING btree (id_asesor_cierre);


--
-- Name: idx_clientes_estado; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_clientes_estado ON comercial.hist_clientes USING btree (estado_cliente);


--
-- Name: idx_clientes_fecha; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_clientes_fecha ON comercial.hist_clientes USING btree (fecha_compra DESC);


--
-- Name: idx_conv_direccion; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_conv_direccion ON comercial.hist_conversaciones USING btree (direccion);


--
-- Name: idx_conv_intencion; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_conv_intencion ON comercial.hist_conversaciones USING btree (intencion);


--
-- Name: idx_conv_lead; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_conv_lead ON comercial.hist_conversaciones USING btree (id_lead);


--
-- Name: idx_conv_timestamp; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_conv_timestamp ON comercial.hist_conversaciones USING btree ("timestamp" DESC);


--
-- Name: idx_leads_asesor; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_leads_asesor ON comercial.bd_leads USING btree (ultimo_asesor_asignado);


--
-- Name: idx_leads_dni; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_leads_dni ON comercial.bd_leads USING btree (dni);


--
-- Name: idx_leads_estado; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_leads_estado ON comercial.bd_leads USING btree (estado_de_lead);


--
-- Name: idx_leads_numero; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_leads_numero ON comercial.bd_leads USING btree (numero);


--
-- Name: idx_leads_origen; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_leads_origen ON comercial.bd_leads USING btree (origen_lead);


--
-- Name: idx_leads_producto; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_leads_producto ON comercial.bd_leads USING btree (producto);


--
-- Name: idx_leads_suborigen; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_leads_suborigen ON comercial.bd_leads USING btree (suborigen_lead);


--
-- Name: idx_matching_asignado; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_matching_asignado ON comercial.matching USING btree (asignado) WHERE (asignado = true);


--
-- Name: idx_matching_fecha; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_matching_fecha ON comercial.matching USING btree (fecha_evaluacion DESC);


--
-- Name: idx_matching_lead; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_matching_lead ON comercial.matching USING btree (id_lead);


--
-- Name: idx_scoring_evento; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_scoring_evento ON comercial.hist_scoring USING btree (evento_trigger);


--
-- Name: idx_scoring_lead; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_scoring_lead ON comercial.hist_scoring USING btree (id_lead);


--
-- Name: idx_scoring_timestamp; Type: INDEX; Schema: comercial; Owner: -
--

CREATE INDEX idx_scoring_timestamp ON comercial.hist_scoring USING btree ("timestamp" DESC);


--
-- Name: bd_leads fk_leads_asesor; Type: FK CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.bd_leads
    ADD CONSTRAINT fk_leads_asesor FOREIGN KEY (ultimo_asesor_asignado) REFERENCES comercial.bd_asesores(id_asesor) ON DELETE SET NULL;


--
-- Name: hist_asignaciones hist_asignaciones_id_asesor_anterior_fkey; Type: FK CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.hist_asignaciones
    ADD CONSTRAINT hist_asignaciones_id_asesor_anterior_fkey FOREIGN KEY (id_asesor_anterior) REFERENCES comercial.bd_asesores(id_asesor);


--
-- Name: hist_asignaciones hist_asignaciones_id_asesor_fkey; Type: FK CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.hist_asignaciones
    ADD CONSTRAINT hist_asignaciones_id_asesor_fkey FOREIGN KEY (id_asesor) REFERENCES comercial.bd_asesores(id_asesor);


--
-- Name: hist_asignaciones hist_asignaciones_id_lead_fkey; Type: FK CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.hist_asignaciones
    ADD CONSTRAINT hist_asignaciones_id_lead_fkey FOREIGN KEY (id_lead) REFERENCES comercial.bd_leads(id_lead);


--
-- Name: hist_asignaciones hist_asignaciones_id_matching_fkey; Type: FK CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.hist_asignaciones
    ADD CONSTRAINT hist_asignaciones_id_matching_fkey FOREIGN KEY (id_matching) REFERENCES comercial.matching(id_matching);


--
-- Name: hist_clientes hist_clientes_id_asesor_cierre_fkey; Type: FK CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.hist_clientes
    ADD CONSTRAINT hist_clientes_id_asesor_cierre_fkey FOREIGN KEY (id_asesor_cierre) REFERENCES comercial.bd_asesores(id_asesor);


--
-- Name: hist_clientes hist_clientes_id_lead_fkey; Type: FK CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.hist_clientes
    ADD CONSTRAINT hist_clientes_id_lead_fkey FOREIGN KEY (id_lead) REFERENCES comercial.bd_leads(id_lead);


--
-- Name: hist_conversaciones hist_conversaciones_id_lead_fkey; Type: FK CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.hist_conversaciones
    ADD CONSTRAINT hist_conversaciones_id_lead_fkey FOREIGN KEY (id_lead) REFERENCES comercial.bd_leads(id_lead);


--
-- Name: hist_scoring hist_scoring_id_lead_fkey; Type: FK CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.hist_scoring
    ADD CONSTRAINT hist_scoring_id_lead_fkey FOREIGN KEY (id_lead) REFERENCES comercial.bd_leads(id_lead);


--
-- Name: matching matching_id_asesor_fkey; Type: FK CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.matching
    ADD CONSTRAINT matching_id_asesor_fkey FOREIGN KEY (id_asesor) REFERENCES comercial.bd_asesores(id_asesor);


--
-- Name: matching matching_id_lead_fkey; Type: FK CONSTRAINT; Schema: comercial; Owner: -
--

ALTER TABLE ONLY comercial.matching
    ADD CONSTRAINT matching_id_lead_fkey FOREIGN KEY (id_lead) REFERENCES comercial.bd_leads(id_lead);


--
-- PostgreSQL database dump complete
--

\unrestrict YhOIw5vnCWymUkM2j2f4T2tcDoNsnFei96kEVGvDxM7dhzfcVJbdYMqDfEtZIuW

