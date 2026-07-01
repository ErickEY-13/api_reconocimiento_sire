import os
from deepface import DeepFace
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance

# 1. Configuración de Rutas
BD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bd_rostros")
QDRANT_HOST = "localhost"
QDRANT_PORT = 6333
COLLECTION_NAME = "reniec_biometria"

print("[INFO] Conectando a Qdrant...")
try:
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
except Exception as e:
    print(f"[❌] Error conectando a Qdrant: {e}\nPor favor verifica que Docker esté corriendo.")
    exit(1)

# 2. Crear la colección si no existe
# ArcFace genera vectores de 512 dimensiones
if not client.collection_exists(COLLECTION_NAME):
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=512, distance=Distance.EUCLID)
    )
    print(f"[INFO] Colección '{COLLECTION_NAME}' creada exitosamente.")
else:
    print(f"[INFO] Colección '{COLLECTION_NAME}' ya existe.")

# 2.5 Obtener archivos ya indexados para no repetir trabajo
archivos_existentes = set()
ultimo_id = 0

if client.collection_exists(COLLECTION_NAME):
    # Scroll rápido para ver qué archivos ya están guardados
    puntos_existentes, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        limit=10000,
        with_payload=True,
        with_vectors=False
    )
    for p in puntos_existentes:
        if "archivo_original" in p.payload:
            archivos_existentes.add(p.payload["archivo_original"])
        if isinstance(p.id, int) and p.id > ultimo_id:
            ultimo_id = p.id

# 3. Leer imágenes y extraer vectores
puntos = []
idx = ultimo_id + 1
extensiones_validas = (".jpg", ".jpeg", ".png")

print(f"[INFO] Escaneando imágenes en '{BD_PATH}'...")
for archivo in os.listdir(BD_PATH):
    if archivo.lower().endswith(extensiones_validas):
        if archivo in archivos_existentes:
            print(f"[SKIP] '{archivo}' ya está indexado. Saltando...")
            continue
            
        ruta_img = os.path.join(BD_PATH, archivo)
        try:
            # represent devuelve una lista de diccionarios, tomamos el primero
            resultados = DeepFace.represent(
                img_path=ruta_img,
                model_name="ArcFace",
                detector_backend="retinaface",
                enforce_detection=False # Para no fallar si alguna foto de la BD es oscura
            )
            
            if len(resultados) > 0:
                vector = resultados[0]["embedding"]
                
                # Normalización L2
                import numpy as np
                vector_np = np.array(vector)
                norm = np.linalg.norm(vector_np)
                if norm > 0:
                    vector = (vector_np / norm).tolist()
                
                # Crear el Payload
                nombre_persona = os.path.splitext(archivo)[0]
                
                punto = PointStruct(
                    id=idx,
                    vector=vector,
                    payload={
                        "archivo_original": archivo,
                        "identidad": nombre_persona
                    }
                )
                puntos.append(punto)
                print(f"[OK] Vector extraído para: {archivo}")
                idx += 1
                
        except Exception as e:
            print(f"[❌] Error procesando {archivo}: {e}")

# 4. Subir todo a Qdrant
if puntos:
    print(f"\n[INFO] Subiendo {len(puntos)} vectores a Qdrant...")
    client.upsert(
        collection_name=COLLECTION_NAME,
        points=puntos
    )
    print("[INFO] ¡Indexación completada exitosamente!")
else:
    print("[⚠️] No se encontraron rostros válidos para indexar.")
