import streamlit as st
import pandas as pd
import numpy as np
import pyodbc
from sqlalchemy import create_engine, text

# ============================================================
# CONEXIÓN CENTRALIZADA POSTGRESQL (usando secretos seguros)
# ============================================================
CADENA_CONEXION_PG = st.secrets["postgres"]["url"]
conn = st.connection("postgresql", type="sql", url=CADENA_CONEXION_PG)

# ============================================================
# FUNCIÓN PRINCIPAL DE SINCRONIZACIÓN ELT DESDE SAP
# ============================================================
def ejecutar_sincronizacion_desde_sap():
    """Extrae datos de SAP HANA y los almacena en PostgreSQL"""
    try:
        sap_conf = st.secrets["sap"]
        conn_str_sap = (
            f"DRIVER={sap_conf['driver']};"
            f"SERVERNODE={sap_conf['server']};"
            f"UID={sap_conf['user']};"
            f"PWD={sap_conf['password']}"
        )
        conexion_sap = pyodbc.connect(conn_str_sap)

        # ============================================================
        # 1. QUERIES DE EXTRACCIÓN SAP (módulos existentes)
        # ============================================================
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
                T2."WhsName" as "Sede_Codigo",
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

        # ============================================================
        # 🆕 NUEVO QUERY: VENTAS NETAS (Facturas - Notas Crédito)
        # ============================================================
        query_ventas_netas = """
            SELECT
                T0."DocDate" AS "fecha_contabilizacion",
                T0."DocNum" AS "documento",
                T0."CardCode" AS "codigo_cliente",
                T0."CardName" AS "nombre_cliente",
                T1."LineTotal" AS "precio_sin_iva",
                (T1."LineTotal" + T1."VatSum") AS "precio_con_iva",
                (T1."LineTotal" - T1."GrssProfit") AS "costo_total",
                T1."GrssProfit" AS "rentabilidad",
                COALESCE(T2."U_NAME", 'Sin Usuario') AS "nombre_usuario",
                T0."SlpCode" AS "codigo_vendedor",
                COALESCE(T4."SlpName", 'Sin Vendedor') AS "nombre_vendedor",
                COALESCE(T3."SeriesName", '') AS "nombre_serie",
                COALESCE(T3."BeginStr", '') AS "prefijo_serie",
                T1."WhsCode" AS "codigo_almacen",
                CASE 
                    WHEN T3."SeriesName" IN ('128E', 'NCNT128.', 'ND-NT128', 'RC-NT128', 'NPNT128', '128J', '128F') THEN 'ALM128'
                    WHEN T3."SeriesName" IN ('134E', '134F', 'NCAU134', 'NDFAU134', 'NPAU134', 'RC-AU134', '134J') THEN 'ALM134'
                    WHEN T3."SeriesName" IN ('170E', 'NDFP170', 'NCPU170.', 'NPPU170', 'RC-PU170') THEN 'ALM170'
                    WHEN T3."SeriesName" IN ('7AGE', 'NC7AGOS.', 'NP7AGOS', 'RC-7AGOS', 'NDF7AGOS', '7AGJ', '7AGF') THEN '7AGOS'
                    WHEN T3."SeriesName" IN ('A19E', 'NCAVE19.', 'RC-AVE19', 'A19J', 'NDFAV19', 'NPAVE19') THEN 'AV19'
                    WHEN T3."SeriesName" IN ('ARME', 'ARMJ', 'NCARMEN.', 'NDFARMEN', 'NPARMEN', 'RC-ARMEN', 'ND-ARMEN', 'ARMF') THEN 'ARME'
                    WHEN T3."SeriesName" IN ('CHIE', 'CHIF', 'CHIJ', 'NCCHIA.', 'ND-CHIA', 'NPCHIA', 'RC-CHIA', 'NDFCHIA') THEN 'CHIA' 
                    WHEN T3."SeriesName" IN ('COME', 'NCCOMER.', 'ND-COMER', 'NDFCOMER', 'NPCOMER', 'RC-COMER', 'COMF') THEN 'EJECOM' 
                    WHEN T3."SeriesName" IN ('CT1E', 'NCCENT1.', 'ND-CENT1', 'NDFCENT1', 'RC-CENT1', 'CT1J', 'NPCENT1', 'CT1F') THEN 'Q1' 
                    WHEN T3."SeriesName" IN ('CT3E', 'NCCENT3.', 'NPCENT3', 'RC-CENT3', 'CT3J') THEN 'Q3' 
                    WHEN T3."SeriesName" IN ('CT5E', 'NCCENT5.', 'NPCENT5', 'RC-CENT5', 'NDFCENT5', 'CT5J') THEN 'Q5' 
                    WHEN T3."SeriesName" IN ('CT6E', 'CT6J', 'NCCENT6.', 'NPCENT6', 'RC-CENT6', 'NDFCENT6') THEN 'Q6' 
                    WHEN T3."SeriesName" IN ('VILE', 'VILF', 'VILJ', 'NCVILLA.', 'NPVILLA', 'RC-VILLA', 'ND-VILLA') THEN 'VILL' 
                    ELSE COALESCE(T5."WhsName", 'Sin Almacen') 
                END AS "nombre_almacen",
                T0."OwnerCode" AS "propietario_doc",
                COALESCE(T6."Code", '') AS "codigo_colaborador_slp",
                COALESCE(T8."Code", '') AS "codigo_colaborador_owner"
            FROM "NE042025".OINV T0
            INNER JOIN "NE042025".INV1 T1 ON T0."DocEntry" = T1."DocEntry"
            LEFT JOIN "NE042025".OUSR T2 ON T0."UserSign" = T2."USERID"
            LEFT JOIN "NE042025".NNM1 T3 ON T0."Series" = T3."Series"
            LEFT JOIN "NE042025".OSLP T4 ON T0."SlpCode" = T4."SlpCode"
            LEFT JOIN "NE042025".OWHS T5 ON T1."WhsCode" = T5."WhsCode"
            LEFT JOIN "NE042025".OHEM T6 ON T4."SlpCode" = T6."salesPrson"
            LEFT JOIN "NE042025".OUSR T7 ON T0."OwnerCode" = T7."USERID"
            LEFT JOIN "NE042025".OHEM T8 ON T7."INTERNAL_K" = T8."empID"
            WHERE
                T0."CANCELED" = 'N'
                AND T0."DocDate" >= ADD_MONTHS(CURRENT_DATE, -6)
                AND T0."DocDate" <= CURRENT_DATE
                AND (T3."SeriesName" IS NULL 
                     OR T3."SeriesName" NOT IN ('INGE','NDFINGEN','INGF','NCINGEN.','ND-INGEN','NPINGEN','FactClie','RC-INGEN'))
                AND COALESCE(T5."WhsName", '') <> 'BODEGA INGENIERIA'

            UNION ALL

            SELECT
                T0."DocDate" AS "fecha_contabilizacion",
                T0."DocNum" AS "documento",
                T0."CardCode" AS "codigo_cliente",
                T0."CardName" AS "nombre_cliente",
                -T1."LineTotal" AS "precio_sin_iva",
                (T1."LineTotal" + T1."VatSum") AS "precio_con_iva",
                -(T1."LineTotal" - T1."GrssProfit") AS "costo_total",
                -T1."GrssProfit" AS "rentabilidad",
                COALESCE(T2."U_NAME", 'Sin Usuario') AS "nombre_usuario",
                T0."SlpCode" AS "codigo_vendedor",
                COALESCE(T4."SlpName", 'Sin Vendedor') AS "nombre_vendedor",
                COALESCE(T3."SeriesName", '') AS "nombre_serie",
                COALESCE(T3."BeginStr", '') AS "prefijo_serie",
                T1."WhsCode" AS "codigo_almacen",
                CASE 
                    WHEN T3."SeriesName" IN ('128E', 'NCNT128.', 'ND-NT128', 'RC-NT128', 'NPNT128', '128J', '128F') THEN 'ALM128'
                    WHEN T3."SeriesName" IN ('134E', '134F', 'NCAU134', 'NDFAU134', 'NPAU134', 'RC-AU134', '134J') THEN 'ALM134'
                    WHEN T3."SeriesName" IN ('170E', 'NDFP170', 'NCPU170.', 'NPPU170', 'RC-PU170') THEN 'ALM170'
                    WHEN T3."SeriesName" IN ('7AGE', 'NC7AGOS.', 'NP7AGOS', 'RC-7AGOS', 'NDF7AGOS', '7AGJ', '7AGF') THEN '7AGOS'
                    WHEN T3."SeriesName" IN ('A19E', 'NCAVE19.', 'RC-AVE19', 'A19J', 'NDFAV19', 'NPAVE19') THEN 'AV19'
                    WHEN T3."SeriesName" IN ('ARME', 'ARMJ', 'NCARMEN.', 'NDFARMEN', 'NPARMEN', 'RC-ARMEN', 'ND-ARMEN', 'ARMF') THEN 'ARME'
                    WHEN T3."SeriesName" IN ('CHIE', 'CHIF', 'CHIJ', 'NCCHIA.', 'ND-CHIA', 'NPCHIA', 'RC-CHIA', 'NDFCHIA') THEN 'CHIA' 
                    WHEN T3."SeriesName" IN ('COME', 'NCCOMER.', 'ND-COMER', 'NDFCOMER', 'NPCOMER', 'RC-COMER', 'COMF') THEN 'EJECOM' 
                    WHEN T3."SeriesName" IN ('CT1E', 'NCCENT1.', 'ND-CENT1', 'NDFCENT1', 'RC-CENT1', 'CT1J', 'NPCENT1', 'CT1F') THEN 'Q1' 
                    WHEN T3."SeriesName" IN ('CT3E', 'NCCENT3.', 'NPCENT3', 'RC-CENT3', 'CT3J') THEN 'Q3' 
                    WHEN T3."SeriesName" IN ('CT5E', 'NCCENT5.', 'NPCENT5', 'RC-CENT5', 'NDFCENT5', 'CT5J') THEN 'Q5' 
                    WHEN T3."SeriesName" IN ('CT6E', 'CT6J', 'NCCENT6.', 'NPCENT6', 'RC-CENT6', 'NDFCENT6') THEN 'Q6' 
                    WHEN T3."SeriesName" IN ('VILE', 'VILF', 'VILJ', 'NCVILLA.', 'NPVILLA', 'RC-VILLA', 'ND-VILLA') THEN 'VILL' 
                    ELSE COALESCE(T5."WhsName", 'Sin Almacen') 
                END AS "nombre_almacen",
                T0."OwnerCode" AS "propietario_doc",
                COALESCE(T6."Code", '') AS "codigo_colaborador_slp",
                COALESCE(T8."Code", '') AS "codigo_colaborador_owner"
            FROM "NE042025".ORIN T0
            INNER JOIN "NE042025".RIN1 T1 ON T0."DocEntry" = T1."DocEntry"
            LEFT JOIN "NE042025".OUSR T2 ON T0."UserSign" = T2."USERID"
            LEFT JOIN "NE042025".NNM1 T3 ON T0."Series" = T3."Series"
            LEFT JOIN "NE042025".OSLP T4 ON T0."SlpCode" = T4."SlpCode"
            LEFT JOIN "NE042025".OWHS T5 ON T1."WhsCode" = T5."WhsCode"
            LEFT JOIN "NE042025".OHEM T6 ON T4."SlpCode" = T6."salesPrson"
            LEFT JOIN "NE042025".OUSR T7 ON T0."OwnerCode" = T7."USERID"
            LEFT JOIN "NE042025".OHEM T8 ON T7."INTERNAL_K" = T8."empID"
            WHERE
                T0."CANCELED" = 'N'
                AND T0."DocDate" >= ADD_MONTHS(CURRENT_DATE, -6)
                AND T0."DocDate" <= CURRENT_DATE
                AND (T3."SeriesName" IS NULL 
                     OR T3."SeriesName" NOT IN ('INGE','NDFINGEN','INGF','NCINGEN.','ND-INGEN','NPINGEN','FactClie','RC-INGEN'))
                AND COALESCE(T5."WhsName", '') <> 'BODEGA INGENIERIA'
        """

        # ============================================================
        # 2. EJECUTAR QUERIES EN SAP
        # ============================================================
        df_remisiones = pd.read_sql(query_remisiones, conexion_sap)
        df_colaboradores = pd.read_sql(query_colaboradores, conexion_sap)
        df_cotizaciones = pd.read_sql(query_cotizaciones, conexion_sap)
        df_ordenes = pd.read_sql(query_ordenes, conexion_sap)
        df_solicitudes = pd.read_sql(query_solicitudes, conexion_sap)
        df_traslados = pd.read_sql(query_traslados, conexion_sap)
        df_facturas = pd.read_sql(query_facturas, conexion_sap)
        df_notas = pd.read_sql(query_notas, conexion_sap)
        df_ventas_netas = pd.read_sql(query_ventas_netas, conexion_sap)

        conexion_sap.close()

        # ============================================================
        # 3. CONEXIÓN A POSTGRES Y CARGA DE DATOS
        # ============================================================
        pg_engine = create_engine(CADENA_CONEXION_PG)

        sql_vista_rem = """CREATE OR REPLACE VIEW sap_raw.vw_remisiones_con_rangos AS SELECT r.*, sap_raw.calcular_rango_dias(r."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.remisiones r;"""
        sql_vista_cot = """CREATE OR REPLACE VIEW sap_raw.vw_cotizaciones_con_rangos AS SELECT c.*, sap_raw.calcular_rango_dias(c."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.cotizaciones c;"""
        sql_vista_ord = """CREATE OR REPLACE VIEW sap_raw.vw_ordenes_venta_con_rangos AS SELECT o.*, sap_raw.calcular_rango_dias(o."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.ordenes_venta o;"""
        sql_vista_sol = """CREATE OR REPLACE VIEW sap_raw.vw_solicitudes_compras_con_rangos AS SELECT s.*, sap_raw.calcular_rango_dias(s."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.solicitudes_compras s;"""
        sql_vista_tra = """CREATE OR REPLACE VIEW sap_raw.vw_solicitudes_traslados_con_rangos AS SELECT s.*, sap_raw.calcular_rango_dias(s."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.solicitudes_traslados s;"""
        sql_vista_fac = """CREATE OR REPLACE VIEW sap_raw.vw_facturas_reserva_con_rangos AS SELECT f.*, sap_raw.calcular_rango_dias(f."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.facturas_reserva f;"""
        sql_vista_nc = """CREATE OR REPLACE VIEW sap_raw.vw_notas_credito_con_rangos AS SELECT n.*, sap_raw.calcular_rango_dias(n."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.notas_credito n;"""
        sql_vista_ventas = """CREATE OR REPLACE VIEW sap_raw.vw_ventas_netas_con_rangos AS SELECT v.*, sap_raw.calcular_rango_dias(v."fecha_contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.ventas_netas v;"""

        with pg_engine.begin() as pg_conn:
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_remisiones_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_cotizaciones_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_ordenes_venta_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_solicitudes_compras_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_solicitudes_traslados_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_facturas_reserva_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_notas_credito_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_ventas_netas_con_rangos CASCADE;"))

        df_remisiones.to_sql('remisiones', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_colaboradores.to_sql('colaboradores', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_cotizaciones.to_sql('cotizaciones', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_ordenes.to_sql('ordenes_venta', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_solicitudes.to_sql('solicitudes_compras', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_traslados.to_sql('solicitudes_traslados', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_facturas.to_sql('facturas_reserva', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_notas.to_sql('notas_credito', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_ventas_netas.to_sql('ventas_netas', pg_engine, schema='sap_raw', if_exists='replace', index=False)

        with pg_engine.begin() as pg_conn:
            pg_conn.execute(text(sql_vista_rem))
            pg_conn.execute(text(sql_vista_cot))
            pg_conn.execute(text(sql_vista_ord))
            pg_conn.execute(text(sql_vista_sol))
            pg_conn.execute(text(sql_vista_tra))
            pg_conn.execute(text(sql_vista_fac))
            pg_conn.execute(text(sql_vista_nc))
            pg_conn.execute(text(sql_vista_ventas))

            pg_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ventas_fecha ON sap_raw.ventas_netas (fecha_contabilizacion)"))
            pg_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ventas_almacen ON sap_raw.ventas_netas (codigo_almacen)"))
            pg_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ventas_vendedor ON sap_raw.ventas_netas (codigo_vendedor)"))

        return True
    except Exception as e:
        st.error(f"❌ Error en sincronización: {e}")
        return False


# ============================================================
# FUNCIONES DE CARGA Y TRANSFORMACIÓN (módulos existentes)
# ============================================================
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

# ============================================================
#  FUNCIÓN: CARGA Y TRANSFORMACIÓN DE VENTAS NETAS
# ============================================================
@st.cache_data(ttl=600)
def cargar_y_transformar_ventas():
    """Lee ventas netas desde PostgreSQL"""
    try:
        df = conn.query("SELECT * FROM sap_raw.ventas_netas")
        if df.empty:
            return pd.DataFrame()

        df["fecha_contabilizacion"] = pd.to_datetime(df["fecha_contabilizacion"], errors="coerce")
        for col in ["precio_sin_iva", "costo_total", "rentabilidad"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        
        # ✅ Asignación directa (el SQL ya trae códigos cortos)
        df["Almacen_Corto"] = df["nombre_almacen"]
        df["Mes_Texto"] = df["fecha_contabilizacion"].dt.strftime("%b %Y")
        df["Dias"] = (pd.Timestamp.now().normalize() - df["fecha_contabilizacion"]).dt.days
        return df
    except Exception as e:
        st.error(f"Error analizando ventas netas en Postgres: {e}")
        return pd.DataFrame()


# ============================================================
# ✅ FUNCIÓN ORIGINAL: RLS GENERAL (Para Remisiones, Cotizaciones, etc.)
# ============================================================
def aplicar_seguridad_rls(df):
    import streamlit as st
    if df is None or df.empty: 
        return df
    rol = st.session_state.get('rol_actual', 'comercial')
    df_filtrado = df.copy()
    
    if 'Propietario_Doc' in df_filtrado.columns: 
        df_filtrado['Propietario_Doc'] = df_filtrado['Propietario_Doc'].astype(str).str.strip()
    if 'Empleado_Ventas' in df_filtrado.columns: 
        df_filtrado['Empleado_Ventas'] = df_filtrado['Empleado_Ventas'].astype(str).str.strip()
    if 'Sede_Codigo' in df_filtrado.columns: 
        df_filtrado['Sede_Codigo'] = df_filtrado['Sede_Codigo'].astype(str).str.strip()
        
    if rol in ["admin", "gerente", "gerente_comercial"]: 
        return df_filtrado
    elif rol == "admin_punto":
        sap_branch = st.session_state.get('sap_branch_code')
        if sap_branch and str(sap_branch).strip().lower() not in ['none', '']:
            sap_branch = str(sap_branch).strip().upper()
            if 'Sede_Codigo' in df_filtrado.columns:
                return df_filtrado[df_filtrado['Sede_Codigo'].str.upper() == sap_branch]
        return df_filtrado.iloc[0:0]
    elif rol == "comercial":
        sap_owner = str(st.session_state.get('sap_owner_code', '')).strip()
        sap_slp = str(st.session_state.get('sap_slp_code', '')).strip()
        condicion_owner = df_filtrado['Propietario_Doc'] == sap_owner if sap_owner else False
        condicion_slp = df_filtrado['Empleado_Ventas'] == sap_slp if sap_slp else False
        return df_filtrado[condicion_owner | condicion_slp]
    return df_filtrado


# ============================================================
# 🆕 FUNCIÓN: RLS ESPECÍFICO PARA VENTAS (Metas) - CORREGIDA FINAL
# ============================================================
def aplicar_seguridad_rls_ventas(df: pd.DataFrame) -> pd.DataFrame:
    """RLS específico para el módulo de ventas con metas."""
    if df is None or df.empty:
        return df
    
    rol = st.session_state.get("rol_actual", "comercial")
    df_filtrado = df.copy()

    # ✅ SOLUCIÓN DEFINITIVA: Limpieza robusta de códigos (maneja 466, 466.0, '466.0', etc.)
    def clean_code(val):
        if pd.isna(val):
            return ''
        try:
            # Forzar a float primero para manejar strings como '466.0' o números
            num = float(val)
            if num.is_integer():
                return str(int(num))  # Convierte 466.0 a '466'
            return str(num)
        except (ValueError, TypeError):
            return str(val).strip()

    # Aplicar limpieza a las columnas clave
    if 'propietario_doc' in df_filtrado.columns:
        df_filtrado['propietario_doc'] = df_filtrado['propietario_doc'].apply(clean_code)
    if 'codigo_vendedor' in df_filtrado.columns:
        df_filtrado['codigo_vendedor'] = df_filtrado['codigo_vendedor'].apply(clean_code)
    if 'Almacen_Corto' in df_filtrado.columns:
        df_filtrado['Almacen_Corto'] = df_filtrado['Almacen_Corto'].astype(str).str.strip().str.upper()

    # Roles gerenciales ven todo
    if rol in ["admin", "gerente", "gerente_comercial"]:
        return df_filtrado

    # ==========================================
    # ROL: admin_punto (FILTRADO POR ALMACÉN)
    # ==========================================
    if rol == "admin_punto":
        sap_branch = st.session_state.get('sap_branch_code')
        if sap_branch is None or str(sap_branch).strip().lower() in ['none', '', 'nan']:
            return df_filtrado.iloc[0:0]
        
        branch_str = str(sap_branch).strip().upper()
        mapa_inverso = {
            "EJECUTIVOS COMERCIALES": "EJECOM", "PUNTO 134": "ALM134", "7 DE AGOSTO": "7AGOS", 
            "AVENIDA19": "AV19", "CENTRO 1": "Q1", "CENTRO 3": "Q3", "CENTRO 5": "Q5", "CENTRO 6": "Q6",
            "PUNTO170": "ALM170", "NORTE128": "ALM128", "VILLAVICENCIO": "VILL", "ARMENIA": "ARME", "CHIA": "CHIA"
        }
        codigo_corto = mapa_inverso.get(branch_str, branch_str)
        
        if 'Almacen_Corto' in df_filtrado.columns:
            return df_filtrado[df_filtrado['Almacen_Corto'] == codigo_corto].copy()
        return df_filtrado.iloc[0:0]

    # ==========================================
    # ROL: comercial (FILTRADO ESTRICTO POR PROPIETARIO_DOC, IGUAL QUE POWER BI)
    # ==========================================
    elif rol == "comercial":
        sap_owner_raw = st.session_state.get('sap_owner_code')
        
        if sap_owner_raw is None:
            return df_filtrado.iloc[0:0]
        
        sap_owner_clean = clean_code(sap_owner_raw)
        
        if not sap_owner_clean or sap_owner_clean.lower() in ['none', 'nan', '']:
            return df_filtrado.iloc[0:0]
        
        # ✅ CORRECCIÓN CRÍTICA: Filtrar SOLO por propietario_doc. 
        # Se eliminó por completo el operador OR con codigo_vendedor.
        # Esto evita que aparezcan documentos de otros almacenes (ej. VILL) 
        # solo porque el comercial los creó (OwnerCode), respetando la lógica de Power BI.
        if 'propietario_doc' in df_filtrado.columns:
            df_resultado = df_filtrado[df_filtrado['propietario_doc'] == sap_owner_clean].copy()
        else:
            df_resultado = df_filtrado.iloc[0:0]
        
        return df_resultado

    return df_filtrado