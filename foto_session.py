"""
Módulo de sesión de fotos vía WebSocket para SIRE.
Permite que una PC muestre un QR, el celular tome una foto,
y la foto llegue a la PC en tiempo real a través de WebSocket.

La API solo actúa como relay: no guarda nada en disco.
El almacenamiento permanente ocurre en Firestore (desde la app React).
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

router_fotos = APIRouter(tags=["Sesión de Fotos QR"])

# Sesiones activas en memoria: {session_id: websocket_de_la_pc}
sesiones_foto: dict[str, WebSocket] = {}


# --- WebSocket: La PC se conecta aquí para esperar la foto ---
@router_fotos.websocket("/ws/foto/{session_id}")
async def websocket_foto(websocket: WebSocket, session_id: str):
    """
    La PC abre esta conexión WebSocket al mostrar el QR.
    Se mantiene abierta hasta que recibe la foto o se desconecta.
    """
    await websocket.accept()
    sesiones_foto[session_id] = websocket
    print(f"[QR] 📱 Sesión {session_id[:8]}... conectada. Esperando foto del celular.")
    try:
        # Mantener conexión viva. El cliente puede enviar pings.
        while True:
            data = await websocket.receive_text()
            # Si recibe "ping", responde "pong" para keepalive
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        sesiones_foto.pop(session_id, None)
        print(f"[QR] ❌ Sesión {session_id[:8]}... desconectada.")


# --- Modelo para el body del POST ---
class SubirFotoRequest(BaseModel):
    foto_b64: str


# --- HTTP POST: El celular envía la foto aquí ---
@router_fotos.post("/subir_foto/{session_id}")
async def subir_foto(session_id: str, req: SubirFotoRequest):
    """
    El celular envía la foto en base64.
    La API solo la reenvía por WebSocket a la PC. No guarda nada en disco.
    """
    foto_b64 = req.foto_b64

    if not foto_b64:
        return JSONResponse(
            status_code=400,
            content={"estado": "error", "mensaje": "No se recibió ninguna foto."}
        )

    # Buscar el WebSocket de la PC para esta sesión
    ws = sesiones_foto.get(session_id)
    if not ws:
        return JSONResponse(
            status_code=404,
            content={
                "estado": "error",
                "mensaje": "Sesión no encontrada o expirada. Pide un nuevo código QR."
            }
        )

    # Reenviar la foto a la PC por WebSocket
    try:
        await ws.send_json({
            "tipo": "foto_recibida",
            "foto_b64": foto_b64
        })
    except Exception as e:
        sesiones_foto.pop(session_id, None)
        return JSONResponse(
            status_code=500,
            content={"estado": "error", "mensaje": f"Error enviando a la PC: {e}"}
        )

    # Limpiar la sesión (ya no se necesita)
    sesiones_foto.pop(session_id, None)

    print(f"[QR] ✅ Foto reenviada a la PC para sesión {session_id[:8]}...")
    return {"estado": "exito", "mensaje": "¡Foto enviada exitosamente a la PC!"}


# --- Página HTML para el celular (se abre al escanear el QR) ---
HTML_CAPTURA_MOVIL = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>SIRE - Verificación de Rostro</title>
    <!-- Google Fonts & Material Symbols -->
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" rel="stylesheet">
    
    <!-- MediaPipe Face Mesh -->
    <script src="https://cdn.jsdelivr.net/npm/@mediapipe/camera_utils@0.3/camera_utils.js" crossorigin="anonymous"></script>
    <script src="https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh@0.4/face_mesh.js" crossorigin="anonymous"></script>

    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --color-primary: #b34b28;     /* Terracota */
            --color-secondary: #1E40AF;   /* Azul Confianza */
            --color-bg-warm: #FFFBF7;     /* Crema Fondo */
            --color-dark: #0F172A;        /* Obsidiana */
        }
        body {
            font-family: 'Inter', sans-serif;
            background-color: var(--color-bg-warm);
            color: var(--color-dark);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 1.5rem;
            background-image: 
                radial-gradient(circle at 10% 20%, #FFFBF7 0%, #FFF5EB 100%);
        }
        .container {
            width: 100%;
            max-width: 400px;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 1.5rem;
        }
        .logo-section {
            display: flex;
            align-items: center;
            gap: 0.625rem;
            margin-bottom: 0.5rem;
        }
        .logo-collab {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .logo-text {
            font-family: 'Outfit', sans-serif;
            font-size: 1.35rem;
            font-weight: 700;
            color: var(--color-dark);
            letter-spacing: -0.02em;
        }
        .divider {
            color: #cbd5e1;
            font-size: 1.1rem;
            font-weight: 300;
        }
        .hashtag {
            font-family: 'Outfit', sans-serif;
            font-size: 1.1rem;
            font-weight: 700;
            letter-spacing: -0.01em;
        }
        .hashtag-dark {
            color: var(--color-dark);
        }
        .hashtag-primary {
            color: var(--color-primary);
        }
        .card {
            background: rgba(255, 255, 255, 0.85);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid rgba(179, 75, 40, 0.15);
            border-radius: 2.5rem;
            padding: 2rem 1.5rem;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 40px -15px rgba(15, 23, 42, 0.08);
            position: relative;
            overflow: hidden;
        }
        .card h2 {
            font-family: 'Outfit', sans-serif;
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--color-dark);
            margin-bottom: 0.5rem;
        }
        .card p {
            color: #64748b;
            font-size: 0.9rem;
            margin-bottom: 1.5rem;
            line-height: 1.5;
        }
        .camera-wrapper {
            position: relative;
            width: 280px;
            height: 280px;
            border-radius: 50%;
            overflow: hidden;
            margin: 0 auto 1.5rem auto;
            border: 4px solid var(--color-dark);
            background: #000;
            box-shadow: 0 8px 30px rgba(15, 23, 42, 0.15);
            display: flex;
            align-items: center;
            justify-content: center;
            transition: border-color 0.3s ease;
        }
        .camera-wrapper.detected {
            border-color: var(--color-secondary);
            box-shadow: 0 0 35px rgba(30, 64, 175, 0.4);
        }
        .camera-wrapper.verified {
            border-color: #10B981;
            box-shadow: 0 0 35px rgba(16, 185, 129, 0.4);
        }
        video, canvas {
            position: absolute;
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        #webcam {
            transform: scaleX(-1);
        }
        #previewCanvas {
            transform: scaleX(-1);
            display: none;
            z-index: 10;
        }
        .instruction-box {
            background: var(--color-dark);
            color: #ffffff;
            padding: 0.75rem 1.25rem;
            border-radius: 1rem;
            font-size: 0.85rem;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            margin-bottom: 1rem;
            box-shadow: 0 4px 12px rgba(15, 23, 42, 0.1);
            animation: pulse 2.5s infinite ease-in-out;
        }
        @keyframes pulse {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.02); }
        }
        .btn-group {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            margin-top: 1rem;
        }
        .btn {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            padding: 0.875rem 1.5rem;
            border: none;
            border-radius: 1.5rem;
            font-size: 0.95rem;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s ease;
            width: 100%;
            font-family: 'Inter', sans-serif;
        }
        .btn-primary {
            background-color: var(--color-primary);
            color: #ffffff;
            box-shadow: 0 4px 14px rgba(179, 75, 40, 0.35);
        }
        .btn-primary:hover {
            background-color: #9d3f1f;
            transform: translateY(-1px);
        }
        .btn-secondary {
            background-color: #ffffff;
            color: var(--color-dark);
            border: 1px solid rgba(15, 23, 42, 0.15);
        }
        .btn-secondary:hover {
            background-color: #f8fafc;
        }
        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none !important;
            box-shadow: none !important;
        }
        .loader-wrapper {
            position: absolute;
            inset: 0;
            background: var(--color-bg-warm);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 1rem;
            z-index: 100;
            transition: opacity 0.5s ease;
        }
        .loader-wrapper.hidden {
            opacity: 0;
            pointer-events: none;
        }
        .spinner {
            width: 40px;
            height: 40px;
            border: 4px solid rgba(179, 75, 40, 0.15);
            border-top-color: var(--color-primary);
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        /* Estados de Éxito / Error */
        .status-container {
            display: none;
            flex-direction: column;
            align-items: center;
            gap: 1rem;
            animation: fadeIn 0.4s ease;
        }
        .status-container.visible {
            display: flex;
        }
        .status-icon {
            width: 72px;
            height: 72px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 2.25rem;
            color: #ffffff;
            box-shadow: 0 8px 24px rgba(16, 185, 129, 0.25);
        }
        .bg-success {
            background-color: #10B981;
        }
        .bg-error {
            background-color: #EF4444;
            box-shadow: 0 8px 24px rgba(239, 68, 68, 0.25);
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
    </style>
</head>
<body>
    <div class="loader-wrapper" id="pageLoader">
        <div class="spinner"></div>
        <p class="text-sm font-semibold text-slate-600">Iniciando cámara segura...</p>
    </div>

    <div class="container">
        <!-- Logo -->
        <div class="logo-section">
            <svg viewBox="0 0 44 44" fill="none" xmlns="http://www.w3.org/2000/svg" style="width: 28px; height: 28px;">
                <path d="M22 2C12.0589 2 4 10.0589 4 20C4 26.5411 7.498 32.2656 12.7224 35.4379C12.8711 35.5283 13.0645 35.4526 13.1166 35.2858C13.6371 33.6186 14.6367 31.6186 15.6367 30.1186" stroke="#0F172A" stroke-width="3" stroke-linecap="round"/>
                <path d="M22 13C18.134 13 15 16.134 15 20C15 25.5 22 31 22 31C22 31 29 25.5 29 20C29 16.134 25.866 13 22 13Z" fill="#b34b28"/>
                <path d="M19 19.5H25" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round"/>
            </svg>
            <div class="logo-collab">
                <span class="logo-text">SIRE</span>
                <span class="divider">/</span>
                <span class="hashtag">
                    <span class="hashtag-dark">#YoCuido</span><span class="hashtag-primary">MiFamilia</span>
                </span>
            </div>
        </div>

        <div class="card">
            <!-- Capture Area -->
            <div id="captureArea">
                <h2>Verificación de Identidad</h2>
                <p>Alinea tu rostro dentro del círculo. Para validar tu identidad de forma segura, parpadea (pestañea) o gira tu cabeza hacia un costado.</p>

                <div class="camera-wrapper" id="camWrapper">
                    <video id="webcam" autoplay playsinline muted></video>
                    <canvas id="previewCanvas"></canvas>
                </div>

                <div class="instruction-box" id="instructionBox">
                    <span class="material-symbols-outlined text-[18px]">photo_camera</span>
                    <span id="instructionText">Iniciando cámara...</span>
                </div>

                <div class="btn-group">
                    <button id="btnSend" class="btn btn-primary hidden" type="button" onclick="enviarFoto()">
                        <span class="material-symbols-outlined">send</span>
                        Enviar Foto
                    </button>
                    <button id="btnRetry" class="btn btn-secondary hidden" type="button" onclick="reintentar()">
                        <span class="material-symbols-outlined">refresh</span>
                        Tomar otra foto
                    </button>
                </div>
            </div>

            <!-- Success Area -->
            <div id="statusSuccess" class="status-container">
                <div class="status-icon bg-success">
                    <span class="material-symbols-outlined text-[36px]">check</span>
                </div>
                <h2>¡Foto Enviada!</h2>
                <p>La foto ya llegó a la computadora de registro. Puedes cerrar esta pestaña en tu celular de forma segura.</p>
            </div>

            <!-- Error Area -->
            <div id="statusError" class="status-container">
                <div class="status-icon bg-error">
                    <span class="material-symbols-outlined text-[36px]">error</span>
                </div>
                <h2>Error de Conexión</h2>
                <p id="errorMsg">Ocurrió un problema enviando la imagen. Inténtalo de nuevo.</p>
                <button class="btn btn-primary" onclick="reintentar()">
                    <span class="material-symbols-outlined">refresh</span>
                    Reintentar
                </button>
            </div>
        </div>

        <p style="color: #94a3b8; font-size: 0.75rem; text-align: center; font-weight: 500;">
            Validación de Liveness en el Dispositivo · Privacidad Garantizada
        </p>
    </div>

    <script>
        const SESSION_ID = "{SESSION_ID}";
        const video = document.getElementById('webcam');
        const previewCanvas = document.getElementById('previewCanvas');
        const camWrapper = document.getElementById('camWrapper');
        const instructionBox = document.getElementById('instructionBox');
        const instructionText = document.getElementById('instructionText');
        const btnSend = document.getElementById('btnSend');
        const btnRetry = document.getElementById('btnRetry');
        const pageLoader = document.getElementById('pageLoader');
        
        let localStream = null;
        let faceMesh = null;
        let active = false;
        let faceInside = false;
        let fotoBase64 = null;
        let faceMeshLoaded = false;
        let cooldown = false;
        let faceStableTime = null;
        let processFrameInstance = null;

        // Iniciar cámara web
        async function startWebcam() {
            try {
                localStream = await navigator.mediaDevices.getUserMedia({
                    video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 640 } },
                    audio: false
                });
                video.srcObject = localStream;
                await video.play();
                
                pageLoader.classList.add('hidden');
                instructionText.textContent = "Buscando rostro...";
                active = true;
                
                // Cargar e iniciar MediaPipe
                initMediaPipe();
            } catch (err) {
                console.error("Error al acceder a la cámara:", err);
                pageLoader.classList.add('hidden');
                instructionText.textContent = "Error al iniciar cámara";
                alert("Por favor, permite el acceso a la cámara para realizar la validación.");
            }
        }

        // Configuración de MediaPipe Face Mesh
        function initMediaPipe() {
            try {
                faceMesh = new FaceMesh({
                    locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh@0.4/${file}`
                });
                
                faceMesh.setOptions({
                    maxNumFaces: 1,
                    refineLandmarks: true,
                    minDetectionConfidence: 0.5,
                    minTrackingConfidence: 0.5
                });
                
                faceMesh.onResults(onFaceMeshResults);
                
                let processing = false;
                processFrameInstance = async function() {
                    if (active && video.readyState === video.HAVE_ENOUGH_DATA && !processing && !cooldown) {
                        processing = true;
                        try {
                            await faceMesh.send({ image: video });
                        } catch (e) {
                            console.error("Error procesando frame con MediaPipe:", e);
                        }
                        processing = false;
                    }
                    if (active) {
                        requestAnimationFrame(processFrameInstance);
                    }
                };
                
                processFrameInstance();
                faceMeshLoaded = true;
            } catch (err) {
                console.error("No se pudo iniciar MediaPipe:", err);
                instructionText.textContent = "Error al iniciar verificación";
            }
        }

        function distance(p1, p2) {
            return Math.sqrt(Math.pow(p1.x - p2.x, 2) + Math.pow(p1.y - p2.y, 2) + Math.pow(p1.z - p2.z, 2));
        }

        function getEAR(landmarks) {
            // Ojo izquierdo landmarks refinados de FaceMesh
            const p1 = landmarks[159]; // superior
            const p2 = landmarks[145]; // inferior
            const p3 = landmarks[133]; // lateral izquierdo
            const p4 = landmarks[33];  // lateral derecho
            
            if (!p1 || !p2 || !p3 || !p4) return 1.0;
            return distance(p1, p2) / distance(p3, p4);
        }

        // Callback de MediaPipe
        function onFaceMeshResults(results) {
            if (!active || fotoBase64 || cooldown) return;

            if (results.multiFaceLandmarks && results.multiFaceLandmarks.length > 0) {
                const landmarks = results.multiFaceLandmarks[0];
                
                // Verificar que el rostro esté centrado y a distancia adecuada
                // En coordenadas normalizadas de 0 a 1: la nariz (landmark 1) debe estar centrada
                const nose = landmarks[1];
                const faceWidth = distance(landmarks[33], landmarks[263]); // distancia entre bordes externos de ojos
                
                const centered = nose.x > 0.35 && nose.x < 0.65 && nose.y > 0.35 && nose.y < 0.65;
                const goodDistance = faceWidth > 0.18; // tamaño relativo en pantalla

                if (centered && goodDistance) {
                    if (!faceInside) {
                        faceInside = true;
                        faceStableTime = Date.now();
                        camWrapper.classList.add('detected');
                        instructionText.textContent = "¡Rostro alineado! Prepárate...";
                    }
                    
                    // Esperar 1.2 segundos de estabilidad antes de verificar las acciones de liveness
                    const timeStable = Date.now() - faceStableTime;
                    if (timeStable > 1200) {
                        instructionText.textContent = "¡Pestañea (parpadea) o gira tu cabeza!";
                        
                        let actionDetected = false;
                        
                        // 1. Validar parpadeo (EAR)
                        const ear = getEAR(landmarks);
                        if (ear < 0.19) {
                            actionDetected = true;
                        }
                        
                        // 2. Validar rotación de cabeza (girar a un costado)
                        const leftEdge = landmarks[234];
                        const rightEdge = landmarks[454];
                        if (nose && leftEdge && rightEdge) {
                            const distLeft = distance(nose, leftEdge);
                            const distRight = distance(nose, rightEdge);
                            const ratio = distLeft / distRight;
                            if (ratio < 0.42 || ratio > 2.38) {
                                actionDetected = true;
                            }
                        }
                        
                        if (actionDetected) {
                            capturarFoto();
                        }
                    }
                } else {
                    faceInside = false;
                    faceStableTime = null;
                    camWrapper.classList.remove('detected');
                    if (!centered) {
                        instructionText.textContent = "Centra tu rostro";
                    } else {
                        instructionText.textContent = "Acerca tu rostro";
                    }
                }
            } else {
                faceInside = false;
                faceStableTime = null;
                camWrapper.classList.remove('detected');
                instructionText.textContent = "Buscando rostro...";
            }
        }

        function capturarFoto() {
            active = false; // Detener bucle de frames
            camWrapper.classList.remove('detected');
            camWrapper.classList.add('verified');
            
            // Sonido de obturador virtual / Flash visual
            navigator.vibrate && navigator.vibrate([100, 50, 100]);
            
            // Dibujar video en canvas preview
            const ctx = previewCanvas.getContext('2d');
            previewCanvas.width = video.videoWidth;
            previewCanvas.height = video.videoHeight;
            ctx.drawImage(video, 0, 0);
            
            // Guardar base64
            fotoBase64 = previewCanvas.toDataURL('image/jpeg', 0.85);
            
            // Mostrar preview y botones
            previewCanvas.style.display = 'block';
            btnSend.classList.remove('hidden');
            btnRetry.classList.remove('hidden');
            
            instructionText.textContent = "¡Identidad Validada con Éxito!";
        }

        function reintentar() {
            fotoBase64 = null;
            previewCanvas.style.display = 'none';
            camWrapper.classList.remove('verified');
            btnSend.classList.add('hidden');
            btnRetry.classList.add('hidden');
            document.getElementById('captureArea').style.display = 'block';
            document.getElementById('statusSuccess').classList.remove('visible');
            document.getElementById('statusError').classList.remove('visible');
            
            instructionText.textContent = "Buscando rostro...";
            faceInside = false;
            faceStableTime = null;
            cooldown = true;
            active = true;
            
            // 1.5 segundos de cooldown para evitar captura instantánea al presionar
            setTimeout(() => {
                cooldown = false;
            }, 1500);

            // Reiniciar el loop de frames de video
            if (processFrameInstance) {
                processFrameInstance();
            }
        }

        async function enviarFoto() {
            if (!fotoBase64) return;

            btnSend.disabled = true;
            btnSend.innerHTML = '<span class="spinner" style="width:16px;height:16px;border-width:2px;"></span> Enviando...';

            try {
                const resp = await fetch('/subir_foto/' + SESSION_ID, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ foto_b64: fotoBase64 })
                });

                const data = await resp.json();

                if (resp.ok && data.estado === 'exito') {
                    document.getElementById('captureArea').style.display = 'none';
                    document.getElementById('statusSuccess').classList.add('visible');
                    
                    // Apagar cámara web
                    if (localStream) {
                        localStream.getTracks().forEach(track => track.stop());
                    }
                } else {
                    mostrarError(data.mensaje || 'Error desconocido.');
                }
            } catch (err) {
                mostrarError('Fallo al conectar con el servidor. Verifica tu conexión.');
            }
        }

        function mostrarError(msg) {
            document.getElementById('captureArea').style.display = 'none';
            document.getElementById('errorMsg').textContent = msg;
            document.getElementById('statusError').classList.add('visible');
        }

        // Iniciar al cargar
        window.onload = startWebcam;
    </script>
</body>
</html>"""


@router_fotos.get("/captura/{session_id}")
async def pagina_captura(session_id: str):
    """
    Sirve la página de captura de foto para el celular.
    Se accede al escanear el código QR desde la PC.
    """
    html = HTML_CAPTURA_MOVIL.replace("{SESSION_ID}", session_id)
    return HTMLResponse(content=html)
