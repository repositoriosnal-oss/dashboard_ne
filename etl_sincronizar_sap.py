import pyodbc
import pandas as pd
from sqlalchemy import create_engine, text
import toml

def ejecutar_etl():
    print("Iniciando extracción de SAP HANA...")
    
    # Leer secretos locales
    secretos = toml.load(".streamlit/secrets.toml")
    
    sap_str = f"DRIVER={secretos['sap']['driver']};SERVERNODE={secretos['sap']['server']};UID={secretos['sap']['user']};PWD={secretos['sap']['password']}"
    conexion_sap = pyodbc.connect(sap_str)
    
    # (Aquí pegas el mismo QUERY_REMISIONES y QUERY_COLABORADORES que ya teníamos)
    query_remisiones = """
        SELECT T0."DocNum" as "Documento", T0."CardName" as "Cliente", T0."DocDate" as "Fecha_Contabilizacion", 
        T0."DocStatus" as "Status_Documento", T0."SlpCode" as "Empleado_Ventas", T0."OwnerCode" as "Propietario_Doc",
        T0."Branch" as "Sede_Codigo", T1."ItemCode" as "Numero_Articulo", T1."Dscription" as "Descripcion", 
        T1."Quantity" as "Cantidad", T1."Price" as "Precio", T1."LineTotal" AS "Precio_Sin_IVA", 
        T1."LineTotal" + T1."VatSum" AS "Precio_Total", T2."WhsName" as "Almacen", T3."U_NAME" as "Nombre_Usuario",
        T5."ItmsGrpNam" as "Linea", T6."FirmName" AS "Marca"
        FROM "NE042025"."ODLN" T0 
        INNER JOIN "NE042025"."DLN1" T1 ON T0."DocEntry" = T1."DocEntry" 
        INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
        INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID"
        INNER JOIN "NE042025"."OITM" T4 ON T1."ItemCode" = T4."ItemCode"
        INNER JOIN "NE042025"."OITB" T5 ON T4."ItmsGrpCod" = T5."ItmsGrpCod"
        INNER JOIN "NE042025"."OMRC" T6 ON T4."FirmCode" = T6."FirmCode"
        WHERE T0."DocStatus" = 'O' AND T2."WhsName" <> 'BODEGA INGENIERIA'
    """
    df_remisiones = pd.read_sql(query_remisiones, conexion_sap)
    df_colaboradores = pd.read_sql('SELECT T0."lastName" as "Nombre", T0."firstName" as "Apellido", T0."Code" as "Codigo" FROM "NE042025"."OHEM" T0', conexion_sap)
    conexion_sap.close()
    
    print("Guardando en PostgreSQL...")
    pg_engine = create_engine(secretos['postgres']['url'])
    with pg_engine.begin() as pg_conn:
        # esto esun comentario
        pg_conn.execute(text("TRUNCATE TABLE sap_raw.remisiones RESTART IDENTITY CASCADE;"))
        pg_conn.execute(text("TRUNCATE TABLE sap_raw.colaboradores RESTART IDENTITY CASCADE;"))
    
    df_remisiones.to_sql('remisiones', pg_engine, schema='sap_raw', if_exists='append', index=False)
    df_colaboradores.to_sql('colaboradores', pg_engine, schema='sap_raw', if_exists='append', index=False)
    print("¡ETL Finalizado con éxito!")

if __name__ == "__main__":
    ejecutar_etl()