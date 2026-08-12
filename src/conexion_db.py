import streamlit as st
import pandas as pd
import numpy as np
import pyodbc
from sqlalchemy import create_engine, text

# Conexión centralizada usando los secretos seguros
CADENA_CONEXION_PG = st.secrets["postgres"]["url"]

conn = st.connection("postgresql", type="sql", url=CADENA_CONEXION_PG)

def ejecutar_sincronizacion_desde_sap():
    """Extrae datos de SAP HANA y los almacena en PostgreSQL"""
    try:
        sap_conf = st.secrets["sap"]
        conn_str_sap = f"DRIVER={sap_conf['driver']};SERVERNODE={sap_conf['server']};UID={sap_conf['user']};PWD={sap_conf['password']}"
        conexion_sap = pyodbc.connect(conn_str_sap)
        
        # --- 1. QUERIES DE EXTRACCIÓN SAP ---
        query_remisiones = """
            SELECT T0."DocNum" as "Documento", T0."CardName" as "Cliente", T0."DocDate" as "Fecha_Contabilizacion", 
            T0."DocStatus" as "Status_Documento", T0."SlpCode" as "Empleado_Ventas", T0."OwnerCode" as "Propietario_Doc",
            T2."WhsName" as "Sede_Codigo",
            T1."ItemCode" as "Numero_Articulo", T1."Dscription" as "Descripcion", 
            T1."Quantity" as "Cantidad", T1."Price" as "Precio", T1."LineTotal" AS "Precio_Sin_IVA", 
            T1."LineTotal" + T1."VatSum" AS "Precio_Total", T2."WhsName" as "Almacen", T3."U_NAME" as "Nombre_Usuario",
            T5."ItmsGrpNam" as "Linea", T6."FirmName" AS "Marca",
            COALESCE(NULLIF(TRIM(T8."firstName" || ' ' || T8."lastName"), ''), T3."U_NAME") as "Colaborador"
            FROM "NE042025"."ODLN" T0 
            INNER JOIN "NE042025"."DLN1" T1 ON T0."DocEntry" = T1."DocEntry" 
            INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
            INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID"
            INNER JOIN "NE042025"."OITM" T4 ON T1."ItemCode" = T4."ItemCode"
            INNER JOIN "NE042025"."OITB" T5 ON T4."ItmsGrpCod" = T5."ItmsGrpCod"
            INNER JOIN "NE042025"."OMRC" T6 ON T4."FirmCode" = T6."FirmCode"
            LEFT JOIN "NE042025"."OUSR" T7 ON T0."OwnerCode" = T7."USERID"
            LEFT JOIN "NE042025"."OHEM" T8 ON T7."INTERNAL_K" = T8."empID"
            WHERE T0."DocStatus" = 'O' AND T2."WhsName" <> 'BODEGA INGENIERIA'
        """
        query_colaboradores = """
            SELECT T0."lastName" as "Nombre", T0."firstName" as "Apellido", T0."Code" as "Codigo" FROM "NE042025"."OHEM" T0
        """
        query_cotizaciones = """
            SELECT 
                T0."DocNum" as "Documento", 
                T0."CardCode" as "Numero_Cliente",
                T0."CardName" as "Cliente", 
                T0."DocDate" as "Fecha_Contabilizacion", 
                T0."DocStatus" as "Status_Documento", 
                T0."SlpCode" as "Empleado_Ventas", 
                T0."OwnerCode" as "Propietario_Doc",
                T2."WhsName" as "Sede_Codigo", 
                T1."ItemCode" as "Numero_Articulo", 
                T1."Dscription" as "Descripcion", 
                T1."Quantity" as "Cantidad", 
                T1."Price" as "Precio", 
                T1."LineTotal" AS "Precio_Sin_IVA", 
                T1."LineTotal" + T1."VatSum" AS "Precio_Total",
                T1."GrssProfit" AS "Rentabilidad",
                T2."WhsName" as "Almacen", 
                T3."U_NAME" as "Nombre_Usuario",
                T6."CreditLine" AS "Cupo_Credito",
                COALESCE(NULLIF(TRIM(T4."firstName" || ' ' || T4."lastName"), ''), T3."U_NAME") as "Colaborador"
            FROM "NE042025"."OQUT" T0 
            INNER JOIN "NE042025"."QUT1" T1 ON T0."DocEntry" = T1."DocEntry" 
            INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
            INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID" 
            LEFT JOIN "NE042025"."OUSR" T5 ON T0."OwnerCode" = T5."USERID"
            LEFT JOIN "NE042025"."OHEM" T4 ON T5."INTERNAL_K" = T4."empID"
            INNER JOIN "NE042025"."OCRD" T6 ON T0."CardCode" = T6."CardCode"
            WHERE T0."DocStatus" = 'O' AND T2."WhsName" <> 'BODEGA INGENIERIA'
        """
        # ✅ CORREGIDO: Agregado T0."CardCode" as "Numero_Cliente"
        query_ordenes = """
            SELECT T0."DocNum" as "Documento", T0."CardCode" as "Numero_Cliente", T0."CardName" as "Cliente", T0."DocDate" as "Fecha_Contabilizacion", 
            T0."DocStatus" as "Status_Documento", T0."SlpCode" as "Empleado_Ventas", T0."OwnerCode" as "Propietario_Doc",
            T2."WhsName" as "Sede_Codigo", T1."ItemCode" as "Numero_Articulo", T1."Dscription" as "Descripcion", 
            T1."Quantity" as "Cantidad", T1."Price" as "Precio", T1."LineTotal" AS "Precio_Sin_IVA", 
            T1."LineTotal" + T1."VatSum" AS "Precio_Total", T2."WhsName" as "Almacen", T3."U_NAME" as "Nombre_Usuario",
            COALESCE(NULLIF(TRIM(T4."firstName" || ' ' || T4."lastName"), ''), T3."U_NAME") as "Colaborador"
            FROM "NE042025"."ORDR" T0 INNER JOIN "NE042025"."RDR1" T1 ON T0."DocEntry" = T1."DocEntry" 
            INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
            INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID" 
            LEFT JOIN "NE042025"."OUSR" T5 ON T0."OwnerCode" = T5."USERID"
            LEFT JOIN "NE042025"."OHEM" T4 ON T5."INTERNAL_K" = T4."empID"
            WHERE T0."DocStatus" = 'O' AND T2."WhsName" <> 'BODEGA INGENIERIA'
        """
        query_solicitudes = """
            SELECT 
            T0."DocNum" as "Documento", T0."Comments" as "Observaciones", T0."DocDate" as "Fecha_Contabilizacion", 
            T0."DocStatus" as "Status_Documento", T0."SlpCode" as "Empleado_Ventas", T0."OwnerCode" as "Propietario_Doc",
            T2."WhsName" as "Sede_Codigo", 
            T1."ItemCode" as "Numero_Articulo", T1."Dscription" as "Descripcion", 
            T1."Quantity" as "Cantidad", T1."Price" as "Precio", 
            T1."LineTotal" AS "Precio_Sin_IVA", T1."LineTotal" + T1."VatSum" AS "Precio_Total",
            T2."WhsName" as "Almacen", T3."U_NAME" as "Nombre_Usuario",
            COALESCE(NULLIF(TRIM(T4."firstName" || ' ' || T4."lastName"), ''), T3."U_NAME") as "Colaborador"
            FROM "NE042025"."OPRQ" T0 
            INNER JOIN "NE042025"."PRQ1" T1 ON T0."DocEntry" = T1."DocEntry" 
            INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
            INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID" 
            LEFT JOIN "NE042025"."OUSR" T5 ON T0."OwnerCode" = T5."USERID"
            LEFT JOIN "NE042025"."OHEM" T4 ON T5."INTERNAL_K" = T4."empID"
            WHERE T0."DocStatus" = 'O' AND T2."WhsName" <> 'BODEGA INGENIERIA'
        """
        query_traslados = """
            SELECT 
                T0."DocNum" as "Documento", T0."Comments" as "Observaciones", 
                T6."WhsName" as "Almacen_Origen",
                T0."DocDate" as "Fecha_Contabilizacion", 
                T0."DocStatus" as "Status_Documento", 
                T0."OwnerCode" as "Propietario_Doc",
                T2."WhsName" as "Sede_Codigo",   -- correccion de sede
                T1."ItemCode" as "Numero_Articulo", T1."Dscription" as "Descripcion", 
                T1."Quantity" as "Cantidad", T1."Price" as "Precio", 
                T1."LineTotal" AS "Precio_Sin_IVA", T1."LineTotal" + T1."VatSum" AS "Precio_Total",
                T2."WhsName" as "Almacen", T3."U_NAME" as "Nombre_Usuario",
                COALESCE(NULLIF(TRIM(T4."firstName" || ' ' || T4."lastName"), ''), T3."U_NAME") as "Colaborador"
            FROM "NE042025"."OWTQ" T0 
            INNER JOIN "NE042025"."WTQ1" T1 ON T0."DocEntry" = T1."DocEntry" 
            INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
            INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID" 
            LEFT JOIN "NE042025"."OUSR" T5 ON T0."OwnerCode" = T5."USERID"
            LEFT JOIN "NE042025"."OHEM" T4 ON T5."INTERNAL_K" = T4."empID"
            LEFT JOIN "NE042025"."OWHS" T6 ON T0."Filler" = T6."WhsCode"
            WHERE T0."DocStatus" = 'O' AND T2."WhsName" <> 'BODEGA INGENIERIA'
        """
        # ✅ CORREGIDO: Agregado T0."CardCode" as "Numero_Cliente"
        query_facturas = """
            SELECT T0."DocNum" as "Documento", T0."CardCode" as "Numero_Cliente", T0."CardName" as "Cliente", T0."DocDate" as "Fecha_Contabilizacion", 
            T0."DocStatus" as "Status_Documento", T0."SlpCode" as "Empleado_Ventas", T0."OwnerCode" as "Propietario_Doc",
            T2."WhsName" as "Sede_Codigo", T1."ItemCode" as "Numero_Articulo", T1."Dscription" as "Descripcion", 
            T1."Quantity" as "Cantidad", T1."Price" as "Precio", T1."LineTotal" AS "Precio_Sin_IVA", 
            T1."LineTotal" + T1."VatSum" AS "Precio_Total", T2."WhsName" as "Almacen", T3."U_NAME" as "Nombre_Usuario",
            COALESCE(NULLIF(TRIM(T4."firstName" || ' ' || T4."lastName"), ''), T3."U_NAME") as "Colaborador"
            FROM "NE042025"."OINV" T0 
            INNER JOIN "NE042025"."INV1" T1 ON T0."DocEntry" = T1."DocEntry" 
            INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
            INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID" 
            LEFT JOIN "NE042025"."OUSR" T5 ON T0."OwnerCode" = T5."USERID"
            LEFT JOIN "NE042025"."OHEM" T4 ON T5."INTERNAL_K" = T4."empID"
            WHERE T0."DocStatus" = 'O' AND T0."isIns" = 'Y' AND T2."WhsName" <> 'BODEGA INGENIERIA'
        """
        # ✅ CORREGIDO: Agregado T0."CardCode" as "Numero_Cliente"
        query_notas = """
            SELECT T0."DocNum" as "Documento", T0."CardCode" as "Numero_Cliente", T0."CardName" as "Cliente", T0."DocDate" as "Fecha_Contabilizacion", 
            T0."DocStatus" as "Status_Documento", T0."SlpCode" as "Empleado_Ventas", T0."OwnerCode" as "Propietario_Doc",
            T2."WhsName" as "Sede_Codigo", T1."ItemCode" as "Numero_Articulo", T1."Dscription" as "Descripcion", 
            T1."Quantity" as "Cantidad", T1."Price" as "Precio", T1."LineTotal" AS "Precio_Sin_IVA", 
            T1."LineTotal" + T1."VatSum" AS "Precio_Total", T2."WhsName" as "Almacen", T3."U_NAME" as "Nombre_Usuario",
            COALESCE(NULLIF(TRIM(T4."firstName" || ' ' || T4."lastName"), ''), T3."U_NAME") as "Colaborador"
            FROM "NE042025"."ORIN" T0 
            INNER JOIN "NE042025"."RIN1" T1 ON T0."DocEntry" = T1."DocEntry" 
            INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
            INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID" 
            LEFT JOIN "NE042025"."OUSR" T5 ON T0."OwnerCode" = T5."USERID"
            LEFT JOIN "NE042025"."OHEM" T4 ON T5."INTERNAL_K" = T4."empID"
            WHERE T0."DocStatus" = 'O' AND T2."WhsName" <> 'BODEGA INGENIERIA'
        """
        
        df_remisiones = pd.read_sql(query_remisiones, conexion_sap)
        df_colaboradores = pd.read_sql(query_colaboradores, conexion_sap)
        df_cotizaciones = pd.read_sql(query_cotizaciones, conexion_sap)
        df_ordenes = pd.read_sql(query_ordenes, conexion_sap)
        df_solicitudes = pd.read_sql(query_solicitudes, conexion_sap)
        df_traslados = pd.read_sql(query_traslados, conexion_sap)
        df_facturas = pd.read_sql(query_facturas, conexion_sap)
        df_notas = pd.read_sql(query_notas, conexion_sap)

        conexion_sap.close() 
        
        # --- 2. CONEXIÓN A POSTGRES ---
        pg_engine = create_engine(CADENA_CONEXION_PG)
        
        sql_vista_rem = """CREATE OR REPLACE VIEW sap_raw.vw_remisiones_con_rangos AS SELECT r.*, sap_raw.calcular_rango_dias(r."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.remisiones r;"""
        sql_vista_cot = """CREATE OR REPLACE VIEW sap_raw.vw_cotizaciones_con_rangos AS SELECT c.*, sap_raw.calcular_rango_dias(c."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.cotizaciones c;"""
        sql_vista_ord = """CREATE OR REPLACE VIEW sap_raw.vw_ordenes_venta_con_rangos AS SELECT o.*, sap_raw.calcular_rango_dias(o."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.ordenes_venta o;"""
        sql_vista_sol = """CREATE OR REPLACE VIEW sap_raw.vw_solicitudes_compras_con_rangos AS SELECT s.*, sap_raw.calcular_rango_dias(s."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.solicitudes_compras s;"""
        sql_vista_tra = """CREATE OR REPLACE VIEW sap_raw.vw_solicitudes_traslados_con_rangos AS SELECT s.*, sap_raw.calcular_rango_dias(s."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.solicitudes_traslados s;"""
        sql_vista_fac = """CREATE OR REPLACE VIEW sap_raw.vw_facturas_reserva_con_rangos AS SELECT f.*, sap_raw.calcular_rango_dias(f."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.facturas_reserva f;"""
        sql_vista_nc = """CREATE OR REPLACE VIEW sap_raw.vw_notas_credito_con_rangos AS SELECT n.*, sap_raw.calcular_rango_dias(n."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.notas_credito n;"""

        with pg_engine.begin() as pg_conn:
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_remisiones_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_cotizaciones_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_ordenes_venta_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_solicitudes_compras_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_solicitudes_traslados_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_facturas_reserva_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_notas_credito_con_rangos CASCADE;"))
        
        df_remisiones.to_sql('remisiones', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_colaboradores.to_sql('colaboradores', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_cotizaciones.to_sql('cotizaciones', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_ordenes.to_sql('ordenes_venta', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_solicitudes.to_sql('solicitudes_compras', pg_engine, schema='sap_raw', if_exists='replace', index=False) 
        df_traslados.to_sql('solicitudes_traslados', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_facturas.to_sql('facturas_reserva', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_notas.to_sql('notas_credito', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        
        with pg_engine.begin() as pg_conn:
            pg_conn.execute(text(sql_vista_rem))
            pg_conn.execute(text(sql_vista_cot))
            pg_conn.execute(text(sql_vista_ord))
            pg_conn.execute(text(sql_vista_sol))
            pg_conn.execute(text(sql_vista_tra))
            pg_conn.execute(text(sql_vista_fac))
            pg_conn.execute(text(sql_vista_nc))
            
        return True
    except Exception as e:
        st.error(f"❌ Error en sincronización: {e}")
        return False

@st.cache_data(ttl=600)
def cargar_y_transformar_remisiones():
    try:
        df = conn.query("SELECT * FROM sap_raw.vw_remisiones_con_rangos")
        columnas_esperadas = ['Documento', 'Cliente', 'Fecha_Contabilizacion', 'Status_Documento', 'Empleado_Ventas', 'Propietario_Doc', 'Sede_Codigo', 'Numero_Articulo', 'Descripcion', 'Cantidad', 'Precio', 'Precio_Sin_IVA', 'Precio_Total', 'Almacen', 'Nombre_Usuario', 'Linea', 'Marca', 'Colaborador', 'Rango_Dias']
        mapeo = {c.lower().strip(): c for c in columnas_esperadas}
        df.columns = [mapeo.get(c.lower().strip(), c) for c in df.columns]
        df['Fecha_Contabilizacion'] = pd.to_datetime(df['Fecha_Contabilizacion'], errors='coerce')
        df['Dias'] = (pd.Timestamp.now().normalize() - df['Fecha_Contabilizacion']).dt.days
        df['Fecha_Texto'] = df['Fecha_Contabilizacion'].dt.strftime('%d/%m/%Y')
        if 'Colaborador' not in df.columns: df['Colaborador'] = "Sin Asignar"
        else: df['Colaborador'] = df['Colaborador'].fillna('Sin Asignar').replace('', 'Sin Asignar')
        return df
    except Exception as e:
        st.error(f"Error analizando datos en Postgres: {e}")
        return pd.DataFrame()

def aplicar_seguridad_rls(df):
    import streamlit as st
    if df is None or df.empty: return df
    rol = st.session_state.get('rol_actual', 'comercial')
    df_filtrado = df.copy()
    if 'Propietario_Doc' in df_filtrado.columns: df_filtrado['Propietario_Doc'] = df_filtrado['Propietario_Doc'].astype(str).str.strip()
    if 'Empleado_Ventas' in df_filtrado.columns: df_filtrado['Empleado_Ventas'] = df_filtrado['Empleado_Ventas'].astype(str).str.strip()
    if 'Sede_Codigo' in df_filtrado.columns: df_filtrado['Sede_Codigo'] = df_filtrado['Sede_Codigo'].astype(str).str.strip()
    if rol in ["admin", "gerente", "gerente_comercial"]: return df_filtrado
    elif rol == "admin_punto":
        sap_branch = st.session_state.get('sap_branch_code')
        # Validamos que no sea None, ni vacío, ni la cadena de texto "None"
        if sap_branch and str(sap_branch).strip().lower() not in ['none', '']:
            sap_branch = str(sap_branch).strip().upper()
            if 'Sede_Codigo' in df_filtrado.columns:
                # Comparamos en mayúsculas para evitar errores de tipeo
                return df_filtrado[df_filtrado['Sede_Codigo'].str.upper() == sap_branch]
        # Si no tiene un branch code válido, no mostramos nada (seguridad por defecto)
        return df_filtrado.iloc[0:0]
    elif rol == "comercial":
        sap_owner = str(st.session_state.get('sap_owner_code', '')).strip()
        sap_slp = str(st.session_state.get('sap_slp_code', '')).strip()
        condicion_owner = df_filtrado['Propietario_Doc'] == sap_owner if sap_owner else False
        condicion_slp = df_filtrado['Empleado_Ventas'] == sap_slp if sap_slp else False
        return df_filtrado[condicion_owner | condicion_slp]
    return df_filtrado

@st.cache_data(ttl=600)
def cargar_y_transformar_cotizaciones():
    try:
        df = conn.query("SELECT * FROM sap_raw.vw_cotizaciones_con_rangos")
        columnas_esperadas = ['Documento', 'Numero_Cliente', 'Cliente', 'Fecha_Contabilizacion', 'Status_Documento', 'Empleado_Ventas', 'Propietario_Doc', 'Sede_Codigo', 'Numero_Articulo', 'Descripcion', 'Cantidad', 'Precio', 'Precio_Sin_IVA', 'Precio_Total', 'Rentabilidad', 'Almacen', 'Nombre_Usuario', 'Cupo_Credito', 'Colaborador', 'Rango_Dias']
        mapeo = {c.lower().strip(): c for c in columnas_esperadas}
        df.columns = [mapeo.get(c.lower().strip(), c) for c in df.columns]
        df['Fecha_Contabilizacion'] = pd.to_datetime(df['Fecha_Contabilizacion'], errors='coerce')
        df['Dias'] = (pd.Timestamp.now().normalize() - df['Fecha_Contabilizacion']).dt.days
        df['Fecha_Texto'] = df['Fecha_Contabilizacion'].dt.strftime('%d/%m/%Y')
        return df
    except Exception as e:
        st.error(f"Error analizando cotizaciones en Postgres: {e}")
        return pd.DataFrame()

# ✅ CORREGIDO: Agregado 'Numero_Cliente' a columnas_esperadas
@st.cache_data(ttl=600)
def cargar_y_transformar_ordenes():
    try:
        df = conn.query("SELECT * FROM sap_raw.vw_ordenes_venta_con_rangos")
        columnas_esperadas = ['Documento', 'Numero_Cliente', 'Cliente', 'Fecha_Contabilizacion', 'Status_Documento', 'Empleado_Ventas', 'Propietario_Doc', 'Sede_Codigo', 'Numero_Articulo', 'Descripcion', 'Cantidad', 'Precio', 'Precio_Sin_IVA', 'Precio_Total', 'Almacen', 'Nombre_Usuario', 'Colaborador', 'Rango_Dias']
        mapeo = {c.lower().strip(): c for c in columnas_esperadas}
        df.columns = [mapeo.get(c.lower().strip(), c) for c in df.columns]
        df['Fecha_Contabilizacion'] = pd.to_datetime(df['Fecha_Contabilizacion'], errors='coerce')
        df['Dias'] = (pd.Timestamp.now().normalize() - df['Fecha_Contabilizacion']).dt.days
        df['Fecha_Texto'] = df['Fecha_Contabilizacion'].dt.strftime('%d/%m/%Y')
        return df
    except Exception as e:
        st.error(f"Error analizando órdenes de venta en Postgres: {e}")
        return pd.DataFrame()
    
@st.cache_data(ttl=600)
def cargar_y_transformar_solicitudes():
    try:
        df = conn.query("SELECT * FROM sap_raw.vw_solicitudes_compras_con_rangos")
        columnas_esperadas = ['Documento', 'Observaciones', 'Fecha_Contabilizacion', 'Status_Documento', 'Empleado_Ventas', 'Propietario_Doc', 'Sede_Codigo', 'Numero_Articulo', 'Descripcion', 'Cantidad', 'Precio', 'Precio_Sin_IVA', 'Precio_Total', 'Almacen', 'Nombre_Usuario', 'Colaborador', 'Rango_Dias']
        mapeo = {c.lower().strip(): c for c in columnas_esperadas}
        df.columns = [mapeo.get(c.lower().strip(), c) for c in df.columns]
        df['Fecha_Contabilizacion'] = pd.to_datetime(df['Fecha_Contabilizacion'], errors='coerce')
        df['Dias'] = (pd.Timestamp.now().normalize() - df['Fecha_Contabilizacion']).dt.days
        df['Fecha_Texto'] = df['Fecha_Contabilizacion'].dt.strftime('%d/%m/%Y')
        return df
    except Exception as e:
        st.error(f"Error analizando solicitudes en Postgres: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=600)
def cargar_y_transformar_traslados():
    try:
        df = conn.query("SELECT * FROM sap_raw.vw_solicitudes_traslados_con_rangos")
        columnas_esperadas = [
            'Documento', 'Observaciones', 'Almacen_Origen', 'Fecha_Contabilizacion', 'Status_Documento', 
            'Propietario_Doc', 'Numero_Articulo', 'Descripcion', 'Cantidad', 'Precio', 
            'Precio_Sin_IVA', 'Precio_Total', 'Almacen', 'Nombre_Usuario', 'Colaborador', 'Rango_Dias'
        ]
        mapeo = {c.lower().strip(): c for c in columnas_esperadas}
        df.columns = [mapeo.get(c.lower().strip(), c) for c in df.columns]
        df['Fecha_Contabilizacion'] = pd.to_datetime(df['Fecha_Contabilizacion'], errors='coerce')
        df['Dias'] = (pd.Timestamp.now().normalize() - df['Fecha_Contabilizacion']).dt.days
        df['Fecha_Texto'] = df['Fecha_Contabilizacion'].dt.strftime('%d/%m/%Y')
        return df
    except Exception as e:
        st.error(f"Error analizando traslados en Postgres: {e}")
        return pd.DataFrame()

# ✅ CORREGIDO: Agregado 'Numero_Cliente' a columnas_esperadas
@st.cache_data(ttl=600)
def cargar_y_transformar_facturas():
    try:
        df = conn.query("SELECT * FROM sap_raw.vw_facturas_reserva_con_rangos")
        columnas_esperadas = [
            'Documento', 'Numero_Cliente', 'Cliente', 'Fecha_Contabilizacion', 'Status_Documento', 
            'Empleado_Ventas', 'Propietario_Doc', 'Sede_Codigo', 'Numero_Articulo', 
            'Descripcion', 'Cantidad', 'Precio', 'Precio_Sin_IVA', 'Precio_Total', 
            'Almacen', 'Nombre_Usuario', 'Colaborador', 'Rango_Dias'
        ]
        mapeo = {c.lower().strip(): c for c in columnas_esperadas}
        df.columns = [mapeo.get(c.lower().strip(), c) for c in df.columns]
        df['Fecha_Contabilizacion'] = pd.to_datetime(df['Fecha_Contabilizacion'], errors='coerce')
        df['Dias'] = (pd.Timestamp.now().normalize() - df['Fecha_Contabilizacion']).dt.days
        df['Fecha_Texto'] = df['Fecha_Contabilizacion'].dt.strftime('%d/%m/%Y')
        return df
    except Exception as e:
        st.error(f"Error analizando facturas reserva en Postgres: {e}")
        return pd.DataFrame()

# ✅ CORREGIDO: Agregado 'Numero_Cliente' a columnas_esperadas
@st.cache_data(ttl=600)
def cargar_y_transformar_notas():
    try:
        df = conn.query("SELECT * FROM sap_raw.vw_notas_credito_con_rangos")
        columnas_esperadas = [
            'Documento', 'Numero_Cliente', 'Cliente', 'Fecha_Contabilizacion', 'Status_Documento', 
            'Empleado_Ventas', 'Propietario_Doc', 'Sede_Codigo', 'Numero_Articulo', 
            'Descripcion', 'Cantidad', 'Precio', 'Precio_Sin_IVA', 'Precio_Total', 
            'Almacen', 'Nombre_Usuario', 'Colaborador', 'Rango_Dias'
        ]
        mapeo = {c.lower().strip(): c for c in columnas_esperadas}
        df.columns = [mapeo.get(c.lower().strip(), c) for c in df.columns]
        df['Fecha_Contabilizacion'] = pd.to_datetime(df['Fecha_Contabilizacion'], errors='coerce')
        df['Dias'] = (pd.Timestamp.now().normalize() - df['Fecha_Contabilizacion']).dt.days
        df['Fecha_Texto'] = df['Fecha_Contabilizacion'].dt.strftime('%d/%m/%Y')
        return df
    except Exception as e:
        st.error(f"Error analizando notas crédito en Postgres: {e}")
        return pd.DataFrame()