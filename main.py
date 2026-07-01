import os
import cv2
import numpy as np
import base64
from fastapi import FastAPI, UploadFile, File, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from foto_session import router_fotos
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from deepface import DeepFace
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, PointStruct
import uuid

app = FastAPI(title="API de Reconocimiento Facial Asistencial")

# CORS: Permitir conexiones desde la app web React
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Router de sesión de fotos vía QR
app.include_router(router_fotos)

QDRANT_HOST = "localhost"
QDRANT_PORT = 6333
COLLECTION_NAME = "reniec_biometria"

# Servir la carpeta de fotos estáticamente para la app móvil
app.mount("/static", StaticFiles(directory="bd_rostros"), name="static")

# Conectar a Qdrant
try:
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
except Exception as e:
    print(f"[X] Error conectando a Qdrant: {e}")

# --- NUEVO: CALENTAMIENTO DE INTELIGENCIA ARTIFICIAL (WARM-UP) ---
@app.on_event("startup")
async def cargar_modelos_ram():
    """
    Este bloque se ejecuta solo una vez cuando prendes uvicorn.
    Carga los modelos pesados a la memoria RAM antes de que llegue cualquier foto,
    evitando que el primer paciente tenga que esperar 5 segundos extra.
    """
    print("\n[INFO] Calentando motores de IA en la memoria RAM...")
    try:
        # Obligamos a DeepFace a descargar/cargar el modelo de antemano
        DeepFace.build_model("ArcFace")
        DeepFace.build_model("RetinaFace")
        print("[INFO] ¡Modelos listos en RAM! La API está a su máxima velocidad.\n")
    except Exception as e:
        mensaje_error = str(e).encode('ascii', 'ignore').decode('ascii')
        print(f"[!] Aviso de calentamiento: {mensaje_error}")
# -----------------------------------------------------------------

@app.post("/reconocer")
async def reconocer_rostro(request: Request, foto: UploadFile = File(...)):
    """
    Recibe una imagen (.jpg, .jpeg, .png) enviada desde la app móvil.
    La procesa en memoria, busca coincidencias en Qdrant y devuelve un JSON.
    """
    # 1. Validar el formato
    extension = os.path.splitext(foto.filename)[1].lower()
    if extension not in [".jpg", ".jpeg", ".png"]:
        return JSONResponse(status_code=400, content={"estado": "error", "mensaje": "Solo se aceptan imágenes JPG, JPEG o PNG."})

    # 2. Leer la imagen en memoria usando OpenCV
    contents = await foto.read()
    nparr = np.frombuffer(contents, np.uint8)
    img_cv = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if img_cv is None:
        return JSONResponse(status_code=400, content={"estado": "error", "mensaje": "Imagen corrupta o no legible."})

    # 3. Extraer el vector usando DeepFace (Directamente desde la memoria RAM)
    try:
        resultados = DeepFace.represent(
            img_path=img_cv,
            model_name="ArcFace",
            detector_backend="retinaface",
            enforce_detection=True
        )
    except ValueError as e:
        return JSONResponse(status_code=400, content={"estado": "error", "mensaje": "No se detectó un rostro claro en la imagen."})
    except Exception as e:
         return JSONResponse(status_code=500, content={"estado": "error", "mensaje": f"Error interno: {str(e)}"})

    if len(resultados) == 0:
        return JSONResponse(status_code=400, content={"estado": "error", "mensaje": "No se encontraron rostros."})

    vector_test = resultados[0]["embedding"]

    # 4. Normalizar L2 para la distancia Euclidiana en Qdrant
    vector_np = np.array(vector_test)
    norm = np.linalg.norm(vector_np)
    if norm > 0:
        vector_test = (vector_np / norm).tolist()

    # 5. Buscar en Qdrant
    try:
        resultados_busqueda = client.query_points(
            collection_name=COLLECTION_NAME,
            query=vector_test,
            limit=5
        ).points
    except Exception as e:
        return JSONResponse(status_code=500, content={"estado": "error", "mensaje": f"Error consultando la base de datos: {str(e)}"})

    # 6. Formatear la respuesta JSON
    respuesta_json = {
        "estado": "exito",
        "resultados": []
    }

    if resultados_busqueda:
        for hit in resultados_busqueda:
            distancia = hit.score
            
            # Lógica estricta de ArcFace (L2 Euclidiano normalizado)
            if distancia < 0.90:
                certeza = "🟢 ALTA"
                nota = "Identidad confirmada. Alta coincidencia de rasgos óseos."
            elif 0.90 <= distancia < 1.05:
                certeza = "🟡 MODERADA"
                nota = "Similitud estructural fuerte. Requiere validación visual."
            elif 1.05 <= distancia <= 1.13:
                certeza = "🟠 BAJA"
                nota = "Certeza baja. Posible falso positivo por rasgos comunes."
            else:
                # Ignorar distancias mayores a 1.13
                continue

            archivo_original = hit.payload.get("archivo_original", "N/A")
            url_foto = f"{request.base_url}static/{archivo_original}" if archivo_original != "N/A" else None

            respuesta_json["resultados"].append({
                "identidad": hit.payload.get("identidad", "Desconocido"),
                "archivo_original": archivo_original,
                "url_foto": url_foto,
                "certeza": certeza,
                "distancia_l2": round(distancia, 4),
                "nota": nota
            })

    if not respuesta_json["resultados"]:
         return JSONResponse(status_code=200, content={"estado": "exito", "mensaje": "No se encontraron coincidencias cercanas.", "resultados": []})

    return JSONResponse(content=respuesta_json)

# --- NUEVO ENDPOINT: INDEXAR ROSTROS ---
@app.post("/indexar")
async def indexar_rostro(dni: str = Form(...), foto: UploadFile = File(...)):
    """
    Recibe una imagen y un DNI. Extrae el vector facial y lo sube a Qdrant si no existe.
    Guarda la foto físicamente como {dni}.jpg
    """
    # 1. Validar el formato de la foto
    extension = os.path.splitext(foto.filename)[1].lower()
    if extension not in [".jpg", ".jpeg", ".png"]:
        return JSONResponse(status_code=400, content={"estado": "error", "mensaje": "Solo imágenes JPG, JPEG o PNG."})

    # 2. Verificar si el DNI ya existe en Qdrant
    try:
        registros, _ = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="identidad", match=MatchValue(value=dni))
                ]
            ),
            limit=1
        )
        if len(registros) > 0:
            return JSONResponse(status_code=400, content={"estado": "error", "mensaje": f"El DNI {dni} ya se encuentra registrado en el sistema biométrico."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"estado": "error", "mensaje": f"Error verificando base de datos: {e}"})

    # 3. Guardar la imagen físicamente con el nombre del DNI
    directorio_rostros = "bd_rostros"
    if not os.path.exists(directorio_rostros):
        os.makedirs(directorio_rostros)
    
    nombre_archivo = f"{dni}{extension}"
    ruta_guardado = os.path.join(directorio_rostros, nombre_archivo)
    
    # Leer en memoria y guardar en disco a la vez para OpenCV
    contents = await foto.read()
    with open(ruta_guardado, "wb") as f:
        f.write(contents)

    # 4. Decodificar la imagen guardada para DeepFace
    nparr = np.frombuffer(contents, np.uint8)
    img_cv = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if img_cv is None:
        os.remove(ruta_guardado)
        return JSONResponse(status_code=400, content={"estado": "error", "mensaje": "Imagen corrupta o ilegible."})

    # 5. Extraer Biometría (ArcFace)
    try:
        resultados = DeepFace.represent(
            img_path=img_cv,
            model_name="ArcFace",
            detector_backend="retinaface",
            enforce_detection=True
        )
    except ValueError:
        os.remove(ruta_guardado)
        return JSONResponse(status_code=400, content={"estado": "error", "mensaje": "No se detectó un rostro claro en la foto."})
    except Exception as e:
        os.remove(ruta_guardado)
        return JSONResponse(status_code=500, content={"estado": "error", "mensaje": f"Error interno IA: {e}"})

    if len(resultados) == 0:
        os.remove(ruta_guardado)
        return JSONResponse(status_code=400, content={"estado": "error", "mensaje": "No se encontraron rostros."})

    vector_test = resultados[0]["embedding"]

    # 6. Normalización L2
    vector_np = np.array(vector_test)
    norm = np.linalg.norm(vector_np)
    if norm > 0:
        vector_test = (vector_np / norm).tolist()

    # 7. Subir a Qdrant
    nuevo_id = str(uuid.uuid4())
    punto = PointStruct(
        id=nuevo_id,
        vector=vector_test,
        payload={
            "archivo_original": nombre_archivo,
            "identidad": dni
        }
    )

    try:
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=[punto]
        )
    except Exception as e:
        os.remove(ruta_guardado)
        return JSONResponse(status_code=500, content={"estado": "error", "mensaje": f"Error subiendo a Qdrant: {e}"})

    return JSONResponse(content={"estado": "exito", "mensaje": f"Paciente con DNI {dni} indexado correctamente."})

class IndexarBase64Request(BaseModel):
    dni: str
    foto_b64: str

@app.post("/indexar_base64")
async def indexar_rostro_base64(req: IndexarBase64Request):
    """
    Indexa un rostro a partir de una cadena Base64.
    Si el DNI ya existe, sobrescribe el archivo y actualiza el vector (Upsert).
    """
    dni = req.dni
    foto_b64 = req.foto_b64
    
    # 1. Decodificar Base64
    try:
        if "," in foto_b64:
            foto_b64 = foto_b64.split(",")[1]
        img_data = base64.b64decode(foto_b64)
    except Exception as e:
        return JSONResponse(status_code=400, content={"estado": "error", "mensaje": f"Error decodificando base64: {e}"})

    # 2. Guardar físicamente
    directorio_rostros = "bd_rostros"
    if not os.path.exists(directorio_rostros):
        os.makedirs(directorio_rostros)
    
    nombre_archivo = f"{dni}.jpg"
    ruta_guardado = os.path.join(directorio_rostros, nombre_archivo)
    
    with open(ruta_guardado, "wb") as f:
        f.write(img_data)

    # 3. Decodificar para DeepFace
    nparr = np.frombuffer(img_data, np.uint8)
    img_cv = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if img_cv is None:
        os.remove(ruta_guardado)
        return JSONResponse(status_code=400, content={"estado": "error", "mensaje": "Imagen corrupta o ilegible."})

    # 4. Extraer Biometría
    try:
        resultados = DeepFace.represent(
            img_path=img_cv,
            model_name="ArcFace",
            detector_backend="retinaface",
            enforce_detection=True
        )
    except ValueError:
        os.remove(ruta_guardado)
        return JSONResponse(status_code=400, content={"estado": "error", "mensaje": "No se detectó un rostro claro."})
    except Exception as e:
        os.remove(ruta_guardado)
        return JSONResponse(status_code=500, content={"estado": "error", "mensaje": f"Error interno IA: {e}"})

    if len(resultados) == 0:
        os.remove(ruta_guardado)
        return JSONResponse(status_code=400, content={"estado": "error", "mensaje": "No se encontraron rostros."})

    vector_test = resultados[0]["embedding"]
    vector_np = np.array(vector_test)
    norm = np.linalg.norm(vector_np)
    if norm > 0:
        vector_test = (vector_np / norm).tolist()

    # 5. Upsert en Qdrant (buscar si existe DNI para mantener su ID o generar uno nuevo)
    try:
        registros, _ = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=[FieldCondition(key="identidad", match=MatchValue(value=dni))]),
            limit=1
        )
        if len(registros) > 0:
            nuevo_id = registros[0].id
        else:
            nuevo_id = str(uuid.uuid4())
            
        punto = PointStruct(
            id=nuevo_id,
            vector=vector_test,
            payload={"archivo_original": nombre_archivo, "identidad": dni}
        )
        
        client.upsert(collection_name=COLLECTION_NAME, points=[punto])
    except Exception as e:
        os.remove(ruta_guardado)
        return JSONResponse(status_code=500, content={"estado": "error", "mensaje": f"Error en Qdrant: {e}"})

    return JSONResponse(content={"estado": "exito", "mensaje": f"DNI {dni} indexado vía Base64 correctamente."})

# --- NUEVO: PROXIES SEGUROS PARA APIS DE RENIEC (EVITA CORS Y EXPONER LLAVES) ---
import urllib.request
import json

def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()

# Cargar variables de entorno locales
load_env()

class DniQuery(BaseModel):
    dni: str

@app.post("/proxy/dni")
async def proxy_dni(query: DniQuery):
    """
    Proxy seguro para consultar DNI mediante json.pe (API RENIEC básica).
    Evita CORS en el navegador y protege el RENIEC_API_KEY.
    """
    token = os.environ.get("RENIEC_API_KEY")
    if not token:
        return JSONResponse(status_code=500, content={"success": False, "message": "RENIEC_API_KEY no configurado en el servidor."})

    url = "https://api.json.pe/api/dni"
    data = {"dni": query.dni}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        req_data = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data
    except urllib.error.HTTPError as e:
        try:
            err_data = json.loads(e.read().decode("utf-8"))
            return JSONResponse(status_code=e.code, content=err_data)
        except Exception:
            return JSONResponse(status_code=e.code, content={"success": False, "message": f"Error HTTP de RENIEC: {e.reason}"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"Error conectando con la API de RENIEC: {str(e)}"})

@app.post("/proxy/biometria")
async def proxy_biometria(query: DniQuery):
    """
    Proxy seguro para consultar el expediente biométrico mediante la API Avanzada de RENIEC.
    Evita CORS en el navegador y protege el token VITE_ADVANCED_RENIEC_API_TOKEN.
    """
    api_url = os.environ.get("VITE_ADVANCED_RENIEC_API_URL")
    api_token = os.environ.get("VITE_ADVANCED_RENIEC_API_TOKEN")

    if not api_url or not api_token:
        return JSONResponse(status_code=500, content={"success": False, "message": "Configuración de API Avanzada incompleta en el servidor."})

    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "dni": query.dni,
        "source": "database",
        "token": api_token
    }

    try:
        req_data = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(api_url, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data
    except urllib.error.HTTPError as e:
        try:
            err_data = json.loads(e.read().decode("utf-8"))
            return JSONResponse(status_code=e.code, content=err_data)
        except Exception:
            return JSONResponse(status_code=e.code, content={"success": False, "message": f"Error HTTP de API Avanzada: {e.reason}"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"Error conectando con la API Avanzada: {str(e)}"})
