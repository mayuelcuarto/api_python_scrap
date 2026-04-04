from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time
import re
import uvicorn

app = FastAPI(title="Soccer Scraper API")

# Configuración de CORS para permitir peticiones desde Angular (habitualmente puerto 4200)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción, usa ["http://localhost:4200"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_match_stats(url: str):
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    
    try:
        driver.get(url)
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        # Intentar hacer clic en la pestaña "Estadísticas"
        try:
            # Buscamos de forma más flexible y esperamos a que sea clickable
            xpath_stats = "//div[contains(., 'Estadísticas')] | //span[contains(., 'Estadísticas')] | //a[contains(., 'Estadísticas')]"
            boton_stats = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_stats)))
            driver.execute_script("arguments[0].click();", boton_stats)
            # En lugar de sleep fijo, esperamos a que aparezca un elemento clave de las estadísticas
            wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(., 'Grandes chances')] | //div[contains(., 'Faltas')]")))
        except Exception:
            pass # Si falla el clic, intentamos extraer lo que sea visible
            print("No se pudo hacer clic en la pestaña de estadísticas o no cargó a tiempo.")

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        results = {}

        # --- Extracción del Marcador (Score) ---
        try:
            # Buscamos el nodo de texto que contiene únicamente el guion "-" separador
            dash_node = soup.find(string=re.compile(r"^\s*-\s*$"))
            if dash_node:
                # Intentamos obtener los números del contenedor padre
                container = dash_node.parent
                # Usamos un separador para identificar números aislados y evitar capturar minutos como "90'"
                texto_area = container.get_text(separator='|', strip=True)
                
                # Si el padre directo no tiene los marcadores, subimos un nivel (común en estructuras de encabezado)
                numeros = [n.strip() for n in texto_area.split('|') if n.strip().isdigit()]
                if len(numeros) < 2:
                    texto_area = container.parent.get_text(separator='|', strip=True)
                    numeros = [n.strip() for n in texto_area.split('|') if n.strip().isdigit()]

                if len(numeros) >= 2:
                    results["score_equipo1"] = numeros[0]
                    results["score_equipo2"] = numeros[1]
            
            if "score_equipo1" not in results:
                results["score_equipo1"] = "N/A"
                results["score_equipo2"] = "N/A"
        except Exception:
            results["score_equipo1"] = "N/A"
            results["score_equipo2"] = "N/A"

        stats_interes = [
            "Goles esperados",
            "Total Remates",
            "Remates al arco",
            "Grandes chances",
            "Saques de esquina",
            "Salvadas de Portero",
            "Faltas",
            "Faltas recibidas",
            "Tarjetas Amarillas",
            "Tarjetas Rojas"
        ]

        for stat in stats_interes:
            # Búsqueda estricta usando regex
            pattern = re.compile(rf"^\s*{re.escape(stat)}\s*$", re.IGNORECASE)
            stat_node = soup.find(string=pattern)
            
            # Generamos la clave base en formato snake_case (ej: "total_remates")
            key_base = stat.lower().replace(" ", "_")

            if stat_node:
                fila_stat = stat_node.find_parent()
                # Obtenemos los textos individuales (valor local, nombre stat, valor visitante)
                # Usamos un separador interno para dividir la fila de forma segura
                partes = [p.strip() for p in fila_stat.get_text(separator='|', strip=True).split('|')]
                
                if len(partes) >= 3:
                    results[f"{key_base}_equipo1"] = partes[0]
                    results[f"{key_base}_equipo2"] = partes[-1]
                else:
                    # Si no hay 3 partes, buscamos un patrón numérico tipo "10 - 5" en el texto unido
                    texto_unido = " ".join(partes)
                    match_stats = re.search(r"(\d+)\s*[-–]\s*(\d+)", texto_unido)
                    if match_stats:
                        results[f"{key_base}_equipo1"] = match_stats.group(1)
                        results[f"{key_base}_equipo2"] = match_stats.group(2)
                    else:
                        results[key_base] = " | ".join(partes)
            else:
                results[f"{key_base}_equipo1"] = "0"
                results[f"{key_base}_equipo2"] = "0"

        return results

    finally:
        driver.quit()

@app.get("/api/stats")
def stats_endpoint(url: str = Query(..., description="URL de Scores")):
    """
    Recibe la URL de un partido y devuelve un JSON con las estadísticas.
    """
    try:
        data = get_match_stats(url)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health():
    return {"status": "alive"}

if __name__ == "__main__":
    # Ejecución del servidor en el puerto 5000
    uvicorn.run(app, host="127.0.0.1", port=5000)